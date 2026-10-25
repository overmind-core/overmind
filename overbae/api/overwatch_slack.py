"""Signed Slack slash-command ingress for the private Overwatch agent."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from urllib.parse import parse_qs

from django.conf import settings
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.services.overwatch_privacy import slack_response_url
from overbae.tasks.overwatch import answer_slack_message, answer_slack_question

MAX_REQUEST_AGE_SECONDS = 300
MAX_BODY_BYTES = 16_384
MAX_QUESTION_LENGTH = 4000


def _valid_signature(body: bytes, timestamp: str, signature: str) -> bool:
    if (
        not settings.SLACK_SIGNING_SECRET
        or len(timestamp) > 12
        or not timestamp.isascii()
        or not timestamp.isdecimal()
        or re.fullmatch(r"v0=[a-f0-9]{64}", signature) is None
    ):
        return False
    if abs(int(time.time()) - int(timestamp)) > MAX_REQUEST_AGE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    digest = hmac.new(
        settings.SLACK_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def _admit(user_id: str) -> bool:
    """Bound expensive model calls for both one user and the whole workspace."""
    minute = int(time.time()) // 60
    for scope, limit in ((user_id, 5), ("workspace", 20)):
        key = f"overwatch:slack:rate:{scope}:{minute}"
        cache.add(key, 0, timeout=120)
        if cache.incr(key) > limit:
            return False
    return True


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(sensitive_post_parameters("text", "response_url"), name="dispatch")
class OverwatchSlackCommandView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    @sensitive_variables()
    def post(self, request):
        body = request.body
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if len(body) > MAX_BODY_BYTES or not _valid_signature(body, timestamp, signature):
            return Response({"text": "Invalid Slack request."}, status=401)

        expected_team = settings.OVERWATCH_SLACK_TEAM_ID
        if not expected_team:
            return Response({"text": "Overwatch Slack is not configured."}, status=503)
        fields = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)

        def field(name: str) -> str:
            return fields.get(name, [""])[0]

        if field("team_id") != expected_team or field("command") != "/overwatch":
            return Response(
                {"text": "This Slack workspace is not enabled for Overwatch."}, status=403
            )

        user_id = field("user_id")
        question = field("text").strip()[:MAX_QUESTION_LENGTH]
        response_url = field("response_url")
        if not re.fullmatch(r"[UW][A-Z0-9]{1,31}", user_id) or not slack_response_url(response_url):
            return Response({"text": "Could not read this Slack command."}, status=400)
        if not question:
            return Response(
                {
                    "response_type": "ephemeral",
                    "text": (
                        "Ask about customer activity, for example: "
                        "`/overwatch where are users dropping off?`"
                    ),
                }
            )

        try:
            cache_key = (
                "overwatch:slack:command:" + hashlib.sha256(timestamp.encode() + body).hexdigest()
            )
            if not cache.add(cache_key, True, timeout=600):
                return Response(
                    {
                        "response_type": "ephemeral",
                        "text": "That question is already being processed.",
                    }
                )
            if not _admit(user_id):
                cache.delete(cache_key)
                return Response(
                    {
                        "response_type": "ephemeral",
                        "text": "Overwatch has reached its query limit. Try again in a minute.",
                    },
                    status=429,
                )
            answer_slack_question.apply_async(
                args=(user_id, question, response_url),
                argsrepr="(<redacted>)",
                kwargsrepr="{}",
                expires=120,
            )
        except Exception:
            if "cache_key" in locals():
                cache.delete(cache_key)
            return Response(
                {
                    "response_type": "ephemeral",
                    "text": "Overwatch is busy. Please try again shortly.",
                },
                status=503,
            )
        return Response({"response_type": "ephemeral", "text": "Overwatch is looking into that…"})


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(sensitive_post_parameters(), name="dispatch")
class OverwatchSlackEventsView(APIView):
    """Queue signed direct-message events; public channel mentions are ignored."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    @sensitive_variables()
    def post(self, request):
        body = request.body
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if len(body) > MAX_BODY_BYTES or not _valid_signature(body, timestamp, signature):
            return Response({"error": "invalid_request"}, status=401)

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return Response({"error": "invalid_json"}, status=400)
        if not isinstance(payload, dict):
            return Response({"error": "invalid_request"}, status=400)
        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge")
            if not isinstance(challenge, str) or len(challenge) > 512:
                return Response({"error": "invalid_challenge"}, status=400)
            return Response({"challenge": challenge})
        if payload.get("type") != "event_callback":
            return Response({"ok": True})

        expected_team = settings.OVERWATCH_SLACK_TEAM_ID
        event_id = payload.get("event_id")
        event = payload.get("event")
        if payload.get("team_id") != expected_team or not expected_team:
            return Response({"error": "workspace_not_enabled"}, status=403)
        if not isinstance(event_id, str) or not event_id or len(event_id) > 255:
            return Response({"error": "invalid_event"}, status=400)
        if not isinstance(event, dict):
            return Response({"ok": True})
        if (
            event.get("type") != "message"
            or event.get("channel_type") != "im"
            or event.get("subtype")
            or event.get("bot_id")
        ):
            return Response({"ok": True})

        user_id = event.get("user")
        channel_id = event.get("channel")
        question = str(event.get("text", "")).strip()[:MAX_QUESTION_LENGTH]
        message_ts = event.get("ts")
        thread_ts = event.get("thread_ts", "")
        if not all(isinstance(value, str) and value for value in (user_id, channel_id, message_ts)):
            return Response({"ok": True})
        if not re.fullmatch(r"[UW][A-Z0-9]{1,31}", user_id) or not re.fullmatch(
            r"D[A-Z0-9]{1,31}", channel_id
        ):
            return Response({"ok": True})
        if not question:
            return Response({"ok": True})

        cache_key = "overwatch:slack:event:" + hashlib.sha256(event_id.encode()).hexdigest()
        try:
            if not cache.add(cache_key, True, timeout=24 * 60 * 60):
                return Response({"ok": True})
            if not _admit(user_id):
                return Response({"ok": True})
            answer_slack_message.apply_async(
                args=(
                    user_id,
                    question,
                    channel_id,
                    message_ts,
                    thread_ts if isinstance(thread_ts, str) else "",
                ),
                argsrepr="(<redacted>)",
                kwargsrepr="{}",
                expires=120,
            )
        except Exception:
            cache.delete(cache_key)
            return Response({"error": "overwatch_busy"}, status=503)
        return Response({"ok": True})
