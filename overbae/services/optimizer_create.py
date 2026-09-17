"""Shared optimizer experiment creation — REST viewset and MCP tools.

Every create door goes through here so the defaults, dataset gate and billing
gates stay identical. The client drives execution; create only stores meta.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework.exceptions import ValidationError

from overbae.api.credit_gate import require_credits
from overbae.core.model_registry import pricing_slug
from overbae.models import DeployedModel, OptimizerExperiment
from overbae.models.optimizer import optimizer_dataset_error
from overbae.services.model_catalog import (
    is_model_available,
)
from overbae.services.plan_limits import require_plan_quota


def validate_openrouter_key_source(source: str | None) -> str:
    """Validate the credential trust boundary. Persisted on every experiment for the
    audit trail, though only model-bearing runs use OpenRouter credentials.
    """
    source = source or OptimizerExperiment.OpenRouterKeySource.PLATFORM
    if source not in OptimizerExperiment.OpenRouterKeySource.values:
        raise ValidationError({"openrouter_key_source": "Choose Overmind credits or local .env."})
    return source


def _is_finetuned_reference(model_id: str) -> bool:
    """True for a bare ``ft-…`` id; OpenRouter slugs always carry a ``provider/model``
    slash. The ``overmind/<capability-uuid>`` alias is deliberately not a valid optimiser
    reference — it chases the capability's active model, so a comparison could run a
    different model than the one picked.
    """
    return "/" not in model_id


def _finetuned_reference_routable(model_id: str, project) -> bool:
    """READY deployment inside ``project`` — the resolution the chat gateway performs."""
    return (
        DeployedModel.objects.filter(project=project, model_id=model_id)
        .filter(status=DeployedModel.Status.READY)
        .exists()
    )


def validate_optimizer_models(
    mode: str,
    model_ids,
    project=None,
    openrouter_key_source: str | None = None,
) -> list[str]:
    """Accepts OpenRouter slugs from the live catalog plus ``ft-…`` ids for the
    project's own READY fine-tunes. Fine-tuned references route through Overmind's
    gateway only, so they are rejected when the run uses a local OpenRouter key.
    """
    model_ids = [] if model_ids is None else model_ids
    if mode == OptimizerExperiment.Mode.OPTIMIZE:
        if model_ids:
            raise ValidationError({"model_ids": "Normal optimization runs cannot select models."})
        return []
    if mode not in {
        OptimizerExperiment.Mode.MODEL_COMPARISON,
        OptimizerExperiment.Mode.HYBRID,
    }:
        raise ValidationError({"mode": "Unsupported optimizer mode."})
    if not isinstance(model_ids, list):
        raise ValidationError({"model_ids": "Expected a list of model slugs."})
    if not 1 <= len(model_ids) <= 5:
        raise ValidationError({"model_ids": "Select between 1 and 5 models."})
    if any(
        not isinstance(model_id, str) or not model_id or model_id != model_id.strip()
        for model_id in model_ids
    ):
        raise ValidationError({"model_ids": "Every model must be a non-empty model slug."})
    canonical_ids = [
        model_id if _is_finetuned_reference(model_id) else pricing_slug(model_id) or model_id
        for model_id in model_ids
    ]
    if len(set(canonical_ids)) != len(canonical_ids):
        raise ValidationError({"model_ids": "Duplicate models are not allowed."})
    if (
        any(_is_finetuned_reference(model_id) for model_id in model_ids)
        and openrouter_key_source == OptimizerExperiment.OpenRouterKeySource.LOCAL
    ):
        raise ValidationError(
            {
                "model_ids": (
                    "Fine-tuned models route through Overmind's model gateway — "
                    "select Overmind credits in the Model access step."
                )
            }
        )
    unavailable = [
        model_id
        for model_id in model_ids
        if not (
            is_model_available(model_id)
            if not _is_finetuned_reference(model_id)
            else project is not None and _finetuned_reference_routable(model_id, project)
        )
    ]
    if unavailable:
        raise ValidationError({"model_ids": f"Unavailable model: {', '.join(unavailable)}."})
    return canonical_ids


def create_optimizer_experiment(
    *,
    user,
    capability,
    dataset=None,
    cell=None,
    eval_set=None,
    entrypoint: str = "",
    code_trigger: str = "",
    num_iterations: int = 5,
    num_candidates_per_iteration: int = 3,
    max_iterations_without_improvement: int = 3,
    initial_state: dict | None = None,
    mode: str = OptimizerExperiment.Mode.OPTIMIZE,
    model_ids: list[str] | None = None,
    openrouter_key_source: str = OptimizerExperiment.OpenRouterKeySource.PLATFORM,
) -> OptimizerExperiment:
    openrouter_key_source = validate_openrouter_key_source(openrouter_key_source)
    model_ids = validate_optimizer_models(
        mode, model_ids, project=capability.project, openrouter_key_source=openrouter_key_source
    )
    if mode == OptimizerExperiment.Mode.MODEL_COMPARISON:
        # One iteration per model so the table reads as a per-model leaderboard.
        num_iterations = len(model_ids)
        num_candidates_per_iteration = 1
        max_iterations_without_improvement = 0

    if error := optimizer_dataset_error(capability, dataset, cell):
        raise ValidationError({"dataset": error})
    eval_set = eval_set or capability.active_eval_set
    if eval_set is not None and eval_set.capability_id != capability.id:
        raise ValidationError({"eval_set": "Eval set does not belong to this capability."})
    entrypoint = entrypoint or capability.entrypoint_fn

    if user is not None:
        require_credits(user)
        require_plan_quota(user, "optimize_runs")

    # "Overmind credits" proxies to OpenRouter with the server key, so fail fast when
    # it is unset rather than 503ing mid-run. A local run uses only the client's
    # own key, which the server never sees.
    if (
        mode in {OptimizerExperiment.Mode.MODEL_COMPARISON, OptimizerExperiment.Mode.HYBRID}
        and openrouter_key_source == OptimizerExperiment.OpenRouterKeySource.PLATFORM
        and not getattr(settings, "OPENROUTER_API_KEY", "")
    ):
        raise ValidationError(
            {
                "openrouter": (
                    "Overmind's model gateway is not configured on this server. "
                    "Choose local .env instead."
                )
            }
        )

    from overbae.services.datasets import use  # noqa: PLC0415 — avoid import cycle

    return OptimizerExperiment.objects.create(
        project=capability.project,
        capability=capability,
        dataset=dataset,
        cell=use.use(dataset, "eval", cell=cell),
        eval_set=eval_set,
        entrypoint=entrypoint,
        code_trigger=code_trigger,
        mode=mode,
        model_ids=model_ids,
        openrouter_key_source=openrouter_key_source,
        num_iterations=num_iterations,
        num_candidates_per_iteration=num_candidates_per_iteration,
        max_iterations_without_improvement=max_iterations_without_improvement,
        triggered_by=user,
        status=OptimizerExperiment.Status.SCHEDULED,
        state=initial_state or {},
        scores={},
        current_iteration=0,
    )
