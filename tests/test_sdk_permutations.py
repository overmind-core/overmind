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
from decimal import Decimal

import pytest
from factories import api_key_client, make_member, make_project, make_user

from overbae.models import (
    Capability,
    DeployedModel,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
)
from overbae.models.optimizer import _model_ids_match

pytestmark = pytest.mark.django_db


URL = "/api/v1/chat/completions"
MESSAGES = [{"role": "user", "content": "Hello"}]


@pytest.fixture
def gateway(fake_llm, settings):
    settings.OPENROUTER_API_KEY = "or-test"

    def post(model_id: str, *, optimiser_header: bool = True) -> tuple[int, str]:
        user, project = make_user(), make_project()
        make_member(user, project)
        headers = {"HTTP_X_OVERMIND_OPTIMISER": "1"} if optimiser_header else {}
        before = len(fake_llm.requests)
        r = api_key_client(user, project).post(
            URL, {"model": model_id, "messages": MESSAGES}, format="json", **headers
        )
        if r.status_code == 200:
            return r.status_code, fake_llm.requests[before].model
        return r.status_code, r.json().get("error", {}).get("message", "")

    return post


class TestGatewayModelNamePermutations:
    @pytest.mark.parametrize(
        ("sent", "forwarded"),
        [
            ("gpt-5-mini", "openai/gpt-5-mini"),
            ("deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
            ("deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-flash"),
            ("openrouter/openai/gpt-5-mini", "openai/gpt-5-mini"),
        ],
        ids=["curated bare name", "catalog-only bare name", "full slug", "openrouter namespace"],
    )
    def test_the_optimiser_names_a_model_the_way_its_sdk_does(
        self, gateway, fake_llm, sent, forwarded
    ):
        fake_llm.extra_models.append("deepseek/deepseek-v4-flash")
        assert gateway(sent) == (200, forwarded)

    @pytest.mark.parametrize(
        ("sent", "header"),
        [
            ("no-such-model", True),
            ("openai:gpt-5-mini", True),
            ("gpt-5-mini", False),
            ("ft-deadbeef", True),
        ],
        ids=["unknown", "colon form", "no optimiser header", "ft id"],
    )
    def test_a_name_the_gateway_cannot_route_is_not_found_and_named(self, gateway, sent, header):
        status_code, message = gateway(sent, optimiser_header=header)
        assert status_code == 404
        assert sent in message

    def test_a_bare_name_is_billed_under_its_resolved_slug(self, fake_llm, settings):
        from overbae.models import BillingService, BillingTelemetry

        settings.OPENROUTER_API_KEY = "or-test"
        fake_llm.on(
            lambda r: r.model == "openai/gpt-5-mini",
            {"content": "Hi!", "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
        )
        user, project = make_user(), make_project()
        make_member(user, project)
        r = api_key_client(user, project).post(
            URL,
            {"model": "gpt-5-mini", "messages": MESSAGES},
            format="json",
            HTTP_X_OVERMIND_OPTIMISER="1",
        )

        assert r.status_code == 200
        [charge] = BillingTelemetry.objects.filter(user=user, service=BillingService.INFERENCE)
        assert charge.metadata["model_id"] == "openai/gpt-5-mini"
        assert -charge.amount == Decimal("0.00002")


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
        project = make_project()
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

        project = make_project()
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
