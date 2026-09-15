"""``genai.*`` usage attributes for spans this server creates.

The keys must match exactly what the external Overmind SDK's LLM tracer stamps,
or the /traces token and cost columns only populate for SDK-emitted spans. The
``usage`` input is OpenAI/OpenRouter-shaped; OpenRouter adds ``cost`` only under
``extra_body={"usage": {"include": True}}``.
"""

from __future__ import annotations

from typing import Any

from overbae.api import overmind_attrs as oc_attrs
from overbae.services.model_catalog import estimate_cost


def _get(usage: Any, key: str) -> Any:
    if isinstance(usage, dict):
        return usage.get(key)
    return getattr(usage, key, None)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def usage_span_attributes(usage: Any, *, model: str | None = None) -> dict[str, Any]:
    """Sets only the keys actually present — a missing value is omitted, never
    zero-filled, so an absent usage stays an honest absence."""
    if usage is None:
        return {}

    prompt = _as_int(_get(usage, "prompt_tokens"))
    completion = _as_int(_get(usage, "completion_tokens"))
    total = _as_int(_get(usage, "total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)

    cost = _as_float(_get(usage, "cost"))
    if cost is None and model:
        cost = estimate_cost(model, prompt, completion)

    attrs: dict[str, Any] = {}
    if model:
        attrs[oc_attrs.LLM_MODEL] = model
    if prompt is not None:
        attrs[oc_attrs.LLM_PROMPT_TOKENS] = prompt
    if completion is not None:
        attrs[oc_attrs.LLM_COMPLETION_TOKENS] = completion
    if total is not None:
        attrs[oc_attrs.LLM_TOTAL_TOKENS] = total
    if cost is not None:
        attrs[oc_attrs.LLM_COST] = cost
    return attrs
