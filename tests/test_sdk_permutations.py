"""SDK routing permutations — how each capability SDK names models vs what the
platform's routing chain (gateway resolution, telemetry comparison, model
validation) accepts.

Each section mirrors one real-world way people use their models:
- bare hardcoded names (plain openai, langchain, smolagents, crewai)
- full ``provider/slug`` names (openai-agents — the ``Unknown prefix`` trap)
- ``openrouter/``-namespaced slugs, ``ft-…`` deployment ids, colon forms
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from overbae.models import (
    Capability,
    DeployedModel,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    User,
)
from overbae.models.optimizer import _model_ids_match
from overbae.services.model_catalog import resolve_bare_openrouter_slug

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_model_catalog_cache():
    cache.delete("openrouter_model_catalog_v1")
    yield
    cache.delete("openrouter_model_catalog_v1")


URL = "/api/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Hello"}]

FAKE_RESP = {
    "id": "chatcmpl-opt",
    "object": "chat.completion",
    "model": "x",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "Hi!"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}

SDK_DEPENDENCIES = {
    "openai-sdk": "openai>=1.40\n",
    "openai-agents": "openai-agents>=0.19\nopenai>=1.40\n",
    "claude-agent-sdk": "claude-agent-sdk>=2.0\n",
    "google-adk": "google-adk>=1.0\n",
    "langgraph": "langgraph>=0.4\nlangchain-openai>=0.3\n",
    "smolagents": "smolagents>=1.0\n",
    "crewai": "crewai>=0.80\n",
}


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _membership(user: User, project: Project) -> None:
    ProjectMembership.objects.create(user=user, project=project)


def _api_key_client(user: User, project: Project) -> APIClient:
    from overbae.models import APIToken

    raw_key, _ = APIToken.create_for_user(user, project=project)
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw_key)
    return client


def _post(model_id: str, *, optimiser_header: bool = True) -> tuple[int, str]:
    u, p = _user(), _project()
    _membership(u, p)
    mock_post = MagicMock()
    mock_post.return_value.ok = True
    mock_post.return_value.json.return_value = FAKE_RESP
    headers = {}
    if optimiser_header:
        headers["HTTP_X_OVERMIND_OPTIMISER"] = "1"
    with (
        override_settings(OPENROUTER_API_KEY="or-test"),
        patch("overbae.api.completions._requests.post", mock_post),
    ):
        r = _api_key_client(u, p).post(URL, {"model": model_id, "messages": MESSAGES}, **headers)
        if r.status_code == 200:
            return r.status_code, mock_post.call_args.kwargs["json"]["model"]
        return r.status_code, r.json().get("error", {}).get("message", "")


def _catalog_with(*ids: str) -> dict:
    return {
        "data": [
            {"id": i, "name": i, "architecture": {"modality": "text->text"}, "pricing": {}}
            for i in ids
        ]
    }


class TestGatewayModelNamePermutations:
    """A1b: the gateway must canonicalize bare names, pass full slugs through
    verbatim, and leave unknown/ft/colon forms to the existing 404 path."""

    def test_bare_curated_name_resolves_via_slug_map(self):
        status_code, forwarded = _post("gpt-5-mini")
        assert status_code == 200
        assert forwarded == "openai/gpt-5-mini"

    def test_bare_catalog_only_name_resolves_via_catalog(self):
        with patch(
            "overbae.services.model_catalog.requests.get",
            return_value=MagicMock(
                status_code=200,
                json=MagicMock(return_value=_catalog_with("deepseek/deepseek-v4-flash")),
            ),
        ):
            status_code, forwarded = _post("deepseek-v4-flash")
        assert status_code == 200
        assert forwarded == "deepseek/deepseek-v4-flash"

    def test_full_slug_forwarded_verbatim(self):
        status_code, forwarded = _post("deepseek/deepseek-v4-flash")
        assert status_code == 200
        assert forwarded == "deepseek/deepseek-v4-flash"

    def test_openrouter_namespaced_slug_stripped_before_forwarding(self):
        # ``openrouter/`` is a platform-side alias namespace, not an id OpenRouter
        # serves — forwarding it verbatim 502s upstream.
        status_code, forwarded = _post("openrouter/openai/gpt-5-mini")
        assert status_code == 200
        assert forwarded == "openai/gpt-5-mini"

    def test_unknown_bare_name_still_404s(self):
        status_code, message = _post("no-such-model")
        assert status_code == 404
        assert "no-such-model" in message

    def test_colon_form_still_404s(self):
        status_code, message = _post("openai:gpt-5-mini")
        assert status_code == 404
        assert "openai:gpt-5-mini" in message

    def test_bare_name_without_optimiser_header_still_404s(self):
        status_code, message = _post("gpt-5-mini", optimiser_header=False)
        assert status_code == 404
        assert "gpt-5-mini" in message

    def test_ft_id_never_slug_resolved(self):
        assert resolve_bare_openrouter_slug("ft-deadbeef") is None
        status_code, message = _post("ft-deadbeef")
        assert status_code == 404
        assert "ft-deadbeef" in message  # untouched by the resolver

    def test_bare_name_billing_charges_resolved_slug(self):
        """Billing runs after A1b rebinds the model id, so the ledger metadata
        must carry the canonical slug — not the bare name the client sent."""
        from decimal import Decimal

        from overbae.models import BillingService

        u, p = _user(), _project()
        _membership(u, p)
        mock_post = MagicMock()
        mock_post.return_value.ok = True
        mock_post.return_value.json.return_value = {
            **FAKE_RESP,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        captured: dict = {}
        with (
            override_settings(OPENROUTER_API_KEY="or-test"),
            patch("overbae.api.completions._requests.post", mock_post),
            patch("overbae.api.completions.estimate_cost", return_value=Decimal("0.0005")),
            patch(
                "overbae.api.completions.charge_credits",
                lambda user, amount, service, **kwargs: captured.update(
                    {"amount": amount, "service": service, "metadata": kwargs["metadata"]}
                ),
            ),
        ):
            r = _api_key_client(u, p).post(
                URL,
                {"model": "gpt-5-mini", "messages": MESSAGES},
                HTTP_X_OVERMIND_OPTIMISER="1",
            )

        assert r.status_code == 200
        assert captured["service"] == BillingService.INFERENCE
        assert captured["metadata"]["model_id"] == "openai/gpt-5-mini"
        assert captured["amount"] == Decimal("0.0005")


class TestTelemetryNormalisationPermutations:
    """C1: final-path-segment comparison must accept every provider/slug form
    an SDK may echo while still rejecting genuinely different models."""

    @pytest.mark.parametrize(
        ("expected", "observed"),
        [
            ("openai/gpt-5-mini", "gpt-5-mini"),
            ("qwen/qwen3-14b", "qwen3-14b"),
            ("openrouter/qwen/qwen3-14b", "qwen3-14b"),
            ("deepseek/deepseek-v4-flash", "DeepSeek/DeepSeek-V4-Flash"),
            ("deepseek/deepseek-v4-flash", "deepseek-v4-flash"),
            ("ft-abc", "ft-abc"),
        ],
    )
    def test_forms_compare_equal(self, expected: str, observed: str):
        assert _model_ids_match(expected, observed)

    @pytest.mark.parametrize(
        ("expected", "observed"),
        [
            ("openai/gpt-5-mini", "anthropic/claude-sonnet-5"),
            ("openai/gpt-5-mini", "anthropic/gpt-5-mini"),
            ("qwen/qwen3-14b", "deepseek/deepseek-v4-flash"),
            ("openai/gpt-5-mini", "openai/gpt-5.4"),
            ("ft-abc", "ft-def"),
        ],
    )
    def test_different_models_still_differ(self, expected: str, observed: str):
        assert not _model_ids_match(expected, observed)

    def test_bare_observed_model_does_not_fail_the_telemetry_gate(self):
        project = _project()
        capability = Capability.objects.create(
            project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
        )
        experiment = OptimizerExperiment.objects.create(
            project=project,
            capability=capability,
            mode=OptimizerExperiment.Mode.MODEL_COMPARISON,
            model_ids=["qwen/qwen3-14b"],
            entrypoint="run",
            code_trigger="run(**datapoint)",
            status=OptimizerExperiment.Status.SCHEDULED,
        )
        iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
        candidate = OptimizerCandidate.objects.create(
            experiment=experiment,
            iteration=iteration,
            candidate_index=0,
            target_model="qwen/qwen3-14b",
            status=OptimizerCandidate.Status.RUNNING_COMMANDS,
        )
        command = OptimizerCommand.objects.create(
            experiment=experiment,
            candidate=candidate,
            iteration=iteration,
            datapoint_index=0,
            status=OptimizerCommand.Status.RAN,
        )
        assert (
            command._telemetry_error(
                {
                    "output": "answer",
                    "trace_id": "trace-1",
                    "telemetry": {"provider": "qwen", "model": "qwen3-14b"},
                }
            )
            == ""
        )


class TestModelValidationPermutations:
    """A2: stored ids must be canonical — ``openrouter/`` namespaces stripped,
    full slugs kept, ft ids never touched, bare names rejected up front."""

    def test_openrouter_namespaced_slug_stored_canonical(self):
        from overbae.services.optimizer_create import validate_optimizer_models

        stored = validate_optimizer_models(
            OptimizerExperiment.Mode.MODEL_COMPARISON, ["openrouter/openai/gpt-5-mini"]
        )
        assert stored == ["openai/gpt-5-mini"]

    def test_full_slug_stored_verbatim(self):
        from overbae.services.optimizer_create import validate_optimizer_models

        stored = validate_optimizer_models(
            OptimizerExperiment.Mode.MODEL_COMPARISON, ["openai/gpt-5-mini"]
        )
        assert stored == ["openai/gpt-5-mini"]

    def test_provider_aliases_cannot_duplicate_one_model(self):
        from rest_framework.exceptions import ValidationError

        from overbae.services.optimizer_create import validate_optimizer_models

        with pytest.raises(ValidationError, match="Duplicate"):
            validate_optimizer_models(
                OptimizerExperiment.Mode.MODEL_COMPARISON,
                ["openai/gpt-5-mini", "openrouter/openai/gpt-5-mini"],
            )

    def test_bare_name_rejected_before_storage(self):
        from rest_framework.exceptions import ValidationError

        from overbae.services.optimizer_create import validate_optimizer_models

        with pytest.raises(ValidationError):
            validate_optimizer_models(OptimizerExperiment.Mode.MODEL_COMPARISON, ["gpt-5-mini"])

    def test_ft_id_stored_verbatim(self):
        from overbae.services.optimizer_create import validate_optimizer_models

        project = _project()
        DeployedModel.objects.create(
            project=project,
            model_id="ft-nimbus-1234",
            status=DeployedModel.Status.READY,
            base_model_id="qwen/qwen3-14b",
        )
        stored = validate_optimizer_models(
            OptimizerExperiment.Mode.MODEL_COMPARISON, ["ft-nimbus-1234"], project=project
        )
        assert stored == ["ft-nimbus-1234"]
