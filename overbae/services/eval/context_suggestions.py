from __future__ import annotations

import math

from overbae.core.llms import effective_max_tokens
from overbae.core.model_registry import (
    inference_models,
    judge_picker_models,
    openrouter_configured,
    pricing_slug,
)
from overbae.services.model_catalog import fetch_model_catalog


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
