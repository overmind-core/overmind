from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from io import StringIO
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import RequestFactory
from django.utils import timezone
from rest_framework.test import APIClient

from overbae.core.encryption import decrypt
from overbae.core.logging import RedactChatGPTCallback
from overbae.models import BillingTelemetry, Dataset, Project, User
from overbae.models.chatgpt import ChatGPTAccount, ChatGPTAuthorization, ChatGPTLoginTicket
from overbae.services import chatgpt
from overbae.services.datasets import land, semantic_checks
from overbae.services.datasets.notebook import agent, engines

pytestmark = pytest.mark.django_db
BASE = "/api/chatgpt/"
MODEL = "account-model"


@pytest.fixture
def local(settings, monkeypatch):
    settings.CLERK_API_SECRET_KEY = ""
    settings.STRIPE_SECRET_KEY = ""
    settings.CHATGPT_PLAN_USAGE_ENABLED = True
    settings.CHATGPT_REDIRECT_URI = "http://127.0.0.1:8000/api/chatgpt/callback/"
    settings.FRONTEND_URL = "http://127.0.0.1:5173"

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid="test-key", alg="RS256")
    calls = []
    control = {
        "nonce": "",
        "scope": "openid email resource.invoke chatgpt.tokens.use.direct",
        "events": [],
    }

    def request(req):
        calls.append(req)
        if req.url.path == "/.well-known/jwks.json":
            return httpx.Response(200, json={"keys": [jwk]})
        if req.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200, json={"revocation_endpoint": "https://auth.openai.com/revoke"}
            )
        if req.url.path == "/revoke":
            return httpx.Response(200)
        if req.url.path.endswith("/oauth/token"):
            form = parse_qs(req.content.decode())
            if control.get("token_error"):
                return httpx.Response(
                    400,
                    json={"error": control["token_error"], "error_description": "private-code"},
                    headers={"x-request-id": "req_test-rejection"},
                )
            claims = {
                "iss": "https://auth.openai.com",
                "aud": "oaiapp_test",
                "sub": "subscriber",
                "email": "person@example.com",
                "nonce": control["nonce"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            }
            claims.update(control.get("claims", {}))
            return httpx.Response(
                200,
                json={
                    "access_token": "test-access",
                    "refresh_token": "rotated-refresh"
                    if form["grant_type"] == ["refresh_token"]
                    else "test-refresh",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": control["scope"],
                    "id_token": jwt.encode(
                        claims, private, algorithm="RS256", headers={"kid": "test-key"}
                    ),
                },
            )
        if req.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"slug": MODEL, "display_name": "Account model", "visibility": "list"},
                        {"slug": "hidden", "display_name": "Hidden", "visibility": "hide"},
                    ]
                },
            )
        if req.url.path == "/v1/responses":
            body = json.loads(req.content)
            assert body["store"] is False and body["stream"] is True
            assert (
                not {"max_output_tokens", "temperature", "previous_response_id", "metadata"}
                & body.keys()
            )
            events = control["events"].pop(0)
            return httpx.Response(
                200,
                text="".join(f"data: {json.dumps(event)}\n\n" for event in events),
                headers={"content-type": "text/event-stream"},
            )
        raise AssertionError(f"Unexpected network request: {req.url.path}")

    monkeypatch.setattr(
        chatgpt, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(request))
    )
    user = User.objects.create_user(email="owner@example.com", password="test")
    client = APIClient()
    client.force_authenticate(user=user)
    client.defaults["HTTP_HOST"] = "127.0.0.1:8000"
    client.defaults["HTTP_ORIGIN"] = "http://127.0.0.1:5173"
    return client, user, control, calls


def connect(local):
    client, _, control, _ = local
    result = client.post(BASE + "start/", {}, format="json")
    assert result.status_code == 200, result.content
    query = parse_qs(urlparse(result.json()["authorization_url"]).query)
    control["nonce"] = query["nonce"][0]
    callback = client.get(
        BASE + "callback/",
        {"state": query["state"][0], "code": "test-code", "client_id": "oaiapp_test"},
    )
    assert callback.status_code == 302, callback.content
    status = client.get(BASE).json()
    return status["accounts"][0], query


def choose(local):
    client, _, _, _ = local
    account, _ = connect(local)
    response = client.patch(
        BASE,
        {"funding_source": "chatgpt", "account_id": account["id"], "model": MODEL},
        format="json",
    )
    assert response.status_code == 200, response.content
    return account


def completed(output):
    return {
        "type": "response.completed",
        "response": {
            "status": "completed",
            "model": MODEL,
            "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 5},
        },
    }


def message(text):
    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
    }


def test_connect_select_turn_semantic_check_and_zero_ledger(local, monkeypatch):
    client, user, control, calls = local
    account = choose(local)

    stored = ChatGPTAccount.objects.get(pk=account["id"])
    assert "test-access" not in stored.credentials
    assert "test-refresh" not in client.get(BASE).content.decode()
    project = Project.objects.create(name="Workshop", slug="workshop")
    dataset = Dataset.objects.create(project=project, name="Evidence", intent="explore")
    land.land_rows(dataset, [{"evidence": "Paris is in France", "answer": "France"}])
    control["events"] = [
        [
            completed(
                [
                    {
                        "type": "function_call",
                        "name": "check_semantic_quality",
                        "namespace": "workshop",
                        "call_id": "call_check",
                        "arguments": json.dumps(
                            {
                                "checks": [
                                    {
                                        "name": "answer_support",
                                        "question": "Is the answer supported?",
                                        "evidence_columns": ["evidence"],
                                        "answer_columns": ["answer"],
                                    }
                                ]
                            }
                        ),
                    }
                ]
            )
        ],
        [completed([message('{"answers":{"r0_c0":"pass"}}')])],
        [completed([message("The answer is supported.")])],
    ]
    monkeypatch.setattr(
        "overbae.core.llms.stream_llm_tools", lambda *a, **k: pytest.fail("Platform model called")
    )
    monkeypatch.setattr(
        "overbae.services.eval.decisions.invoke", lambda *a, **k: pytest.fail("Jev called")
    )
    monkeypatch.setattr(
        semantic_checks, "ensure_credits", lambda *a: pytest.fail("Platform credits required")
    )
    list(
        agent.iter_turn(
            dataset.id, "Check answer support", display="Check answer support", user=user
        )
    )
    dataset.refresh_from_db()
    turn = dataset.chat[-1]
    assert not turn["error"] and turn["funding_source"] == "chatgpt"
    assert turn["text"] == "The answer is supported."
    assert dataset.cells.first().quality_report["semantic_audit"]["results"]
    entries = BillingTelemetry.objects.filter(user=user, service="data-workshop")
    assert entries.count() == 2 and all(entry.amount == 0 for entry in entries)
    assert all(entry.metadata["funding_source"] == "chatgpt" for entry in entries)
    assert len([r for r in calls if r.url.path == "/v1/responses"]) == 3


@pytest.mark.parametrize(
    "bad", ["state", "cookie", "nonce", "aud", "azp", "sub", "iss", "exp", "expired"]
)
def test_callback_rejects_unbound_or_invalid_identity(local, bad):
    client, _, control, calls = local
    start = client.post(BASE + "start/", {}, format="json")
    query = parse_qs(urlparse(start.json()["authorization_url"]).query)
    control["nonce"] = query["nonce"][0]
    if bad == "cookie":
        client.cookies.clear()
    if bad in {"nonce", "aud", "iss"}:
        control["claims"] = {bad: "wrong"}
    if bad == "azp":
        control["claims"] = {"aud": ["oaiapp_test", "another-client"], "azp": "another-client"}
    if bad == "exp":
        control["claims"] = {"exp": int(time.time()) - 10}
    if bad == "expired":
        ChatGPTAuthorization.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    if bad == "sub":
        control["claims"] = {"sub": ""}
    params = {
        "state": "wrong" if bad == "state" else query["state"][0],
        "code": "test-code",
        "client_id": "oaiapp_test",
    }
    result = client.get(BASE + "callback/", params)
    assert result.status_code == 400
    assert not any(a["connected"] for a in client.get(BASE).json()["accounts"])
    if bad in {"state", "cookie"}:
        assert not any(r.url.path.endswith("/oauth/token") for r in calls)


def test_callback_replay_declined_scope_and_cross_user_selection(local):
    client, _, control, _ = local
    control["scope"] = "openid email"
    account, query = connect(local)
    assert account["plan_enabled"] is False
    assert (
        client.get(
            BASE + "callback/",
            {"state": query["state"][0], "code": "test-code", "client_id": "oaiapp_test"},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            BASE,
            {"funding_source": "chatgpt", "account_id": account["id"], "model": MODEL},
            format="json",
        ).status_code
        == 400
    )
    other = User.objects.create_user(email="other@example.com", password="test")
    client.force_authenticate(user=other)
    assert client.get(BASE).json()["accounts"] == []
    assert (
        client.patch(
            BASE,
            {"funding_source": "chatgpt", "account_id": account["id"], "model": MODEL},
            format="json",
        ).status_code
        == 400
    )


def test_refresh_rotation_disconnect_keeps_funding_and_stops(local):
    client, user, control, calls = local
    account = choose(local)

    ChatGPTAccount.objects.filter(pk=account["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    session = chatgpt.selected_session(user)
    assert session.access_token() == "test-access"
    saved = ChatGPTAccount.objects.get(pk=account["id"])
    assert json.loads(decrypt(saved.credentials))["refresh_token"] == "rotated-refresh"
    assert (
        client.post(BASE + "disconnect/", {"account_id": account["id"]}, format="json").status_code
        == 200
    )
    assert client.get(BASE).json()["funding_source"] == "chatgpt"
    assert engines.select(user).name == "chatgpt"
    with pytest.raises(chatgpt.ChatGPTError):
        session.access_token()
    assert any(r.url.path == "/revoke" for r in calls)


@pytest.mark.parametrize(
    "events",
    [
        [],
        [
            {
                "type": "response.failed",
                "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}},
            }
        ],
        [{"type": "response.incomplete", "response": {"status": "incomplete"}}],
    ],
)
def test_unfinished_stream_never_executes_tools_or_switches_provider(local, events, monkeypatch):
    _, user, control, _ = local
    choose(local)
    project = Project.objects.create(name="Workshop", slug="stream-test")
    dataset = Dataset.objects.create(project=project, name="Evidence", intent="explore")
    land.land_rows(dataset, [{"value": 1}])
    control["events"] = [
        [
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "name": "rename",
                    "call_id": "pending",
                    "arguments": '{"name":"Wrong"}',
                },
            },
            *events,
        ]
    ]
    monkeypatch.setattr(agent.Tools, "rename", lambda *a, **k: pytest.fail("Tool executed"))
    list(agent.iter_turn(dataset.id, "Rename the dataset", display="Rename the dataset", user=user))
    dataset.refresh_from_db()
    assert dataset.chat[-1]["error"]
    assert dataset.chat[-1]["funding_source"] == "chatgpt"
    assert not BillingTelemetry.objects.filter(user=user, amount__lt=0).exists()


def test_cloud_and_guest_cannot_connect(local, settings):
    client, user, _, _ = local
    settings.CLERK_API_SECRET_KEY = "hosted"
    assert client.get(BASE).json()["enabled"] is False
    assert client.post(BASE + "start/", {}, format="json").status_code == 400
    settings.CLERK_API_SECRET_KEY = ""
    user.is_guest = True
    user.save(update_fields=["is_guest"])
    assert client.post(BASE + "start/", {}, format="json").status_code == 400


@pytest.mark.parametrize(
    "error,cleared", [("invalid_grant", True), ("temporarily_unavailable", False)]
)
def test_refresh_errors_preserve_registration_and_never_fall_back(local, error, cleared):
    client, user, control, _ = local
    account = choose(local)

    ChatGPTAccount.objects.filter(pk=account["id"]).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    control["token_error"] = error
    with pytest.raises(chatgpt.ChatGPTError):
        chatgpt.selected_session(user).access_token()
    saved = ChatGPTAccount.objects.get(pk=account["id"])
    assert bool(saved.credentials) is not cleared
    assert saved.client_id == "oaiapp_test"
    assert client.get(BASE).json()["funding_source"] == "chatgpt"


def test_reconnect_reuses_registration_and_rejects_changed_identity(local):
    client, _, control, _ = local
    account, original = connect(local)
    started = client.post(BASE + "start/", {"account_id": account["id"]}, format="json")
    query = parse_qs(urlparse(started.json()["authorization_url"]).query)
    assert (
        query["client_id"] == ["oaiapp_test"]
        and query["ext_agent_host_id"] == original["ext_agent_host_id"]
    )
    assert "agent_name_hint" not in query
    control["nonce"] = query["nonce"][0]
    control["claims"] = {"sub": "different-person"}
    result = client.get(BASE + "callback/", {"state": query["state"][0], "code": "test-code"})
    assert result.status_code == 400
    assert client.get(BASE).json()["accounts"][0]["email"] == "person@example.com"


def test_chatgpt_audit_resume_is_free_and_idempotent(local):
    _, user, control, _ = local
    choose(local)

    project = Project.objects.create(name="Audit", slug="audit-test")
    dataset = Dataset.objects.create(project=project, name="Audit", intent="explore")
    land.land_rows(dataset, [{"evidence": "Paris is in France", "answer": "France"}])
    dataset.refresh_from_db()
    request = semantic_checks.SemanticReviewRequest(
        checks=[
            {
                "name": "answer_support",
                "question": "Is the answer supported?",
                "evidence_columns": ["evidence"],
                "answer_columns": ["answer"],
            }
        ]
    )
    session = chatgpt.selected_session(user)
    control["events"] = [[completed([message('{"answers":{"r0_c0":"pass"}}')])]]
    first = semantic_checks.run_checks(
        dataset, dataset.active_cell, request, user=user, chatgpt_session=session
    )
    again = semantic_checks.run_checks(
        dataset, dataset.active_cell, request, user=user, chatgpt_session=session
    )
    assert first["remaining_rows"] == again["remaining_rows"] == 0
    assert (
        BillingTelemetry.objects.filter(user=user, service="data-workshop", amount=0).count() == 1
    )


def test_completed_round_usage_survives_a_later_interruption(local):
    _, user, control, _ = local
    choose(local)
    project = Project.objects.create(name="Interrupted", slug="interrupted-test")
    dataset = Dataset.objects.create(project=project, name="Evidence", intent="explore")
    land.land_rows(dataset, [{"value": 1}])
    control["events"] = [
        [
            completed(
                [
                    {
                        "type": "function_call",
                        "name": "status",
                        "namespace": "workshop",
                        "call_id": "read",
                        "arguments": "{}",
                    }
                ]
            )
        ],
        [],
    ]
    list(
        agent.iter_turn(
            dataset.id, "Inspect this dataset", display="Inspect this dataset", user=user
        )
    )
    dataset.refresh_from_db()
    turn = dataset.chat[-1]
    assert turn["error"] and turn["model"] == MODEL
    entry = BillingTelemetry.objects.get(user=user, service="data-workshop")
    assert entry.amount == 0 and entry.metadata["llm_usage"]["prompt_tokens"] == 20


def test_callback_access_logs_redact_credentials_and_keep_status():

    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter("%(message)s %(request)s"))
    handler.addFilter(RedactChatGPTCallback())
    logger = logging.Logger("callback-access", level=logging.INFO)
    logger.addHandler(handler)
    target = "/api/chatgpt/callback/?code=private-code&state=private-state"
    logger.info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1",
        "GET",
        target,
        "1.1",
        302,
        extra={"request": RequestFactory().get(target)},
    )
    logged = output.getvalue()
    assert "private-code" not in logged and "private-state" not in logged
    assert "/api/chatgpt/callback/" in logged and "302" in logged


def start_login(local, **body):
    client, _, control, _ = local
    cookies = client.cookies
    client.force_authenticate(user=None)
    client.cookies = cookies
    result = client.post(BASE + "login/", body, format="json")
    assert result.status_code == 200, result.content
    query = parse_qs(urlparse(result.json()["authorization_url"]).query)
    control["nonce"] = query["nonce"][0]
    return query


def finish_login(local, query):
    client, _, _, _ = local
    return client.get(
        BASE + "callback/",
        {"state": query["state"][0], "code": "test-code", "client_id": "oaiapp_test"},
    )


def test_local_chatgpt_login_creates_session_and_loads_account_catalogue(local):
    client, _, _, _ = local
    query = start_login(local)
    result = finish_login(local, query)
    assert result.status_code == 302, result.content
    assert result.url == "http://127.0.0.1:5173/login?chatgpt=complete"
    assert "access" not in result.url and "code" not in result.url
    exchange = client.post(BASE + "callback/session/", {}, format="json")
    assert exchange.status_code == 200, exchange.content
    session = exchange.json()
    user = User.objects.get(pk=session["user"]["id"])
    assert user.email == "person@example.com" and not user.has_usable_password()
    assert client.post(BASE + "callback/session/", {}, format="json").status_code == 400
    client.credentials(HTTP_AUTHORIZATION="Bearer " + session["access"])
    assert client.get("/api/auth/me/").json()["id"] == user.pk
    funding = client.get(BASE).json()
    assert funding["funding_source"] == "chatgpt" and len(funding["accounts"]) == 1
    catalogue = client.get(BASE + "models/", {"account_id": funding["accounts"][0]["id"]})
    assert catalogue.json() == [{"id": MODEL, "name": "Account model"}]


def test_local_chatgpt_login_reuses_registration_and_rejects_changed_subject(local):
    client, _, control, _ = local
    first = start_login(local)
    assert finish_login(local, first).status_code == 302
    user_id = client.post(BASE + "callback/session/", {}, format="json").json()["user"]["id"]
    again = start_login(local)
    assert again["client_id"] == ["oaiapp_test"]
    assert again["ext_agent_host_id"] == first["ext_agent_host_id"]
    assert finish_login(local, again).status_code == 302
    assert (
        client.post(BASE + "callback/session/", {}, format="json").json()["user"]["id"] == user_id
    )
    client.cookies.clear()
    fresh_browser = start_login(local, email="person@example.com")
    assert fresh_browser["client_id"] == ["oaiapp_test"]
    control["claims"] = {"sub": "other-person"}
    assert finish_login(local, fresh_browser).status_code == 400
    assert client.post(BASE + "callback/session/", {}, format="json").status_code == 400


def test_existing_account_links_inside_login_without_repeating_oauth(local):
    client, user, control, calls = local
    control["claims"] = {"email": user.email}
    query = start_login(local)
    result = finish_login(local, query)
    assert result.status_code == 302 and result.url.endswith("?chatgpt=complete")
    assert not ChatGPTAccount.objects.exists()
    ticket = ChatGPTLoginTicket.objects.get()
    assert "test-access" not in ticket.connection
    info = client.get(BASE + "callback/session/")
    assert info.json() == {"email": user.email, "requires_password": True}
    assert client.post(BASE + "callback/session/", {}, format="json").status_code == 400
    assert (
        client.post(BASE + "callback/session/", {"password": "wrong"}, format="json").status_code
        == 400
    )
    assert not ChatGPTAccount.objects.exists()
    linked = client.post(BASE + "callback/session/", {"password": "test"}, format="json")
    assert linked.status_code == 200, linked.content
    assert linked.json()["user"]["id"] == user.pk
    assert not ChatGPTLoginTicket.objects.exists()
    assert (
        client.post(BASE + "callback/session/", {"password": "test"}, format="json").status_code
        == 400
    )
    assert len([r for r in calls if r.url.path.endswith("/oauth/token")]) == 1
    client.credentials(HTTP_AUTHORIZATION="Bearer " + linked.json()["access"])
    funding = client.get(BASE).json()
    assert funding["funding_source"] == "chatgpt" and funding["model"] == MODEL
    account = funding["accounts"][0]
    assert account["plan_enabled"]
    assert client.get(BASE + "models/", {"account_id": account["id"]}).json()[0]["id"] == MODEL
    assert (
        client.patch(
            BASE,
            {"funding_source": "chatgpt", "account_id": account["id"], "model": MODEL},
            format="json",
        ).status_code
        == 200
    )
    client.credentials()
    again = start_login(local)
    assert again["client_id"] == ["oaiapp_test"]
    assert finish_login(local, again).status_code == 302
    assert client.get(BASE + "callback/session/").json()["requires_password"] is False
    assert (
        client.post(BASE + "callback/session/", {}, format="json").json()["user"]["id"] == user.pk
    )


@pytest.mark.parametrize("bad", ["cookie", "expired", "password_attempts", "origin"])
def test_pending_account_link_cannot_be_claimed_without_local_auth(local, bad):
    client, user, control, _ = local
    control["claims"] = {"email": user.email}
    assert finish_login(local, start_login(local)).status_code == 302
    if bad == "cookie":
        client.cookies.clear()
    elif bad == "expired":
        ChatGPTLoginTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    elif bad == "origin":
        client.defaults["HTTP_ORIGIN"] = "http://unrelated.example"
    else:
        for _ in range(5):
            assert (
                client.post(
                    BASE + "callback/session/", {"password": "wrong"}, format="json"
                ).status_code
                == 400
            )
    assert (
        client.post(BASE + "callback/session/", {"password": "test"}, format="json").status_code
        == 400
    )
    assert not ChatGPTAccount.objects.exists()


@pytest.mark.parametrize("bad", ["nonce", "cookie", "ticket_expired", "hosted", "origin"])
def test_local_chatgpt_login_rejects_untrusted_handoffs(local, settings, bad):
    client, _, control, _ = local
    before = User.objects.count()
    if bad == "hosted":
        settings.CLERK_API_SECRET_KEY = "configured"
    if bad == "origin":
        client.defaults["HTTP_ORIGIN"] = "http://unrelated.example"
    if bad in {"hosted", "origin"}:
        client.force_authenticate(user=None)
        assert client.post(BASE + "login/", {}, format="json").status_code == 400
        assert User.objects.count() == before
        return
    query = start_login(local)
    if bad == "nonce":
        control["claims"] = {"nonce": "forged"}
    if bad == "cookie":
        client.cookies.clear()
    result = finish_login(local, query)
    if bad == "ticket_expired":
        assert result.status_code == 302
        ChatGPTLoginTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    else:
        assert result.status_code == 400
        assert User.objects.count() == before
    assert client.post(BASE + "callback/session/", {}, format="json").status_code == 400


def test_local_chatgpt_login_without_plan_permission_still_signs_in(local):
    client, _, control, _ = local
    control["scope"] = "openid profile email"
    query = start_login(local)
    assert finish_login(local, query).status_code == 302
    session = client.post(BASE + "callback/session/", {}, format="json").json()
    client.credentials(HTTP_AUTHORIZATION="Bearer " + session["access"])
    funding = client.get(BASE).json()
    assert funding["funding_source"] == "platform"
    assert not funding["accounts"][0]["plan_enabled"]


@pytest.mark.parametrize("signing_in", [True, False])
def test_rejected_code_reauthorizes_with_issued_registration_and_completes(local, signing_in):
    client, user, control, calls = local
    before = User.objects.count()
    if signing_in:
        original = start_login(local)
    else:
        started = client.post(BASE + "start/", {}, format="json")
        original = parse_qs(urlparse(started.json()["authorization_url"]).query)
    old_cookie = client.cookies[chatgpt.COOKIE].value
    control["token_error"] = "invalid_grant"
    rejected = finish_login(local, original)
    assert rejected.status_code == 302, rejected.content
    assert rejected.url.startswith(chatgpt.AUTHORIZE + "?")
    assert rejected["Cache-Control"] == "no-store"
    assert User.objects.count() == before and not ChatGPTAccount.objects.exists()
    assert not ChatGPTLoginTicket.objects.exists()
    retry = parse_qs(urlparse(rejected.url).query)
    assert retry["client_id"] == ["oaiapp_test"]
    assert retry["ext_agent_host_id"] == original["ext_agent_host_id"]
    assert retry["redirect_uri"] == original["redirect_uri"]
    assert "agent_name_hint" not in retry
    assert all(retry[k] != original[k] for k in ("state", "nonce", "code_challenge"))
    assert client.cookies[chatgpt.COOKIE].value != old_cookie
    control.pop("token_error")
    control["nonce"] = retry["nonce"][0]
    completed = client.get(BASE + "callback/", {"state": retry["state"][0], "code": "new-code"})
    assert completed.status_code == 302, completed.content
    saved = ChatGPTAccount.objects.get()
    assert saved.client_id == "oaiapp_test" and saved.subject == "subscriber"
    exchanges = [parse_qs(r.content.decode()) for r in calls if r.url.path.endswith("/oauth/token")]
    assert [e["code"] for e in exchanges] == [["test-code"], ["new-code"]]
    assert all(e["client_id"] == ["oaiapp_test"] for e in exchanges)
    assert exchanges[0]["code_verifier"] != exchanges[1]["code_verifier"]
    if signing_in:
        session = client.post(BASE + "callback/session/", {}, format="json")
        assert session.status_code == 200, session.content
        assert session.json()["user"]["id"] == saved.user_id
    else:
        assert completed.url == "http://127.0.0.1:5173/settings"
        assert saved.user == user and not ChatGPTLoginTicket.objects.exists()


@pytest.mark.parametrize("failure", ["repeat", "client", "cookie", "nonce", "replay"])
def test_code_recovery_stops_repeated_or_untrusted_callbacks(local, failure):
    client, _, control, calls = local
    original = start_login(local)
    control["token_error"] = "invalid_grant"
    rejected = finish_login(local, original)
    assert rejected.status_code == 302, rejected.content
    retry = parse_qs(urlparse(rejected.url).query)
    control["nonce"] = retry["nonce"][0]
    params = {"state": retry["state"][0], "code": "new-code"}
    if failure != "repeat":
        control.pop("token_error")
    if failure == "client":
        params["client_id"] = "oaiapp_other"
    elif failure == "cookie":
        client.cookies.clear()
    elif failure == "nonce":
        control["claims"] = {"nonce": original["nonce"][0]}
    elif failure == "replay":
        params["state"] = original["state"][0]
    result = client.get(BASE + "callback/", params)
    assert result.status_code == 400
    assert "access has expired" not in result.content.decode()
    assert not ChatGPTAccount.objects.exists() and not ChatGPTLoginTicket.objects.exists()
    if failure in {"client", "cookie", "replay"}:
        assert len([r for r in calls if r.url.path.endswith("/oauth/token")]) == 1


def test_token_rejection_diagnostics_do_not_leak_credentials(local, caplog):
    _, _, control, _ = local
    original = start_login(local)
    control["token_error"] = "invalid_client"
    with caplog.at_level(logging.WARNING, logger="overbae.services.chatgpt"):
        result = finish_login(local, original)
    assert result.status_code == 400
    assert "invalid_client" in caplog.text and "req_test-rejection" in caplog.text
    assert "authorization_code" in caplog.text and "400" in caplog.text
    assert all(
        secret not in caplog.text for secret in ("private-code", "test-code", original["state"][0])
    )
