from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from django.core.cache import cache
from rest_framework.test import APIClient


def _signed_form(fields: dict[str, str], secret: str) -> tuple[bytes, str]:
    body = urlencode(fields).encode()
    timestamp = str(int(time.time()))
    base = b"v0:" + timestamp.encode() + b":" + body
    signature = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return body, f"{timestamp}:{signature}"


def test_slack_command_rejects_invalid_signature(settings, monkeypatch):
    settings.SLACK_SIGNING_SECRET = "signing-secret"
    settings.OVERWATCH_SLACK_TEAM_ID = "T-overmind"
    queued = []
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_question.delay",
        lambda *args: queued.append(args),
    )

    response = APIClient().post(
        "/api/internal/overwatch/slack/command/",
        data=b"team_id=T-overmind&command=%2Foverwatch&text=usage",
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=str(int(time.time())),
        HTTP_X_SLACK_SIGNATURE="v0=invalid",
    )

    assert response.status_code == 401
    assert queued == []


def test_slack_command_queues_only_signed_team_questions(settings, monkeypatch):
    settings.SLACK_SIGNING_SECRET = "signing-secret"
    settings.OVERWATCH_SLACK_TEAM_ID = "T-overmind"
    fields = {
        "team_id": "T-overmind",
        "command": "/overwatch",
        "user_id": "U-member",
        "text": "how many MCP calls this week?",
        "response_url": "https://hooks.slack.com/commands/response",
    }
    body, auth = _signed_form(fields, settings.SLACK_SIGNING_SECRET)
    timestamp, signature = auth.split(":", maxsplit=1)
    queued = []
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_question.delay",
        lambda *args: queued.append(args),
    )

    response = APIClient().post(
        "/api/internal/overwatch/slack/command/",
        data=body,
        content_type="application/x-www-form-urlencoded",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )

    assert response.status_code == 200
    assert response.json()["response_type"] == "ephemeral"
    assert queued == [
        ("U-member", fields["text"], fields["response_url"]),
    ]


def _signed_json(payload: dict, secret: str) -> tuple[bytes, str, str]:
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    base = b"v0:" + timestamp.encode() + b":" + body
    signature = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    return body, timestamp, signature


def test_slack_dm_event_queues_once_and_ignores_public_messages(settings, monkeypatch):
    settings.SLACK_SIGNING_SECRET = "signing-secret"
    settings.OVERWATCH_SLACK_TEAM_ID = "T-overmind"
    cache.clear()
    queued = []
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_message.delay",
        lambda *args: queued.append(args),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T-overmind",
        "event_id": "Ev-direct-message-1",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": "D-overmind",
            "user": "U-member",
            "text": "How many users sent a trace?",
            "ts": "123.45",
        },
    }
    body, timestamp, signature = _signed_json(payload, settings.SLACK_SIGNING_SECRET)

    def send(raw_body, sent_at, signed):
        return APIClient().post(
            "/api/internal/overwatch/slack/events/",
            data=raw_body,
            content_type="application/json",
            HTTP_X_SLACK_REQUEST_TIMESTAMP=sent_at,
            HTTP_X_SLACK_SIGNATURE=signed,
        )

    first = send(body, timestamp, signature)
    retry = send(body, timestamp, signature)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert first.json() == {"ok": True}
    assert queued == [
        ("U-member", "How many users sent a trace?", "D-overmind", "123.45", ""),
    ]

    public_payload = {
        **payload,
        "event_id": "Ev-public-message-1",
        "event": {**payload["event"], "channel_type": "channel"},
    }
    public_body, public_timestamp, public_signature = _signed_json(
        public_payload, settings.SLACK_SIGNING_SECRET
    )
    ignored = send(public_body, public_timestamp, public_signature)

    assert ignored.status_code == 200
    assert queued == [
        ("U-member", "How many users sent a trace?", "D-overmind", "123.45", ""),
    ]


def test_slack_event_url_verification_requires_a_valid_signature(settings):
    settings.SLACK_SIGNING_SECRET = "signing-secret"
    settings.OVERWATCH_SLACK_TEAM_ID = "T-overmind"
    payload = {"type": "url_verification", "challenge": "challenge-value"}
    body, timestamp, signature = _signed_json(payload, settings.SLACK_SIGNING_SECRET)

    response = APIClient().post(
        "/api/internal/overwatch/slack/events/",
        data=body,
        content_type="application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-value"}


def test_slack_dm_task_checks_email_domain_and_replies_in_the_thread(settings, monkeypatch):
    from overbae.tasks import overwatch

    settings.SLACK_BOT_TOKEN = "bot-token"
    settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN = "overmindlab.ai"
    calls = []

    class SlackResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    def get(*args, **kwargs):
        return SlackResponse(
            {"ok": True, "user": {"profile": {"email": "sam@overmindlab.ai"}}}
        )

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return SlackResponse({"ok": True})

    async def answer_question(question):
        return f"Usage summary for: {question}"

    monkeypatch.setattr(overwatch.requests, "get", get)
    monkeypatch.setattr(overwatch.requests, "post", post)
    monkeypatch.setattr(overwatch, "answer_question", answer_question)

    overwatch.answer_slack_message("U-member", "Show product usage", "D-member", "123.45")

    assert calls == [
        {
            "channel": "D-member",
            "text": "Usage summary for: Show product usage",
            "thread_ts": "123.45",
        }
    ]


def test_slack_dm_task_does_not_query_data_for_another_email_domain(settings, monkeypatch):
    from overbae.tasks import overwatch

    settings.SLACK_BOT_TOKEN = "bot-token"
    settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN = "overmindlab.ai"
    replies = []

    class SlackResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": True, "user": {"profile": {"email": "someone@example.com"}}}

    def post(*args, **kwargs):
        replies.append(kwargs["json"])
        return SlackResponse()

    def unexpected_question(_question):
        raise AssertionError("Unauthorised users must not query analytics.")

    monkeypatch.setattr(overwatch.requests, "get", lambda *args, **kwargs: SlackResponse())
    monkeypatch.setattr(overwatch.requests, "post", post)
    monkeypatch.setattr(overwatch, "answer_question", unexpected_question)

    overwatch.answer_slack_message("U-external", "Show product usage", "D-member", "123.45")

    assert replies == [
        {
            "channel": "D-member",
            "text": "Overwatch is available to overmindlab.ai team members only.",
            "thread_ts": "123.45",
        }
    ]
