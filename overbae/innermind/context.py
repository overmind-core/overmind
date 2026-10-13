"""Advisory context and cost estimates for a run's generation model and its
explanation judge. Warnings never change a selection or block a launch."""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field

from django.core.cache import cache

from overbae.core.llms import effective_max_tokens
from overbae.core.model_registry import (
    TaskType,
    inference_models,
    judge_picker_models,
    openrouter_configured,
    pricing_slug,
    resolve_model,
)
from overbae.innermind.evals import EXPLAIN_MAX_TOKENS, EXPLAIN_PROMPT_TOKENS
from overbae.models import EvalMatrix, EvalRun, ModelRef
from overbae.services.datasets.alignment import system_prompt
from overbae.services.datasets.rows import RowStoreError, iter_rows
from overbae.services.llm_context import (
    ModelLimits,
    assess_context,
    estimate_input_tokens,
    model_limits,
)
from overbae.services.model_catalog import fetch_model_catalog

logger = logging.getLogger(__name__)
CACHE_SECONDS = 300


def budget_cost(entry: dict, inputs: list[int], output_tokens: int) -> float | None:
    return token_cost(entry, sum(inputs), len(inputs) * output_tokens) if inputs else None


def token_cost(entry: dict, input_tokens: int, output_tokens: int) -> float | None:
    rates = [entry.get("prompt_price"), entry.get("completion_price")]
    if any(
        not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0 for rate in rates
    ):
        return None
    return round((input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000, 6)


def judge_model_options(checks: list[dict]) -> list[dict]:
    judges = [check for check in checks if check["role"] == "judge"]
    if not judges:
        return []
    catalog, available = fetch_model_catalog()
    entries = {entry["id"]: entry for entry in catalog}
    current_costs = [check.get("estimated_cost_usd") for check in judges]
    current_cost = sum(current_costs) if all(cost is not None for cost in current_costs) else None
    options = []
    for name in judge_picker_models():
        entry = entries.get(pricing_slug(name), {})
        context = entry.get("context_length")
        limit = entry.get("max_completion_tokens")
        output = effective_max_tokens(name)
        known = (
            available
            and isinstance(context, int)
            and isinstance(limit, int)
            and all(check["checked_rows"] for check in judges)
        )
        exceeds = known and (
            output > limit
            or any(check["estimated_input_tokens"] + output > context for check in judges)
        )
        schema_supported = bool(
            {"response_format", "structured_outputs"}.intersection(
                entry.get("supported_parameters") or []
            )
        )
        status = "warning" if exceeds else "fits" if known and schema_supported else "unknown"
        costs = [
            token_cost(
                entry,
                check["total_input_tokens"],
                check["checked_rows"] * output,
            )
            for check in judges
        ]
        cost = round(sum(costs), 6) if known and all(value is not None for value in costs) else None
        options.append(
            {
                "model": name,
                "name": entry.get("name") or name,
                "status": status,
                "context_window": context,
                "max_output_tokens": limit,
                "reserved_output_tokens": output,
                "estimated_cost_usd": cost,
                "cost_delta_usd": round(cost - current_cost, 6)
                if cost is not None and current_cost is not None
                else None,
            }
        )
    return options


def suggest_models(
    check: dict,
    *,
    inputs: list[int],
    uses_tools: bool,
    custom: bool = False,
    variant_models: list[str] | None = None,
) -> dict:
    result = {
        "estimated_cost_usd": None,
        "cost_basis": (
            "USD at published token rates, using all checked rows and the full reserved output "
            "for one dataset pass per model or judge. Excludes retries, extra tool turns, "
            "caching, hosting and training. Actual usage and provider rates may differ."
        ),
        "suggestions": [],
        "suggestion_note": "",
    }
    catalog, available = fetch_model_catalog()
    entries = {entry["id"]: entry for entry in catalog}
    current = entries.get(pricing_slug(check["model"]), {}) if not custom else {}
    result["estimated_cost_usd"] = budget_cost(current, inputs, check["reserved_output_tokens"])
    if check["status"] == "fits":
        return result
    if not inputs or not available or not openrouter_configured():
        result["suggestion_note"] = (
            "Model alternatives are unavailable; the selection is unchanged."
        )
        return result

    is_judge = check["role"] == "judge"
    names = judge_picker_models() if is_judge else [model.name for model in inference_models()]
    avoid = {
        slug.split("/", 1)[0] for model in (variant_models or []) if (slug := pricing_slug(model))
    }
    for name in names:
        slug = pricing_slug(name)
        if (
            not slug
            or slug == pricing_slug(check["model"])
            or (is_judge and slug.split("/", 1)[0] in avoid)
        ):
            continue
        entry = entries.get(slug, {})
        parameters = entry.get("supported_parameters") or []
        if uses_tools and "tools" not in parameters:
            continue
        if is_judge and not {"response_format", "structured_outputs"}.intersection(parameters):
            continue
        context = entry.get("context_length")
        output_limit = entry.get("max_completion_tokens")
        output = max(check["reserved_output_tokens"], effective_max_tokens(name))
        if (
            not isinstance(context, int)
            or not isinstance(output_limit, int)
            or output_limit < output
            or context < max(inputs) + output
        ):
            continue
        cost = budget_cost(entry, inputs, output)
        current_cost = result["estimated_cost_usd"]
        result["suggestions"].append(
            {
                "model": name,
                "name": entry.get("name") or name,
                "context_window": context,
                "max_output_tokens": output_limit,
                "reserved_output_tokens": output,
                "estimated_cost_usd": cost,
                "cost_delta_usd": round(cost - current_cost, 6)
                if cost is not None and current_cost is not None
                else None,
            }
        )
        # Registry order is deliberate; cheaper pricing is not evidence of a better model.
        if len(result["suggestions"]) == 3:
            break
    result["suggestion_note"] = (
        (
            "Context-fitting options in configured model order, not a quality prediction. "
            "Use the same judge on both sides of a comparison."
            if is_judge
            else "Context-fitting evaluation alternatives, not a quality prediction. "
            "Changing the evaluated model changes the benchmark."
        )
        if result["suggestions"]
        else "No compatible alternative with verified limits was found in the configured model list."
    )
    return result


@dataclass
class Target:
    model: str
    label: str
    role: str
    limits: ModelLimits
    output_tokens: int
    configured_model: str = ""
    system_prompt: str = ""
    custom: bool = False
    uses_tools: bool = False
    inputs: list[int] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)


def generation_target(target: dict, *, project_id, capability) -> Target | None:
    if target.get("mode", EvalRun.Mode.GENERATE) != EvalRun.Mode.GENERATE:
        return None
    ref = target.get("model_ref")
    if ref and not isinstance(ref, ModelRef):
        ref = ModelRef.objects.filter(pk=ref, project_id=project_id).first()
    model = ref.model_id if ref else target.get("model_name", "")
    custom = bool(ref and ref.provider == "custom")
    params = target.get("params") or {}
    return Target(
        model=model,
        label=target.get("label") or model,
        role="generation",
        limits=model_limits(model, project_id=project_id, custom=custom),
        output_tokens=target.get("output_tokens")
        or ((ref.params or {}).get("max_tokens") if ref else None)
        or effective_max_tokens(model),
        system_prompt=params.get("system_prompt", system_prompt(capability) if capability else ""),
        custom=custom,
    )


def judge_target(judge_model: str, *, project_id) -> Target:
    configured = resolve_model(TaskType.EVAL_EXPLANATION)
    model = judge_model or configured
    return Target(
        model=model,
        label="Explanation judge",
        role="judge",
        limits=model_limits(model, project_id=project_id),
        output_tokens=EXPLAIN_MAX_TOKENS,
        configured_model=configured,
    )


def as_text(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


def check_context(
    *,
    dataset,
    cell,
    targets: list[dict],
    has_llm_rows: bool,
    capability=None,
    judge_model: str = "",
) -> list[dict]:
    """One check per generation model, plus the explanation judge when the matrix has
    llm rows. The judge reads the row and an answer of reference size."""
    if cell is None:
        return []
    project_id = dataset.project_id
    capability = capability or dataset.capability
    checked = [
        found
        for target in targets
        if (found := generation_target(target, project_id=project_id, capability=capability))
    ]
    if has_llm_rows:
        checked.append(judge_target(judge_model, project_id=project_id))
    if not checked:
        return []
    try:
        for row in iter_rows(cell):
            tools = row.extra.get("tools", [])
            for target in checked:
                if target.role == "generation":
                    target.uses_tools = target.uses_tools or bool(tools)
                    inputs = {
                        "input": row.input,
                        "tools": tools,
                        "system_prompt": target.system_prompt,
                    }
                    target.inputs.append(estimate_input_tokens(inputs))
                else:
                    answer = as_text(row.expected_output)
                    target.inputs.append(
                        estimate_input_tokens([as_text(row.input), answer, answer])
                        + EXPLAIN_PROMPT_TOKENS
                    )
                target.indices.append(row.index)
    except (RowStoreError, OSError, ValueError):
        logger.warning(
            "Evaluation context estimate unavailable for cell %s", cell.pk, exc_info=True
        )
        for target in checked:
            target.inputs.clear()
            target.indices.clear()

    models = [target.model for target in checked if target.role == "generation"]
    checks = []
    for target in checked:
        check = assess_context(
            model=target.model,
            inputs=target.inputs,
            output_tokens=target.output_tokens,
            limits=target.limits,
            role=target.role,
            label=target.label,
            row_indices=target.indices,
        )
        check.update(
            suggest_models(
                check,
                inputs=target.inputs,
                uses_tools=target.uses_tools,
                custom=target.custom,
                variant_models=models,
            )
        )
        check["total_input_tokens"] = sum(target.inputs)
        check["configured_model"] = target.configured_model or target.model
        checks.append(check)
    return checks


def run_context_checks(run: EvalRun) -> list[dict]:
    if not run.dataset_id or not run.cell_id:
        return []
    key = f"eval_context:{run.pk}:{run.updated_at.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    checks = check_context(
        dataset=run.dataset,
        cell=run.cell,
        targets=[
            {
                "mode": run.mode,
                "model_ref": run.model_ref,
                "model_name": run.model_name,
                "label": run.label,
                "params": run.params,
            }
        ],
        has_llm_rows=any(row.get("type") == EvalMatrix.Type.LLM for row in run.matrix),
        judge_model=run.judge_model,
    )
    cache.set(key, checks, CACHE_SECONDS)
    return checks
