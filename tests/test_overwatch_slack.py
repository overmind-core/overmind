from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from django.core.cache import cache
from factories import make_user
from fakes.llm import tool_call
from rest_framework.test import APIClient

from overbae.services.overwatch_agent import answer_question
from overbae.tasks.overwatch import answer_slack_message, answer_slack_question

USER = "U01234567"
TEAM = "T01234567"
CHANNEL = "D01234567"


@pytest.fixture(autouse=True)
def _sandbox(settings):
    # Analytics never invokes the parser sandbox. The global outbound guard stays active.
    settings.SANDBOX_URL = "http://sandbox.test"
    settings.SANDBOX_TOKEN = "analytics-test"


@pytest.fixture(autouse=True)
def slack_config(settings):
    cache.clear()
    settings.SLACK_SIGNING_SECRET = "overwatch-signing-test"
    settings.SLACK_BOT_TOKEN = "overwatch-bot-test"
    settings.OVERWATCH_SLACK_TEAM_ID = TEAM
    settings.OVERWATCH_SLACK_ALLOWED_USER_IDS = USER
    settings.OVERWATCH_ALLOWED_EMAIL_DOMAIN = "overmindlab.ai"
    settings.OVERWATCH_AGENT_ENABLED = True
    settings.OPENROUTER_API_KEY = "overwatch-model-test"


def send(settings, payload, *, command=False, timestamp=None, signature=None):
    body = urlencode(payload).encode() if command else json.dumps(payload).encode()
    timestamp = timestamp or str(int(time.time()))
    signature = (
        signature
        or "v0="
        + hmac.new(
            settings.SLACK_SIGNING_SECRET.encode(),
            b"v0:" + timestamp.encode() + b":" + body,
            hashlib.sha256,
        ).hexdigest()
    )
    return APIClient().post(
        "/api/internal/overwatch/slack/command/"
        if command
        else "/api/internal/overwatch/slack/events/",
        data=body,
        content_type="application/x-www-form-urlencoded" if command else "application/json",
        HTTP_X_SLACK_REQUEST_TIMESTAMP=timestamp,
        HTTP_X_SLACK_SIGNATURE=signature,
    )


def command(**extra):
    return {
        "team_id": TEAM,
        "command": "/overwatch",
        "user_id": USER,
        "text": "How many MCP calls?",
        "response_url": "https://hooks.slack.com/commands/response",
        **extra,
    }


def event(**extra):
    return {
        "type": "event_callback",
        "team_id": TEAM,
        "event_id": "Ev01234567",
        "event": {
            "type": "message",
            "channel_type": "im",
            "channel": CHANNEL,
            "user": USER,
            "text": "Show product usage",
            "ts": "123.45",
            **extra,
        },
    }


@pytest.mark.parametrize("signature", ["v0=invalid", "v0=" + "☃" * 64])
def test_slack_rejects_invalid_signatures(settings, signature):
    assert send(settings, command(), command=True, signature=signature).status_code == 401


def test_slack_rejects_expired_signed_requests(settings):
    assert send(settings, event(), timestamp=str(int(time.time()) - 301)).status_code == 401


def test_signed_commands_queue_once_with_redacted_task_metadata(settings, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_question.apply_async",
        lambda **kwargs: queued.append(kwargs),
    )
    first = send(settings, command(), command=True)
    second = send(settings, command(), command=True)
    assert first.status_code == second.status_code == 200
    assert len(queued) == 1
    assert queued[0]["args"] == (USER, "How many MCP calls?", command()["response_url"])
    assert queued[0]["argsrepr"] == "(<redacted>)"
    assert queued[0]["expires"] == 120


@pytest.mark.parametrize(
    "url",
    [
        "https://hooks.slack.com.attacker.test/commands/x",
        "http://hooks.slack.com/commands/x",
        "https://user@hooks.slack.com/commands/x",
        "https://hooks.slack.com/other/x",
        "https://hooks.slack.com/commands/x?token=private",
    ],
)
def test_signed_command_cannot_choose_arbitrary_reply_destination(settings, url):
    assert send(settings, command(response_url=url), command=True).status_code == 400


def test_command_enforces_workspace_and_query_rate(settings, monkeypatch):
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_question.apply_async", lambda **kwargs: None
    )
    assert send(settings, command(team_id="T99999999"), command=True).status_code == 403
    for index in range(5):
        assert send(settings, command(text=f"Usage {index}"), command=True).status_code == 200
    assert send(settings, command(text="Usage over limit"), command=True).status_code == 429


def test_dm_event_queues_once_and_ignores_public_messages(settings, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "overbae.api.overwatch_slack.answer_slack_message.apply_async",
        lambda **kwargs: queued.append(kwargs),
    )
    assert send(settings, event()).status_code == 200
    assert send(settings, event()).status_code == 200
    assert len(queued) == 1
    assert queued[0]["args"] == (USER, "Show product usage", CHANNEL, "123.45", "")
    assert queued[0]["argsrepr"] == "(<redacted>)"
    public = event(channel_type="channel", channel="C01234567")
    public["event_id"] = "Ev99999999"
    assert send(settings, public).status_code == 200
    assert len(queued) == 1


def test_event_challenge_requires_valid_signature(settings):
    response = send(settings, {"type": "url_verification", "challenge": "test-challenge"})
    assert response.json() == {"challenge": "test-challenge"}


def slack_profile(**extra):
    return {
        "ok": True,
        "user": {
            "id": USER,
            "team_id": TEAM,
            "profile": {"email": "employee@overmindlab.ai"},
            **extra,
        },
    }


def test_dm_answers_only_approved_internal_member_without_unfurls(scripted, fake_llm):
    slack = (
        scripted("https://slack.com").reply(json_body=slack_profile()).reply(json_body={"ok": True})
    )
    fake_llm.on(lambda request: True, "Usage summary <https://example.com|link> <!channel>")
    answer_slack_message(USER, "Show usage", CHANNEL, "123.45")
    reply = slack.calls[-1].json
    assert reply["channel"] == CHANNEL
    assert reply["thread_ts"] == "123.45"
    assert reply["blocks"][0]["text"]["type"] == "plain_text"
    assert reply["unfurl_links"] is reply["unfurl_media"] is False
    assert "<!channel>" not in reply["text"]
    assert fake_llm.requests[0].body["provider"] == {"data_collection": "deny", "zdr": True}


@pytest.mark.parametrize(
    "fields",
    [
        {"deleted": True},
        {"is_bot": True},
        {"is_restricted": True},
        {"is_ultra_restricted": True},
        {"is_stranger": True},
        {"team_id": "T99999999"},
        {"profile": {"email": "external@example.com"}},
    ],
)
def test_dm_denies_unapproved_profiles(scripted, fake_llm, fields):
    slack = (
        scripted("https://slack.com")
        .reply(json_body=slack_profile(**fields))
        .reply(json_body={"ok": True})
    )
    answer_slack_message(USER, "Show customer balances", CHANNEL, "123.45")
    assert "not approved" in slack.calls[-1].json["text"]
    assert fake_llm.requests == []


def test_blank_allowlist_denies_without_model_or_profile_lookup(settings, scripted, fake_llm):
    settings.OVERWATCH_SLACK_ALLOWED_USER_IDS = ""
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    answer_slack_message(USER, "Show customer balances", CHANNEL, "123.45")
    assert len(slack.calls) == 1
    assert slack.calls[0].path == "/api/chat.postMessage"
    assert fake_llm.requests == []


def test_command_reply_never_echoes_credential_input(scripted, fake_llm):
    scripted("https://slack.com").reply(json_body=slack_profile())
    replies = scripted("https://hooks.slack.com").reply(text="ok")
    question = "password=not-a-real-password how much usage?"
    answer_slack_question(USER, question, command()["response_url"])
    assert "not-a-real-password" not in json.dumps(replies.calls[0].json)
    assert fake_llm.requests == []


@pytest.mark.django_db(transaction=True)
def test_customer_record_stays_out_of_model_requests(fake_llm):
    customer = make_user(email="customer-private@example.com")
    fake_llm.on(
        lambda request: True,
        {
            "tool_calls": [
                tool_call("customer_activity", {"customer_ref": "customer_1", "days": 30})
            ]
        },
    )
    answer = asyncio.run(answer_question(f"Show usage and payments for {customer.email}"))
    assert customer.email in answer
    assert "Lifetime spend" in answer
    assert len(fake_llm.requests) == 1
    outbound = json.dumps(fake_llm.requests[0].body)
    assert customer.email not in outbound
    assert "customer_1" in outbound


@pytest.mark.django_db(transaction=True)
def test_aggregate_tool_queries_run_outside_async_event_loop(fake_llm):
    make_user()
    fake_llm.on(
        lambda r: not any(m["role"] == "tool" for m in r.messages),
        {"tool_calls": [tool_call("activation_funnel", {"days": 30})]},
    )
    fake_llm.on(
        lambda r: any(m["role"] == "tool" for m in r.messages), "The tools returned one signup."
    )
    assert "one signup" in asyncio.run(answer_question("How many users signed up?"))
    result = json.loads(fake_llm.requests[-1].messages[-1]["content"])
    assert result["steps"][0] == {"step": "signed_up", "users": 1}


def test_model_is_disabled_without_explicit_opt_in(settings, fake_llm):
    settings.OVERWATCH_AGENT_ENABLED = False
    assert "not enabled" in asyncio.run(answer_question("How many users?"))
    assert fake_llm.requests == []


def test_untrusted_tool_arguments_are_validated_before_execution(fake_llm):
    fake_llm.on(
        lambda r: not any(m["role"] == "tool" for m in r.messages),
        {
            "tool_calls": [tool_call("billing_summary", {"days": True, "sql": "SELECT private"})],
        },
    )
    fake_llm.on(lambda r: any(m["role"] == "tool" for m in r.messages), "That query was refused.")
    assert "No analytics could be loaded" in asyncio.run(answer_question("Show spend"))
    assert json.loads(fake_llm.requests[-1].messages[-1]["content"]) == {
        "error": "Invalid tool arguments."
    }
