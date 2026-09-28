from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any

from overbae.core.llms import ModelSpec, effective_max_tokens
from overbae.core.model_registry import pricing_slug
from overbae.modal.model_registry import get_model_config_any_backend
from overbae.models import DeployedModel
from overbae.services.model_catalog import fetch_model_catalog


@dataclass(frozen=True)
class ModelLimits:
    context_window: int | None = None
    max_output_tokens: int | None = None


def model_limits(model: str, *, project_id=None, custom: bool = False) -> ModelLimits:
    if project_id and (custom or model.startswith("ft-")):
        deployed = DeployedModel.objects.filter(project_id=project_id, model_id=model).first()
        if deployed is not None:
            return ModelLimits(deployed.max_model_len, deployed.max_model_len)
    if custom:
        return ModelLimits()
    slug = pricing_slug(model)
    catalog, _ = fetch_model_catalog()
    entry = next((row for row in catalog if row["id"] == slug), None)
    if entry:
        return ModelLimits(entry.get("context_length"), entry.get("max_completion_tokens"))
    config = get_model_config_any_backend(model) or {}
    limits = [
        int(value)
        for value in (
            config.get("context_length"),
            (config.get("finetuning") or {}).get("context_length"),
        )
        if value
    ]
    return ModelLimits(min(limits) if limits else None)


def estimate_input_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    # UTF-8 sizing includes non-Latin input; template/provider tokenization is still estimated.
    return math.ceil(len(text.encode("utf-8")) / 3) + 256


def request_context(
    *,
    model: str | None,
    model_spec: ModelSpec | None,
    messages: list,
    tools: list | None,
    project_id=None,
) -> dict:
    name = model_spec.model_id if model_spec else model or ""
    output = (model_spec.params or {}).get("max_tokens") if model_spec else None
    return assess_context(
        model=name,
        inputs=[estimate_input_tokens({"messages": messages, "tools": tools or []})],
        output_tokens=output,
        limits=model_limits(
            name, project_id=project_id, custom=bool(model_spec and model_spec.provider == "custom")
        ),
        role="generation",
        label=name,
    )


def assess_context(
    *,
    model: str,
    inputs: list[int],
    output_tokens: int | None = None,
    limits: ModelLimits,
    role: str,
    label: str,
    row_indices: list[int] | None = None,
) -> dict:
    output = output_tokens if output_tokens is not None else effective_max_tokens(model)
    longest = max(inputs, default=0)
    affected = [
        (row_indices[index] if row_indices else index)
        for index, tokens in enumerate(inputs)
        if (limits.context_window and tokens + output > limits.context_window)
        or (limits.max_output_tokens and output > limits.max_output_tokens)
    ]
    unknown = not limits.context_window or not inputs
    status = "warning" if affected else "unknown" if unknown else "fits"
    if affected:
        message = (
            f"{label}: {len(affected):,}/{len(inputs):,} rows may exceed the model's context or output limit. "
            f"Estimated input up to {longest:,} tokens; reserved output {output:,} tokens; "
            f"context {limits.context_window:,} tokens."
            if limits.context_window
            else f"{label}: reserved output {output:,} tokens exceeds the published output limit."
        )
    elif unknown:
        message = f"{label}: context fit is unverified; {'model limits are unavailable' if not limits.context_window else 'no rows were available'}."
    else:
        message = ""
    return {
        "role": role,
        "label": label,
        "model": model,
        **asdict(limits),
        "estimated_input_tokens": longest,
        "reserved_output_tokens": output,
        "required_context": longest + output,
        "checked_rows": len(inputs),
        "affected_rows": len(affected),
        "row_indices": affected[:10],
        "estimated": True,
        "status": status,
        "message": message,
    }
