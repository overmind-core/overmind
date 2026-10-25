"""Signed Slack slash-command ingress for the private Overwatch agent."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

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
    ):
        return False
    if abs(int(time.time()) - int(timestamp)) > MAX_REQUEST_AGE_SECONDS:
        return False
    base = b"v0:" + timestamp.encode("ascii") + b":" + body
    digest = hmac.new(
        settings.SLACK_SIGNING_SECRET.encode("utf-8"), base, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature)


def _response_url_is_slack(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "hooks.slack.com"
            and parsed.port in (None, 443)
        )
    except ValueError:
        return False


@method_decorator(csrf_exempt, name="dispatch")
class OverwatchSlackCommandView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
    def post(self, request):
        body = request.body
        timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
        signature = request.headers.get("X-Slack-Signature", "")
        if len(body) > MAX_BODY_BYTES or not _valid_signature(
            body, timestamp, signature
        ):
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
        if not user_id or not _response_url_is_slack(response_url):
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
            answer_slack_question.delay(user_id, question, response_url)
        except Exception:
            return Response(
                {
                    "response_type": "ephemeral",
                    "text": "Overwatch is busy. Please try again shortly.",
                },
                status=503,
            )
        return Response(
            {"response_type": "ephemeral", "text": "Overwatch is looking into that…"}
        )


@method_decorator(csrf_exempt, name="dispatch")
class OverwatchSlackEventsView(APIView):
    """Queue signed direct-message events; public channel mentions are ignored."""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(exclude=True)
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
        if not question:
            return Response({"ok": True})

        cache_key = "overwatch:slack:event:" + hashlib.sha256(event_id.encode()).hexdigest()
        if not cache.add(cache_key, True, timeout=24 * 60 * 60):
            return Response({"ok": True})
        try:
            answer_slack_message.delay(
                user_id,
                question,
                channel_id,
                message_ts,
                thread_ts if isinstance(thread_ts, str) else "",
            )
        except Exception:
            cache.delete(cache_key)
            return Response({"error": "overwatch_busy"}, status=503)
        return Response({"ok": True})
