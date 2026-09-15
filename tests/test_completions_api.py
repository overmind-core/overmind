from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from conftest import TRAIN_ROWS, drain_stream, frozen_dataset
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Project, ProjectMembership, User
from overbae.models.inference import DeployedModel

pytestmark = pytest.mark.django_db


def _consume(response):
    """Non-stream finetuned completions ping first; drain so the backend call runs."""
    drain_stream(response)
    return response


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _membership(user: User, project: Project) -> None:
    ProjectMembership.objects.create(user=user, project=project)


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
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


_BILLED_USAGE = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def _forced_usage_chunks(*, omit_terminal_choices: bool = False) -> list[str]:
    """SSE lines as vLLM emits them under ``--enable-force-include-usage``: every chunk
    carries a cumulative ``usage`` block, not just the last one."""
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
        "metrics": {"e2e_latency_ms": 42.0},
    }
    if not omit_terminal_choices:
        terminal["choices"] = []
    chunks.append(terminal)
    return [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]


def _sse_payloads(body: str) -> list[dict]:
    return [
        json.loads(payload)
        for line in body.splitlines()
        if line.startswith("data:") and (payload := line[len("data:") :].strip()) != "[DONE]"
    ]


def _api_key_client(user: User, project: Project) -> APIClient:
    from overbae.models import APIToken

    raw_key, _ = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw_key)
    return client


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


class TestModelsListEndpoint:
    URL = "/api/v1/models"

    def test_requires_auth(self):
        r = APIClient().get(self.URL)
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_returns_finetuned_and_frontier(self):
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p)
        r = _jwt_client(u).get(self.URL)
        assert r.status_code == status.HTTP_200_OK
        data = r.json()["data"]
        assert any(m["finetuned"] is True for m in data)
        assert any(m["finetuned"] is False for m in data)

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_finetuned_field_shape(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        r = _jwt_client(u).get(self.URL)
        ft_models = [x for x in r.json()["data"] if x["finetuned"]]
        assert len(ft_models) == 1
        ft = ft_models[0]
        assert ft["id"] == m.model_id
        assert ft["owned_by"] == "overmind"
        assert ft["status"] == "ready"
        assert "base_model" in ft

    def test_no_openrouter_key_omits_frontier(self, settings):
        settings.OPENROUTER_API_KEY = ""
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p)
        r = _jwt_client(u).get(self.URL)
        assert r.status_code == status.HTTP_200_OK
        data = r.json()["data"]
        assert all(m["finetuned"] for m in data)

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_default_status_filter_returns_only_ready(self):
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p, status_val=DeployedModel.Status.READY)
        _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        r = _jwt_client(u).get(self.URL)
        ft = [x for x in r.json()["data"] if x["finetuned"]]
        assert len(ft) == 1
        assert ft[0]["status"] == "ready"

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_status_all_returns_every_status(self):
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p, status_val=DeployedModel.Status.READY)
        _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        _deployed_model(p, status_val=DeployedModel.Status.FAILED)
        r = _jwt_client(u).get(self.URL + "?status=all")
        ft = [x for x in r.json()["data"] if x["finetuned"]]
        assert len(ft) == 3

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_status_filter_specific_value(self):
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p, status_val=DeployedModel.Status.READY)
        _deployed_model(p, status_val=DeployedModel.Status.FAILED)
        r = _jwt_client(u).get(self.URL + "?status=failed")
        ft = [x for x in r.json()["data"] if x["finetuned"]]
        assert len(ft) == 1
        assert ft[0]["status"] == "failed"

    def test_invalid_status_returns_400(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).get(self.URL + "?status=bogus")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_scoped_to_user_projects(self):
        u_a, p_a = _user(), _project()
        _membership(u_a, p_a)
        _deployed_model(p_a)

        p_b = _project()  # user_a has no membership here
        _deployed_model(p_b)

        r = _jwt_client(u_a).get(self.URL + "?status=all")
        ft = [x for x in r.json()["data"] if x["finetuned"]]
        assert len(ft) == 1

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_api_key_auth_also_works(self):
        u, p = _user(), _project()
        _membership(u, p)
        _deployed_model(p)
        r = _api_key_client(u, p).get(self.URL)
        assert r.status_code == status.HTTP_200_OK


class TestModelDetailRetrieve:
    def _url(self, model_id: str) -> str:
        return f"/api/v1/models/{model_id}"

    def test_requires_auth(self):
        r = APIClient().get(self._url("ft-abc"))
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_returns_model_details(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        r = _jwt_client(u).get(self._url(m.model_id))
        assert r.status_code == status.HTTP_200_OK
        body = r.json()
        assert body["id"] == m.model_id
        assert body["finetuned"] is True
        assert body["status"] == "ready"
        assert body["base_model"] == m.base_model_id
        assert body["owned_by"] == "overmind"

    def test_returns_404_for_unknown_model(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).get(self._url("ft-doesnotexist"))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_returns_404_for_other_users_model(self):
        p_a, p_b = _project(), _project()
        u_a, u_b = _user(), _user()
        _membership(u_a, p_a)
        _membership(u_b, p_b)
        m = _deployed_model(p_a)
        r = _jwt_client(u_b).get(self._url(m.model_id))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_non_finetuned_model_returns_404(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).get(self._url("anthropic/claude-sonnet-5"))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_works_for_non_ready_model(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        r = _jwt_client(u).get(self._url(m.model_id))
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["status"] == "deploying"


class TestModelDetailDelete:
    def _url(self, model_id: str) -> str:
        return f"/api/v1/models/{model_id}"

    def _mock_inference_client(self):
        mock = MagicMock()
        mock.delete_model.return_value = None
        return mock

    def test_requires_auth(self):
        r = APIClient().delete(self._url("ft-abc"))
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_delete_marks_model_deleted(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = self._mock_inference_client()
        with patch("overbae.api.completions.get_inference_client", return_value=mock_client):
            r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["deleted"] is True
        assert r.json()["id"] == m.model_id
        m.refresh_from_db()
        assert m.status == DeployedModel.Status.DELETED

    def test_delete_calls_inference_client(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = self._mock_inference_client()
        with patch("overbae.api.completions.get_inference_client", return_value=mock_client):
            _jwt_client(u).delete(self._url(m.model_id))
        mock_client.delete_model.assert_called_once_with(m.model_id)

    def test_cannot_delete_frontier_model(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).delete(self._url("anthropic/claude-sonnet-5"))
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        body = r.json()
        assert "not a fine-tuned model" in body["error"]["message"]

    def test_cannot_delete_model_already_deleting(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p, status_val=DeployedModel.Status.DELETING)
        r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_409_CONFLICT

    def test_cannot_delete_already_deleted_model(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p, status_val=DeployedModel.Status.DELETED)
        r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_409_CONFLICT

    def test_delete_returns_404_for_other_users_model(self):
        p_a, p_b = _project(), _project()
        u_a, u_b = _user(), _user()
        _membership(u_a, p_a)
        _membership(u_b, p_b)
        m = _deployed_model(p_a)
        r = _jwt_client(u_b).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_delete_sets_failed_on_inference_error(self):
        from overbae.services.inference_client import InferenceClientError

        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = MagicMock()
        mock_client.delete_model.side_effect = InferenceClientError("Modal down")
        with patch("overbae.api.completions.get_inference_client", return_value=mock_client):
            r = _jwt_client(u).delete(self._url(m.model_id))
        assert r.status_code == status.HTTP_502_BAD_GATEWAY
        m.refresh_from_db()
        assert m.status == DeployedModel.Status.FAILED


class TestChatCompletionsEndpoint:
    URL = "/api/v1/chat/completions"
    MESSAGES = [{"role": "user", "content": "Hello"}]

    def test_requires_auth(self):
        r = APIClient().post(self.URL, {}, content_type="application/json")
        assert r.status_code == status.HTTP_401_UNAUTHORIZED

    def test_missing_model_returns_400(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(self.URL, {"messages": self.MESSAGES}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_missing_messages_returns_400(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(self.URL, {"model": "ft-abc"}, format="json")
        assert r.status_code == status.HTTP_400_BAD_REQUEST

    def test_unknown_finetuned_model_returns_404(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(
            self.URL,
            {"model": "ft-doesnotexist", "messages": self.MESSAGES},
            format="json",
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND

    def test_unlisted_slug_without_optimiser_header_returns_404(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(
            self.URL,
            {"model": "mistralai/mistral-large", "messages": self.MESSAGES},
            format="json",
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_optimiser_header_bypasses_the_allowlist_for_api_keys_only(self):
        u, p = _user(), _project()
        _membership(u, p)
        fake_resp = {
            "id": "chatcmpl-opt",
            "object": "chat.completion",
            "model": "mistralai/mistral-large",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        mock_post = MagicMock()
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = fake_resp
        with patch("overbae.api.completions._requests.post", mock_post):
            r = _api_key_client(u, p).post(
                self.URL,
                {"model": "mistralai/mistral-large", "messages": self.MESSAGES},
                format="json",
                HTTP_X_OVERMIND_OPTIMISER="1",
            )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["id"] == "chatcmpl-opt"
        assert mock_post.call_args.kwargs["json"]["model"] == "mistralai/mistral-large"

        # The same header on a JWT session buys nothing — the allowlist still gates.
        with patch("overbae.api.completions._requests.post", mock_post):
            jwt = _jwt_client(u).post(
                self.URL,
                {"model": "mistralai/mistral-large", "messages": self.MESSAGES},
                format="json",
                HTTP_X_OVERMIND_OPTIMISER="1",
            )
        assert jwt.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_optimiser_bare_model_name_resolves_to_full_slug(self):
        u, p = _user(), _project()
        _membership(u, p)
        fake_resp = {
            "id": "chatcmpl-opt",
            "object": "chat.completion",
            "model": "openai/gpt-5-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        mock_post = MagicMock()
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = fake_resp
        with patch("overbae.api.completions._requests.post", mock_post):
            r = _api_key_client(u, p).post(
                self.URL,
                {"model": "gpt-5-mini", "messages": self.MESSAGES},
                format="json",
                HTTP_X_OVERMIND_OPTIMISER="1",
            )
        assert r.status_code == status.HTTP_200_OK
        assert mock_post.call_args.kwargs["json"]["model"] == "openai/gpt-5-mini"

    def test_bare_model_name_without_optimiser_header_still_404s(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(
            self.URL,
            {"model": "gpt-5-mini", "messages": self.MESSAGES},
            format="json",
        )
        assert r.status_code == status.HTTP_404_NOT_FOUND

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_response_format_is_forwarded_upstream(self):
        u, p = _user(), _project()
        _membership(u, p)
        mock_post = MagicMock()
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {
            "id": "x",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "{}"}}],
        }
        with patch("overbae.api.completions._requests.post", mock_post):
            r = _jwt_client(u).post(
                self.URL,
                {
                    "model": "anthropic/claude-sonnet-5",
                    "messages": self.MESSAGES,
                    "response_format": {"type": "json_object"},
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        assert mock_post.call_args.kwargs["json"]["response_format"] == {"type": "json_object"}

    @pytest.mark.parametrize(
        ("usage", "estimated", "expected_amount"),
        [
            # OpenRouter's own cost wins, so the catalog estimate is never reached.
            ({"prompt_tokens": 10, "completion_tokens": 5, "cost": 1.23e-4}, None, "0.000123"),
            ({"prompt_tokens": 10, "completion_tokens": 5}, 0.0005, "0.0005"),
            # No known price is an honest absence, never a fabricated zero.
            ({"prompt_tokens": 10, "completion_tokens": 5}, None, None),
        ],
    )
    def test_charge_frontier_usage_pricing(self, usage, estimated, expected_amount):
        from overbae.api.completions import _charge_frontier_usage
        from overbae.models import BillingService

        captured: dict = {}
        with (
            patch("overbae.api.completions.estimate_cost", return_value=estimated),
            patch(
                "overbae.api.completions.charge_credits",
                lambda user, amount, service, **kwargs: captured.update(
                    {"amount": amount, "service": service}
                ),
            ),
        ):
            _charge_frontier_usage(_user(), "mistralai/mistral-large", usage)

        if expected_amount is None:
            assert captured == {}
        else:
            assert captured["amount"] == Decimal(expected_amount)
            assert captured["service"] == BillingService.INFERENCE

    def test_non_ready_model_returns_503(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p, status_val=DeployedModel.Status.DEPLOYING)
        r = _jwt_client(u).post(
            self.URL,
            {"model": m.model_id, "messages": self.MESSAGES},
            format="json",
        )
        assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_frontier_model_without_openrouter_key_returns_503(self, settings):
        settings.OPENROUTER_API_KEY = ""
        u, p = _user(), _project()
        _membership(u, p)
        r = _jwt_client(u).post(
            self.URL,
            {"model": "anthropic/claude-sonnet-5", "messages": self.MESSAGES},
            format="json",
        )
        assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_frontier_model_non_stream_proxies_to_openrouter(self):
        u, p = _user(), _project()
        _membership(u, p)
        fake_resp = {
            "id": "chatcmpl-123",
            "object": "chat.completion",
            "model": "anthropic/claude-sonnet-5",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
        mock_post = MagicMock()
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = fake_resp
        with patch("overbae.api.completions._requests.post", mock_post):
            r = _jwt_client(u).post(
                self.URL,
                {"model": "anthropic/claude-sonnet-5", "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["id"] == "chatcmpl-123"

    @override_settings(OPENROUTER_API_KEY="or-test")
    def test_frontier_model_openrouter_error_returns_502(self):
        u, p = _user(), _project()
        _membership(u, p)
        mock_post = MagicMock()
        mock_post.return_value.ok = False
        mock_post.return_value.status_code = 404
        mock_post.return_value.text = "model not found"
        with patch("overbae.api.completions._requests.post", mock_post):
            r = _jwt_client(u).post(
                self.URL,
                {"model": "anthropic/claude-sonnet-5", "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_502_BAD_GATEWAY

    def test_finetuned_model_non_stream_returns_completion(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        fake_completion = {
            "id": "cmpl-ft-1",
            "object": "chat.completion",
            "model": m.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hi!"},
                    "finish_reason": "stop",
                }
            ],
        }
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = fake_completion
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        raw = drain_stream(r)
        assert raw.startswith(b"\n")
        body = json.loads(raw)
        assert body["id"] == "cmpl-ft-1"
        assert mock_client.chat_completions.call_args.kwargs["deployed"] == m

    def test_finetuned_non_stream_error_after_ping_is_json_error_body(self):
        from overbae.services.inference_client import InferenceClientError

        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = MagicMock()
        mock_client.chat_completions.side_effect = InferenceClientError("Modal down")
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        body = json.loads(drain_stream(r))
        assert body["error"]["type"] == "server_error"

    def test_finetuned_non_stream_unexpected_error_after_ping_is_json_error_body(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = MagicMock()
        mock_client.chat_completions.side_effect = RuntimeError("boom")
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        body = json.loads(drain_stream(r))
        assert body["error"]["type"] == "server_error"

    def test_response_format_reaches_the_vllm_backend(self):
        # Without json_object a reasoning model can reply in ``message.reasoning``, content null.
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {
                    "model": m.model_id,
                    "messages": self.MESSAGES,
                    "response_format": {"type": "json_object"},
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        _consume(r)
        assert mock_client.chat_completions.call_args.kwargs["response_format"] == {
            "type": "json_object"
        }

    def test_gpt_oss_defaults_suppress_reasoning(self):
        # Harmony rejects reasoning_effort=none; gateway injects low + hide CoT.
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(
            p,
            model_id="ft-test-gpt-oss-20b",
            base_model_id="openai/gpt-oss-20b",
        )
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        _consume(r)
        kw = mock_client.chat_completions.call_args.kwargs
        assert kw["include_reasoning"] is False
        assert kw["reasoning_effort"] == "low"

    def test_gpt_oss_client_reasoning_overrides_defaults(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(
            p,
            model_id="ft-test-gpt-oss-20b-override",
            base_model_id="openai/gpt-oss-20b",
        )
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {
                    "model": m.model_id,
                    "messages": self.MESSAGES,
                    "include_reasoning": True,
                    "reasoning_effort": "high",
                },
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        _consume(r)
        kw = mock_client.chat_completions.call_args.kwargs
        assert kw["include_reasoning"] is True
        assert kw["reasoning_effort"] == "high"

    def test_muse_defaults_suppress_reasoning(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(
            p,
            model_id="ft-test-muse-glimmer-30b",
            base_model_id="unsloth/Muse-Glimmer-30B",
        )
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        _consume(r)
        kw = mock_client.chat_completions.call_args.kwargs
        assert kw["include_reasoning"] is False
        assert kw["chat_template_kwargs"]["reasoning_strength"] == "low"
        assert "reasoning_effort" not in kw

    def test_finetuned_model_stream_returns_event_stream(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)

        def _fake_stream(**_):
            yield 'data: {"choices": [{"delta": {"content": "Hi"}}]}\n\n'
            yield "data: [DONE]\n\n"

        mock_client = MagicMock()
        mock_client.stream_chat_completions.side_effect = _fake_stream
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES, "stream": True},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        assert "text/event-stream" in r.get("Content-Type", "")

    def _run_forced_usage_stream(self, stream_options=None, **chunk_kwargs):
        """Returns (forwarded SSE payloads, usage handed to billing)."""
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)

        def _fake_stream(**_):
            yield from _forced_usage_chunks(**chunk_kwargs)

        mock_client = MagicMock()
        mock_client.stream_chat_completions.side_effect = _fake_stream
        body = {"model": m.model_id, "messages": self.MESSAGES, "stream": True}
        if stream_options is not None:
            body["stream_options"] = stream_options
        with (
            patch("overbae.api.completions._get_client", return_value=mock_client),
            patch("overbae.api.completions.record_inference_call") as record,
        ):
            r = _jwt_client(u).post(self.URL, body, format="json")
            assert r.status_code == status.HTTP_200_OK
            raw = drain_stream(r).decode()
        assert raw.startswith(": ")
        return _sse_payloads(raw), record.call_args.args[1]

    def test_stream_forwards_deltas_when_usage_is_forced_on_every_chunk(self):
        payloads, billed = self._run_forced_usage_stream()

        content = "".join(c["choices"][0]["delta"]["content"] for c in payloads)
        assert content == "Hello!"
        assert all("usage" not in c for c in payloads)
        assert all("metrics" not in c for c in payloads)
        assert billed == _BILLED_USAGE

    def test_stream_include_usage_exposes_usage_and_bills_the_same(self):
        payloads, billed = self._run_forced_usage_stream({"include_usage": True})

        content = "".join(c["choices"][0]["delta"]["content"] for c in payloads if c.get("choices"))
        assert content == "Hello!"
        assert all("usage" in c for c in payloads)
        assert payloads[-1]["usage"] == _BILLED_USAGE
        # ``metrics`` is ours, never the client's — stripped even when opted in.
        assert all("metrics" not in c for c in payloads)
        assert billed == _BILLED_USAGE

    @pytest.mark.parametrize("omit_terminal_choices", [False, True])
    def test_stream_drops_usage_only_terminal_chunk(self, omit_terminal_choices):
        payloads, billed = self._run_forced_usage_stream(
            omit_terminal_choices=omit_terminal_choices
        )

        assert len(payloads) == 3
        assert all(c["choices"] for c in payloads)
        assert billed == _BILLED_USAGE

    def test_stream_with_no_upstream_chunks_does_not_record(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)

        def _fake_stream(**_):
            yield from ()

        mock_client = MagicMock()
        mock_client.stream_chat_completions.side_effect = _fake_stream
        with (
            patch("overbae.api.completions._get_client", return_value=mock_client),
            patch("overbae.api.completions.record_inference_call") as record,
        ):
            r = _jwt_client(u).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES, "stream": True},
                format="json",
            )
            drain_stream(r)
        record.assert_not_called()

    def test_api_key_auth_accepted(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _api_key_client(u, p).post(
                self.URL,
                {"model": m.model_id, "messages": self.MESSAGES},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK
        _consume(r)


class TestUrlResolution:
    def test_chat_completions_url(self):
        from django.urls import reverse

        assert reverse("v1-chat-completions") == "/api/v1/chat/completions"

    def test_models_list_url(self):
        from django.urls import reverse

        assert reverse("v1-models") == "/api/v1/models"

    def test_model_detail_url(self):
        assert "/api/v1/models/ft-abc123" == "/api/v1/models/ft-abc123"


class TestAgentAliasRouting:
    URL = "/api/v1/chat/completions"
    MESSAGES = [{"role": "user", "content": "Hello"}]

    def _alias(self, capability) -> str:
        return f"overmind/{capability.id}"

    def _mock_client(self):
        mock = MagicMock()
        mock.chat_completions.return_value = {"id": "cmpl-alias", "choices": []}
        return mock

    def _post(self, client, model: str):
        return client.post(self.URL, {"model": model, "messages": self.MESSAGES}, format="json")

    @pytest.mark.parametrize("auth", ["api_key", "jwt"])
    def test_alias_dispatches_concrete_model_id(self, auth):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)
        client = _api_key_client(u, p) if auth == "api_key" else _jwt_client(u)

        mock_client = self._mock_client()
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = self._post(client, self._alias(capability))

        assert r.status_code == status.HTTP_200_OK
        _consume(r)
        assert mock_client.chat_completions.call_args.kwargs["model_id"] == m.model_id

    def test_malformed_uuid_returns_400_naming_shape_and_source(self):
        u, p = _user(), _project()
        _membership(u, p)
        r = self._post(_jwt_client(u), "overmind/not-a-uuid")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        message = r.json()["error"]["message"]
        assert "overmind/<capability-uuid>" in message
        assert "Models tab" in message

    @pytest.mark.parametrize("unroutable", ["absent", "other_project", "soft_deleted"])
    def test_unroutable_capability_is_an_indistinguishable_404(self, unroutable):
        """404 for every miss, never 403 — the alias must not confirm existence."""
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        client = _jwt_client(u)
        if unroutable == "absent":
            model = f"overmind/{uuid.uuid4()}"
        elif unroutable == "other_project":
            # The user is a member of p_b, but this key is pinned to p_a.
            client = _api_key_client(u, p_a)
            model = self._alias(_capability(p_b, active_model=_deployed_model(p_b)))
        else:
            model = self._alias(
                _capability(p_a, active_model=_deployed_model(p_a), status="leftover")
            )

        assert self._post(client, model).status_code == status.HTTP_404_NOT_FOUND

    def test_capability_without_active_model_returns_404_not_frontier_fallback(self):
        u, p = _user(), _project()
        _membership(u, p)
        capability = _capability(p, model="openai/gpt-5.6-sol")  # must NOT be used
        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert "no active model" in r.json()["error"]["message"]

    def test_non_ready_active_model_returns_503_naming_both_ids(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p, status_val=DeployedModel.Status.WARMING)
        capability = _capability(p, active_model=m)

        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        message = r.json()["error"]["message"]
        assert self._alias(capability) in message
        assert m.model_id in message

    def test_active_model_in_another_project_reads_as_no_active_model(self):
        """``active_model`` is an unconstrained FK — a foreign deployment cannot route."""
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        capability = _capability(p_a, active_model=_deployed_model(p_b))

        r = self._post(_jwt_client(u), self._alias(capability))
        assert r.status_code == status.HTTP_404_NOT_FOUND
        assert "no active model" in r.json()["error"]["message"]


class TestAgentAliasOnModelsEndpoints:
    def test_models_list_includes_alias_with_name(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)

        r = _jwt_client(u).get("/api/v1/models")
        rows = {x["id"]: x for x in r.json()["data"]}
        alias = rows[f"overmind/{capability.id}"]
        assert alias["name"] == "Invoice triage"
        assert alias["finetuned"] is True
        assert alias["base_model"] == m.base_model_id

    def test_models_list_under_pinned_key_omits_other_project(self):
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        capability_a = _capability(p_a, active_model=_deployed_model(p_a))
        m_b = _deployed_model(p_b)
        capability_b = _capability(p_b, active_model=m_b)

        ids = {x["id"] for x in _api_key_client(u, p_a).get("/api/v1/models").json()["data"]}
        assert f"overmind/{capability_a.id}" in ids
        assert f"overmind/{capability_b.id}" not in ids
        assert m_b.model_id not in ids

    def test_models_list_omits_an_alias_pointing_into_another_project(self):
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        capability = _capability(p_a, active_model=_deployed_model(p_b))

        ids = {x["id"] for x in _jwt_client(u).get("/api/v1/models").json()["data"]}
        assert f"overmind/{capability.id}" not in ids

    def test_alias_detail_get_returns_concrete_deployment(self):
        u, p = _user(), _project()
        _membership(u, p)
        m = _deployed_model(p)
        capability = _capability(p, active_model=m)

        r = _jwt_client(u).get(f"/api/v1/models/overmind/{capability.id}")
        assert r.status_code == status.HTTP_200_OK
        assert r.json()["id"] == m.model_id

    def test_alias_detail_delete_returns_400(self):
        u, p = _user(), _project()
        _membership(u, p)
        capability = _capability(p, active_model=_deployed_model(p))

        r = _jwt_client(u).delete(f"/api/v1/models/overmind/{capability.id}")
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot be deleted" in r.json()["error"]["message"]


class TestApiKeyProjectScoping:
    def test_concrete_model_from_other_project_returns_404_under_pinned_key(self):
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        m_b = _deployed_model(p_b)

        mock_client = MagicMock()
        mock_client.chat_completions.return_value = {"id": "x", "choices": []}
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _api_key_client(u, p_a).post(
                "/api/v1/chat/completions",
                {"model": m_b.model_id, "messages": [{"role": "user", "content": "hi"}]},
                format="json",
            )
        assert r.status_code == status.HTTP_404_NOT_FOUND
        # The membership fan-out still applies when no key pins the request.
        with patch("overbae.api.completions._get_client", return_value=mock_client):
            r = _jwt_client(u).post(
                "/api/v1/chat/completions",
                {"model": m_b.model_id, "messages": [{"role": "user", "content": "hi"}]},
                format="json",
            )
        assert r.status_code == status.HTTP_200_OK

    def test_model_detail_from_other_project_returns_404_under_pinned_key(self):
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        m_b = _deployed_model(p_b)

        r = _api_key_client(u, p_a).get(f"/api/v1/models/{m_b.model_id}")
        assert r.status_code == status.HTTP_404_NOT_FOUND


class TestActiveModelValidation:
    def _url(self, capability) -> str:
        return f"/api/capabilities/{capability.id}/"

    def test_active_model_from_another_project_is_rejected(self):
        u, p_a, p_b = _user(), _project(), _project()
        _membership(u, p_a)
        _membership(u, p_b)
        capability = _capability(p_a)
        foreign = _deployed_model(p_b)

        r = _jwt_client(u).patch(
            self._url(capability), {"active_model": str(foreign.id)}, format="json"
        )
        assert r.status_code == status.HTTP_400_BAD_REQUEST
        capability.refresh_from_db()
        assert capability.active_model_id is None

    def test_active_model_owned_by_a_sibling_capability_is_allowed(self):
        """Cross-capability sharing inside one project is intentional (no same-capability rule)."""
        from overbae.models import FinetuningJob

        u, p = _user(), _project()
        _membership(u, p)
        sibling = _capability(p, slug=f"sib-{uuid.uuid4().hex[:6]}")
        capability = _capability(p)
        m = _deployed_model(p)
        m.finetuning_job = FinetuningJob.objects.create(
            project=p,
            capability=sibling,
            dataset=frozen_dataset(p, TRAIN_ROWS, name="t"),
            base_model="Qwen/Qwen3-8B",
            provider=FinetuningJob.Provider.BASETEN,
        )
        m.save(update_fields=["finetuning_job"])

        r = _jwt_client(u).patch(self._url(capability), {"active_model": str(m.id)}, format="json")
        assert r.status_code == status.HTTP_200_OK
        capability.refresh_from_db()
        assert capability.active_model_id == m.id


class TestBaselineCoupling:
    """The eval baseline must follow the routed deployment, not the stale text field."""

    def _job(self, capability, project):
        from overbae.models import FinetuningJob

        return FinetuningJob.objects.create(
            project=project,
            capability=capability,
            dataset=frozen_dataset(project, TRAIN_ROWS, name="t"),
            base_model="Qwen/Qwen3-8B",
            provider=FinetuningJob.Provider.BASETEN,
            baseline_model="",  # not snapshotted yet → resolve live
        )

    def test_active_model_outranks_capability_model_field(self):
        from overbae.services.finetuning_eval import resolve_baseline_model

        p = _project()
        m = _deployed_model(p, model_id="ft-live-qwen3-8b")
        capability = _capability(p, active_model=m, model="openai/gpt-5.6-sol")
        assert resolve_baseline_model(self._job(capability, p)) == "ft-live-qwen3-8b"

    def test_falls_back_to_capability_model_when_no_active_model(self):
        from overbae.services.finetuning_eval import resolve_baseline_model

        p = _project()
        capability = _capability(p, model="openai/gpt-5.6-sol")
        assert resolve_baseline_model(self._job(capability, p)) == "openai/gpt-5.6-sol"

    @override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
    def test_non_ready_self_hosted_incumbent_defers_instead_of_hitting_openrouter(self):
        from overbae.services.finetuning_eval import _baseline_target

        p = _project()
        m = _deployed_model(p, model_id="ft-warming-qwen3-8b")
        m.status = DeployedModel.Status.WARMING
        m.save(update_fields=["status"])
        capability = _capability(p, active_model=m)

        target = _baseline_target(self._job(capability, p))
        assert target.kind == "gateway"
        assert target.ready is False
        assert target.model_id == "ft-warming-qwen3-8b"
