from __future__ import annotations

import json
import time
import uuid
from decimal import Decimal

import pytest
import requests
from conftest import TRAIN_ROWS, drain_stream, frozen_dataset
from django.test import override_settings
from factories import api_key_client, make_member, make_project, make_user
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import BillingService, BillingTelemetry, InferenceCall, Project, User
from overbae.models.inference import DeployedModel
from overbae.services.inference_client import InferenceClientError

pytestmark = pytest.mark.django_db

INFERENCE = "http://inference.test"
OPENROUTER = "https://openrouter.ai"
URL = "/api/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Hello"}]


def _deployed_model(
    project: Project,
    *,
    model_id: str | None = None,
    status_val: str = DeployedModel.Status.READY,
    base_model_id: str = "meta-llama/Llama-3.2-3B-Instruct",
) -> DeployedModel:
    return DeployedModel.objects.create(
        project=project,
        model_id=model_id or f"ft-{uuid.uuid4().hex[:8]}",
        status=status_val,
        base_model_id=base_model_id,
    )


def _capability(project: Project, *, active_model: DeployedModel | None = None, **kwargs):
    from overbae.models import Capability

    return Capability.objects.create(
        project=project,
        name=kwargs.pop("name", "Invoice triage"),
        slug=kwargs.pop("slug", f"a-{uuid.uuid4().hex[:8]}"),
        active_model=active_model,
        **kwargs,
    )


def _jwt_client(user: User) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _member() -> tuple[User, Project]:
    user, project = make_user(), make_project()
    make_member(user, project)
    return user, project


def _served(fake_llm, host: str) -> list:
    return [r for r in fake_llm.requests if r.url.startswith(host)]


def _body(response) -> dict:
    return json.loads(drain_stream(response))


_BILLED_USAGE = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def _forced_usage_sse(*, omit_terminal_choices: bool = False) -> str:
    chunks: list[dict] = [
        {
            "id": "c-1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": {"content": text}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": i, "total_tokens": 7 + i},
        }
        for i, text in enumerate(("Hel", "lo", "!"), start=1)
    ]
    terminal: dict = {
        "id": "c-1",
        "object": "chat.completion.chunk",
        "usage": _BILLED_USAGE,
        "metrics": {"tokens_per_second": 50.0},
    }
    if not omit_terminal_choices:
        terminal["choices"] = []
    chunks.append(terminal)
    return "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"


def _sse_payloads(body: str) -> list[dict]:
    return [
        json.loads(payload)
        for line in body.splitlines()
        if line.startswith("data:") and (payload := line[len("data:") :].strip()) != "[DONE]"
    ]


class TestInferenceModelRegistry:
    def test_inference_models_are_slugged_and_unique(self):
        from overbae.core.model_registry import inference_models

        ids = [m.slug for m in inference_models()]
        assert ids and len(ids) == len(set(ids))
        assert all("/" in i for i in ids)
        assert not any("mistral" in i or i.endswith("/o3") or "deepseek-r1" in i for i in ids)

    def test_is_inference_model_true_for_known_prefix(self):
        from overbae.core.model_registry import is_inference_model

        assert is_inference_model("anthropic/claude-sonnet-5")
        assert is_inference_model("openai/gpt-5.6-sol")
        assert is_inference_model("meta-llama/llama-3.1-8b-instruct")
        assert is_inference_model("qwen/qwen3-8b")

    def test_is_inference_model_false_for_finetuned_id(self):
        from overbae.core.model_registry import is_inference_model

        assert not is_inference_model("ft-abc12345-llama")
        assert not is_inference_model("my-custom-model")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/v1/models"),
        ("get", "/api/v1/models/ft-abc"),
        ("delete", "/api/v1/models/ft-abc"),
        ("post", URL),
    ],
)
def test_every_gateway_route_requires_credentials(method, path):
    response = getattr(APIClient(), method)(path, {}, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/api/v1/models/{id}"), ("delete", "/api/v1/models/{id}"), ("post", URL)],
)
def test_another_accounts_deployment_is_not_found(method, path):
    owner, project = _member()
    stranger, _ = _member()
    model = _deployed_model(project)
    body = {"model": model.model_id, "messages": MESSAGES}
    response = getattr(_jwt_client(stranger), method)(
        path.format(id=model.model_id), body, format="json"
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND
    model.refresh_from_db()
    assert model.status == DeployedModel.Status.READY


class TestModelsListEndpoint:
    URL = "/api/v1/models"

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_returns_finetuned_and_frontier(self):
        u, p = _member()
        _deployed_model(p)
        r = _jwt_client(u).get(self.URL)
        assert r.status_code == status.HTTP_200_OK
        data = r.json()["data"]
        assert any(m["finetuned"] is True for m in data)
        assert any(m["finetuned"] is False for m in data)

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_finetuned_field_shape(self):
        u, p = _member()
        m = _deployed_model(p)
        ft = [x for x in _jwt_client(u).get(self.URL).json()["data"] if x["finetuned"]]
        assert len(ft) == 1
        assert ft[0]["id"] == m.model_id
        assert ft[0]["owned_by"] == "overmind"
        assert ft[0]["status"] == "ready"
        assert "base_model" in ft[0]

    def test_no_openrouter_key_omits_frontier(self, settings):
        settings.OPENROUTER_API_KEY = ""
        u, p = _member()
        _deployed_model(p)
        data = _jwt_client(u).get(self.URL).json()["data"]
        assert all(m["finetuned"] for m in data)

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("", {"ready"}),
            ("?status=all", {"ready", "deploying", "failed"}),
            ("?status=failed", {"failed"}),
        ],
    )
    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_status_filter(self, query, expected):
        u, p = _member()
        for state in ("ready", "deploying", "failed"):
            _deployed_model(p, status_val=state)
        ft = [x for x in _jwt_client(u).get(self.URL + query).json()["data"] if x["finetuned"]]
        assert {x["status"] for x in ft} == expected

    def test_invalid_status_returns_400(self):
        u, _ = _member()
        assert _jwt_client(u).get(self.URL + "?status=bogus").status_code == 400

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_scoped_to_user_projects(self):
        u_a, p_a = _member()
        _deployed_model(p_a)
        _deployed_model(make_project())
        ft = [
            x
            for x in _jwt_client(u_a).get(self.URL + "?status=all").json()["data"]
            if x["finetuned"]
        ]
        assert len(ft) == 1

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_api_key_auth_also_works(self):
        u, p = _member()
        _deployed_model(p)
        assert api_key_client(u, p).get(self.URL).status_code == status.HTTP_200_OK


class TestModelDetail:
    def _url(self, model_id: str) -> str:
        return f"/api/v1/models/{model_id}"

    def test_returns_model_details(self):
        u, p = _member()
        m = _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        r = _jwt_client(u).get(self._url(m.model_id))
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert body["id"] == m.model_id
        assert body["finetuned"] is True
        assert body["status"] == "deploying"
        assert body["base_model"] == m.base_model_id
        assert body["owned_by"] == "overmind"

    @pytest.mark.parametrize("model_id", ["ft-doesnotexist", "anthropic/claude-sonnet-5"])
    def test_an_unknown_or_frontier_model_has_no_detail(self, model_id):
        u, _ = _member()
        assert _jwt_client(u).get(self._url(model_id)).status_code == 404

    def test_delete_removes_the_weights_and_marks_the_model_deleted(self, scripted):
        inference = scripted(INFERENCE).reply(json_body={"ok": True})
        u, p = _member()
        m = _deployed_model(p)
        r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["id"] == m.model_id
        assert r.json()["deleted"] is True
        [call] = inference.calls
        assert (call.method, call.path) == ("DELETE", f"/models/{m.model_id}")
        m.refresh_from_db()
        assert m.status == DeployedModel.Status.DELETED

    def test_a_failed_weight_delete_marks_the_model_failed(self, scripted):
        scripted(INFERENCE).reply(500, text="Modal down")
        u, p = _member()
        m = _deployed_model(p)
        r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_502_BAD_GATEWAY
        m.refresh_from_db()
        assert m.status == DeployedModel.Status.FAILED

    def test_cannot_delete_frontier_model(self):
        u, _ = _member()
        r = _jwt_client(u).delete(self._url("anthropic/claude-sonnet-5"))
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "not a fine-tuned model" in r.json()["error"]["message"]

    @pytest.mark.parametrize("state", [DeployedModel.Status.DELETING, DeployedModel.Status.DELETED])
    def test_cannot_delete_a_model_twice(self, state):
        u, p = _member()
        m = _deployed_model(p, status_val=state)
        assert _jwt_client(u).delete(self._url(m.model_id)).status_code == 409


class TestChatCompletionsEndpoint:
    @pytest.mark.parametrize(
        "body", [{"messages": MESSAGES}, {"model": "ft-abc"}], ids=["no model", "no messages"]
    )
    def test_a_request_without_model_or_messages_is_rejected(self, body):
        u, _ = _member()
        assert _jwt_client(u).post(URL, body, format="json").status_code == 400

    @pytest.mark.parametrize("model", ["ft-doesnotexist", "mistralai/mistral-large", "gpt-5-mini"])
    def test_an_unknown_or_unlisted_model_is_not_found(self, model):
        u, _ = _member()
        r = _jwt_client(u).post(URL, {"model": model, "messages": MESSAGES}, format="json")
        assert r.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_the_optimiser_header_opens_the_catalog_to_api_keys_only(self, fake_llm):
        u, p = _member()
        body = {"model": "mistralai/mistral-large", "messages": MESSAGES}
        r = api_key_client(u, p).post(URL, body, format="json", HTTP_X_OVERMIND_OPTIMISER="1")
        assert r.status_code == status.HTTP_200_OK
        assert _served(fake_llm, OPENROUTER)[-1].model == "mistralai/mistral-large"

        jwt = _jwt_client(u).post(URL, body, format="json", HTTP_X_OVERMIND_OPTIMISER="1")
        assert jwt.status_code == status.HTTP_404_NOT_FOUND
        assert len(_served(fake_llm, OPENROUTER)) == 1

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_the_optimiser_may_name_a_model_without_its_vendor(self, fake_llm):
        u, p = _member()
        body = {"model": "gpt-5-mini", "messages": MESSAGES}
        r = api_key_client(u, p).post(URL, body, format="json", HTTP_X_OVERMIND_OPTIMISER="1")
        assert r.status_code == status.HTTP_200_OK
        assert _served(fake_llm, OPENROUTER)[-1].model == "openai/gpt-5-mini"

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_a_frontier_request_reaches_openrouter_with_its_response_format(self, fake_llm):
        fake_llm.on(lambda r: r.url.startswith(OPENROUTER), "Hi!")
        u, _ = _member()
        r = _jwt_client(u).post(
            URL,
            {
                "model": "anthropic/claude-sonnet-5",
                "messages": MESSAGES,
                "response_format": {"type": "json_object"},
            },
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["choices"][0]["message"]["content"] == "Hi!"
        assert _served(fake_llm, OPENROUTER)[-1].body["response_format"] == {"type": "json_object"}

    @pytest.mark.parametrize(
        ("usage", "expected"),
        [
            ({"cost": 1.23e-4}, Decimal("0.000123")),
            ({}, Decimal("0.000011")),
        ],
        ids=["provider cost wins", "catalog price"],
    )
    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_frontier_usage_is_charged_at_the_provider_cost_or_catalog_price(
        self, fake_llm, usage, expected
    ):
        fake_llm.on(
            lambda r: r.url.startswith(OPENROUTER),
            {"content": "Hi!", "usage": {"prompt_tokens": 1, "completion_tokens": 5, **usage}},
        )
        u, p = _member()
        body = {"model": "anthropic/claude-sonnet-5", "messages": MESSAGES}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 200

        [charge] = BillingTelemetry.objects.filter(user=u, service=BillingService.INFERENCE)
        assert -charge.amount == expected

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_a_frontier_call_with_no_known_price_charges_nothing(self, fake_llm):
        fake_llm.catalog_models = []
        u, p = _member()
        body = {"model": "anthropic/claude-sonnet-5", "messages": MESSAGES}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 200
        assert not BillingTelemetry.objects.filter(
            user=u, service=BillingService.INFERENCE
        ).exists()

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_an_openrouter_failure_is_a_bad_gateway(self, fake_llm):
        fake_llm.fail(lambda r: r.url.startswith(OPENROUTER), status=404)
        u, _ = _member()
        body = {"model": "anthropic/claude-sonnet-5", "messages": MESSAGES}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 502

    def test_a_frontier_model_without_an_openrouter_key_is_unavailable(self, settings):
        settings.OPENROUTER_API_KEY = ""
        u, _ = _member()
        body = {"model": "anthropic/claude-sonnet-5", "messages": MESSAGES}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 503

    def test_a_model_that_is_not_ready_is_unavailable(self):
        u, p = _member()
        m = _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        body = {"model": m.model_id, "messages": MESSAGES}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 503

    @pytest.mark.parametrize("auth", ["jwt", "api_key"])
    def test_a_finetuned_completion_is_served_by_the_inference_gateway(self, fake_llm, auth):
        fake_llm.on(lambda r: r.url.startswith(INFERENCE), "Hi!")
        u, p = _member()
        m = _deployed_model(p)
        client = _jwt_client(u) if auth == "jwt" else api_key_client(u, p)
        r = client.post(
            URL,
            {"model": m.model_id, "messages": MESSAGES, "response_format": {"type": "json_object"}},
            format="json",
        )
        assert r.status_code == status.HTTP_200_OK
        body = _body(r)
        assert not r.streaming
        assert body["choices"][0]["message"]["content"] == "Hi!"
        [served] = _served(fake_llm, INFERENCE)
        assert served.model == m.model_id
        assert served.body["response_format"] == {"type": "json_object"}

    @pytest.mark.parametrize(
        "failure", [500, requests.ConnectionError("down"), RuntimeError("boom")]
    )
    def test_a_backend_failure_is_a_bad_gateway_without_its_detail(self, scripted, failure):
        inference = scripted(INFERENCE)
        if isinstance(failure, int):
            inference.reply(failure, text="private provider detail")
        else:
            inference.fail(failure)
        u, p = _member()
        m = _deployed_model(p)
        r = _jwt_client(u).post(URL, {"model": m.model_id, "messages": MESSAGES}, format="json")
        assert r.status_code == status.HTTP_502_BAD_GATEWAY
        body = _body(r)
        assert body["error"]["type"] == "server_error"
        assert "private provider detail" not in json.dumps(body)

    @pytest.mark.parametrize(
        ("base", "sent", "expected"),
        [
            ("openai/gpt-oss-20b", {}, {"include_reasoning": False, "reasoning_effort": "low"}),
            (
                "openai/gpt-oss-20b",
                {"include_reasoning": True, "reasoning_effort": "high"},
                {"include_reasoning": True, "reasoning_effort": "high"},
            ),
            (
                "unsloth/Muse-Glimmer-30B",
                {},
                {"include_reasoning": False, "chat_template_kwargs": {"reasoning_strength": "low"}},
            ),
        ],
        ids=["gpt-oss defaults", "client overrides", "muse defaults"],
    )
    def test_reasoning_families_get_their_serving_defaults(self, fake_llm, base, sent, expected):
        u, p = _member()
        m = _deployed_model(p, model_id=f"ft-test-{uuid.uuid4().hex[:6]}", base_model_id=base)
        body = {"model": m.model_id, "messages": MESSAGES, **sent}
        assert _jwt_client(u).post(URL, body, format="json").status_code == 200
        [served] = _served(fake_llm, INFERENCE)
        for key, value in expected.items():
            if isinstance(value, dict):
                assert served.body[key].items() >= value.items()
            else:
                assert served.body[key] == value
        if "chat_template_kwargs" in expected:
            assert "reasoning_effort" not in served.body

    def _stream(self, scripted, sse: str, stream_options=None):
        scripted(INFERENCE).reply(text=sse, headers={"content-type": "text/event-stream"})
        u, p = _member()
        m = _deployed_model(p)
        body = {"model": m.model_id, "messages": MESSAGES, "stream": True}
        if stream_options is not None:
            body["stream_options"] = stream_options
        r = _jwt_client(u).post(URL, body, format="json")
        assert r.status_code == status.HTTP_200_OK
        assert "text/event-stream" in r.get("Content-Type", "")
        raw = drain_stream(r).decode()
        assert raw.startswith(": ")
        return _sse_payloads(raw), InferenceCall.objects.get(pk=r["X-Request-ID"])

    def test_a_stream_forwards_deltas_and_hides_forced_usage(self, scripted):
        payloads, call = self._stream(scripted, _forced_usage_sse())

        assert "".join(c["choices"][0]["delta"]["content"] for c in payloads) == "Hello!"
        assert all("usage" not in c and "metrics" not in c for c in payloads)
        assert (call.prompt_tokens, call.completion_tokens) == (7, 3)
        assert call.tokens_per_second == 50.0
        assert call.outcome == "succeeded"

    def test_include_usage_exposes_usage_and_bills_the_same(self, scripted):
        payloads, call = self._stream(scripted, _forced_usage_sse(), {"include_usage": True})

        text = "".join(c["choices"][0]["delta"]["content"] for c in payloads if c.get("choices"))
        assert text == "Hello!"
        assert all("usage" in c for c in payloads)
        assert payloads[-1]["usage"] == _BILLED_USAGE
        assert all("metrics" not in c for c in payloads)
        assert (call.prompt_tokens, call.completion_tokens) == (7, 3)

    @pytest.mark.parametrize("omit_terminal_choices", [False, True])
    def test_a_usage_only_terminal_chunk_is_dropped(self, scripted, omit_terminal_choices):
        payloads, call = self._stream(
            scripted, _forced_usage_sse(omit_terminal_choices=omit_terminal_choices)
        )
        assert len(payloads) == 3
        assert all(c["choices"] for c in payloads)
        assert call.completion_tokens == 3

    def test_a_stream_that_ends_without_done_is_recorded_as_incomplete(self, scripted):
        payloads, call = self._stream(scripted, "")

        assert payloads[-1]["error"]["type"] == "incomplete_stream"
        assert call.outcome == "failed"
        assert call.error_code == "incomplete_stream"


class TestAgentAliasRouting:
    def _alias(self, capability) -> str:
        return f"overmind/{capability.id}"

    def _post(self, client, model: str):
        return client.post(URL, {"model": model, "messages": MESSAGES}, format="json")

    @pytest.mark.parametrize("auth", ["api_key", "jwt"])
    def test_alias_dispatches_concrete_model_id(self, fake_llm, auth):
        u, p = _member()
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)
        client = api_key_client(u, p) if auth == "api_key" else _jwt_client(u)

        r = self._post(client, self._alias(capability))

        assert r.status_code == status.HTTP_200_OK
        _body(r)
        assert _served(fake_llm, INFERENCE)[-1].model == m.model_id

    def test_malformed_uuid_returns_400_naming_shape_and_source(self):
        u, _ = _member()
        r = self._post(_jwt_client(u), "overmind/not-a-uuid")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        message = r.json()["error"]["message"]
        assert "overmind/<capability-uuid>" in message
        assert "Models tab" in message

    @pytest.mark.parametrize("unroutable", ["absent", "other_project", "soft_deleted"])
    def test_unroutable_capability_is_an_indistinguishable_404(self, unroutable):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        client = _jwt_client(u)
        if unroutable == "absent":
            model = f"overmind/{uuid.uuid4()}"
        elif unroutable == "other_project":
            client = api_key_client(u, p_a)
            model = self._alias(_capability(p_b, active_model=_deployed_model(p_b)))
        else:
            model = self._alias(
                _capability(p_a, active_model=_deployed_model(p_a), status="leftover")
            )

        assert self._post(client, model).status_code == status.HTTP_404_NOT_FOUND

    def test_capability_without_active_model_returns_404_not_frontier_fallback(self, fake_llm):
        u, p = _member()
        capability = _capability(p, model="openai/gpt-5.6-sol")
        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert "no active model" in r.json()["error"]["message"]
        assert fake_llm.requests == []

    def test_non_ready_active_model_returns_503_naming_both_ids(self):
        u, p = _member()
        m = _deployed_model(p, status_val=DeployedModel.Status.WARMING)
        capability = _capability(p, active_model=m)

        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        message = r.json()["error"]["message"]
        assert self._alias(capability) in message
        assert m.model_id in message

    def test_active_model_in_another_project_reads_as_no_active_model(self):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        capability = _capability(p_a, active_model=_deployed_model(p_b))

        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert "no active model" in r.json()["error"]["message"]


class TestAgentAliasOnModelsEndpoints:
    def test_models_list_includes_alias_with_name(self):
        u, p = _member()
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)

        rows = {x["id"]: x for x in _jwt_client(u).get("/api/v1/models").json()["data"]}
        alias = rows[f"overmind/{capability.id}"]
        assert alias["name"] == "Invoice triage"
        assert alias["finetuned"] is True
        assert alias["base_model"] == m.base_model_id

    def test_models_list_under_pinned_key_omits_other_project(self):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        capability_a = _capability(p_a, active_model=_deployed_model(p_a))
        m_b = _deployed_model(p_b)
        capability_b = _capability(p_b, active_model=m_b)

        ids = {x["id"] for x in api_key_client(u, p_a).get("/api/v1/models").json()["data"]}
        assert f"overmind/{capability_a.id}" in ids
        assert f"overmind/{capability_b.id}" not in ids
        assert m_b.model_id not in ids

    def test_models_list_omits_an_alias_pointing_into_another_project(self):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        capability = _capability(p_a, active_model=_deployed_model(p_b))

        ids = {x["id"] for x in _jwt_client(u).get("/api/v1/models").json()["data"]}
        assert f"overmind/{capability.id}" not in ids

    def test_alias_detail_get_returns_concrete_deployment(self):
        u, p = _member()
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)

        r = _jwt_client(u).get(f"/api/v1/models/overmind/{capability.id}")
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["id"] == m.model_id

    def test_alias_detail_delete_returns_400(self):
        u, p = _member()
        capability = _capability(p, active_model=_deployed_model(p))

        r = _jwt_client(u).delete(f"/api/v1/models/overmind/{capability.id}")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot be deleted" in r.json()["error"]["message"]


class TestApiKeyProjectScoping:
    def test_a_pinned_key_cannot_reach_a_sibling_projects_model(self, fake_llm):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        m_b = _deployed_model(p_b)
        body = {"model": m_b.model_id, "messages": MESSAGES}

        assert api_key_client(u, p_a).post(URL, body, format="json").status_code == 404
        assert api_key_client(u, p_a).get(f"/api/v1/models/{m_b.model_id}").status_code == 404
        session = _jwt_client(u).post(URL, body, format="json")
        assert session.status_code == status.HTTP_200_OK
        _body(session)


class TestActiveModelValidation:
    def _url(self, capability) -> str:
        return f"/api/capabilities/{capability.id}/"

    def test_active_model_from_another_project_is_rejected(self):
        u, p_a, p_b = make_user(), make_project(), make_project()
        make_member(u, p_a)
        make_member(u, p_b)
        capability = _capability(p_a)
        foreign = _deployed_model(p_b)

        r = _jwt_client(u).patch(
            self._url(capability), {"active_model": str(foreign.id)}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        capability.refresh_from_db()
        assert capability.active_model_id is None

    def test_active_model_owned_by_a_sibling_capability_is_allowed(self):
        from overbae.models import FinetuningJob

        u, p = _member()
        sibling = _capability(p, slug=f"sib-{uuid.uuid4().hex[:6]}")
        capability = _capability(p)
        m = _deployed_model(p)
        m.finetuning_job = FinetuningJob.objects.create(
            project=p,
            capability=sibling,
            dataset=frozen_dataset(p, TRAIN_ROWS, name="t"),
            base_model="Qwen/Qwen3-8B",
            provider=FinetuningJob.Provider.MODAL,
        )
        m.save(update_fields=["finetuning_job"])

        r = _jwt_client(u).patch(self._url(capability), {"active_model": str(m.id)}, format="json")
        assert r.status_code == status.HTTP_200_OK
        capability.refresh_from_db()
        assert capability.active_model_id is None
        assert capability.activation.target_id == m.id
        assert capability.activation.stage == "checking"


def test_a_late_nonstream_failure_aborts_the_response_and_records_the_request(
    scripted, monkeypatch
):
    from overbae.api.streaming import iter_keeping_idle_alive

    def slow_outage(_call):
        time.sleep(0.2)
        return 500, {"content-type": "text/plain"}, b"private provider detail"

    scripted(INFERENCE).then(slow_outage)
    monkeypatch.setitem(iter_keeping_idle_alive.__kwdefaults__, "interval_s", 0.05)
    user, project = _member()
    model = _deployed_model(project)

    response = api_key_client(user, project).post(
        URL, {"model": model.model_id, "messages": MESSAGES}, format="json"
    )

    assert response.streaming
    with pytest.raises(InferenceClientError, match="Inference request"):
        drain_stream(response)
    call = InferenceCall.objects.get(pk=response["X-Request-ID"])
    assert call.outcome == "failed"
    assert call.error_code == "server_error"
    assert call.end_to_end_ms is not None


def test_application_alias_success_confirms_connection_but_failed_request_does_not(scripted):
    inference = scripted(INFERENCE)
    inference.reply(500, text="private provider detail")
    inference.reply(
        json_body={
            "choices": [{"message": {"role": "assistant", "content": "answer"}}],
            "usage": {"completion_tokens": 1},
        }
    )
    user, project = _member()
    model = _deployed_model(project)
    capability = _capability(project, active_model=model)
    alias = f"overmind/{capability.pk}"
    client = api_key_client(user, project)
    body = {"model": alias, "messages": MESSAGES}

    failed = client.post(URL, body, format="json")
    assert failed.status_code == 502
    assert failed.data["error"]["request_id"] == failed["X-Request-ID"]
    assert "private provider detail" not in str(failed.data)
    capability.refresh_from_db()
    assert capability.first_application_request_at is None

    succeeded = client.post(URL, body, format="json")
    assert succeeded.status_code == 200
    capability.refresh_from_db()
    assert capability.first_application_request_at is not None
    call = InferenceCall.objects.get(pk=succeeded["X-Request-ID"])
    assert call.source == "application"
    assert call.requested_model == alias
    assert call.outcome == "succeeded"
