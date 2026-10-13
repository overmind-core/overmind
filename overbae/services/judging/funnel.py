"""Generative judge calls: output-budget retry, context assessment and provenance."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from overbae.core.llms import (
    IncompleteCompletionError,
    ModelSpec,
    call_llm,
    effective_max_tokens,
    try_json_parsing,
)
from overbae.core.model_registry import (
    LLM_PROVIDER_BY_MODEL,
    TaskType,
    resolve_model,
)
from overbae.services.llm_context import assess_context, estimate_input_tokens, model_limits

logger = logging.getLogger(__name__)


@dataclass
class ResolvedJudge:
    model_name: str | None
    model_spec: ModelSpec | None
    family: str


def resolve_judge(judge_model: str, project_id: str | None = None) -> ResolvedJudge:
    """``judge_model`` is "" (the default judge), a catalog model name, or a
    :class:`ModelRef` id."""
    if not judge_model:
        name = resolve_model(TaskType.JUDGE_SCORING)
        return ResolvedJudge(model_name=name, model_spec=None, family=_family_of(name))

    spec = _model_ref_spec(judge_model)
    if spec is not None:
        return ResolvedJudge(model_name=None, model_spec=spec, family=spec.provider)

    return ResolvedJudge(model_name=judge_model, model_spec=None, family=_family_of(judge_model))


def _model_ref_spec(maybe_id: str) -> ModelSpec | None:
    try:
        uuid.UUID(str(maybe_id))
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        from overbae.models import ModelRef  # noqa: PLC0415

        ref = ModelRef.objects.filter(id=maybe_id).first()
    except Exception:  # noqa: BLE001
        return None
    if not ref:
        return None
    return ModelSpec(
        provider=ref.provider,
        model_id=ref.model_id,
        base_url=ref.base_url,
        api_key_env=ref.api_key_ref,
        params=ref.params or {},
    )


def _family_of(model_name: str) -> str:
    return LLM_PROVIDER_BY_MODEL.get(model_name, "unknown")


def resolved_model_name(judge: ResolvedJudge) -> str:
    """A silent fallback to the second-priority model is a new judge contract."""
    if judge.model_spec is not None:
        return judge.model_spec.model_id
    return judge.model_name or ""


def is_rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "rate limit" in text or "rate-limit" in text


_PERMANENT_MARKERS = (
    "context length",
    "context_length",
    "maximum context",
    "context window",
    "too many tokens",
    "prompt is too long",
)


def is_permanent_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _PERMANENT_MARKERS)


def sanitize_raw(raw: str) -> str:
    # Postgres text/jsonb reject NUL and judges occasionally emit one.
    return raw.replace("\\u0000", "").replace("\x00", "")


def parse_structured(raw: str, response_format: type[BaseModel]) -> BaseModel | None:
    raw = sanitize_raw(raw)
    try:
        return response_format.model_validate_json(raw)
    except Exception:  # noqa: BLE001 — fall through to JSON repair
        try:
            return response_format.model_validate(try_json_parsing(raw))
        except Exception:  # noqa: BLE001
            return None


@dataclass
class JudgeOutcome:
    parsed: BaseModel | None
    raw: str
    stats: dict[str, Any]
    judge_trace_id: str
    cached: bool = False


def failure_reason(outcome: JudgeOutcome) -> str:
    return {
        "output_limit": "Judge reached its output token limit before completing the grade.",
        "context_limit": "Judge input and reserved output exceed the provider's context limit.",
    }.get(outcome.stats.get("error_kind"), "Judge output failed to parse.")


def diagnostics(outcome: JudgeOutcome) -> list[dict]:
    metadata = outcome.stats.get("judge")
    return [{"_judge": metadata}] if metadata else []


def invoke_judge(
    prompt: str,
    *,
    response_format: type[BaseModel],
    judge: ResolvedJudge | None = None,
    judge_model: str = "",
    project_id: str | None = None,
    system_prompt: str | None = None,
    request_kwargs: dict[str, Any] | None = None,
) -> JudgeOutcome:
    """Never raises on parse."""
    judge = judge or resolve_judge(judge_model, project_id)
    trace_id = uuid.uuid4().hex

    model = resolved_model_name(judge)
    limits = model_limits(
        model,
        project_id=project_id,
        custom=bool(judge.model_spec and judge.model_spec.provider == "custom"),
    )
    input_tokens = estimate_input_tokens(
        [system_prompt or "", prompt, response_format.model_json_schema()]
    )
    params = {**(judge.model_spec.params if judge.model_spec else {}), **(request_kwargs or {})}
    output_tokens = params.pop("max_tokens", None) or effective_max_tokens(model)
    context = assess_context(
        model=model,
        inputs=[input_tokens],
        output_tokens=output_tokens,
        limits=limits,
        role="judge",
        label=model,
    )
    attempts = []
    started = time.monotonic()
    for attempt in range(2):
        error_kind = ""
        try:
            # max_tokens is an absolute wire budget, including reasoning tokens.
            raw, stats = call_llm(
                prompt,
                system_prompt=system_prompt,
                model=judge.model_name,
                model_spec=judge.model_spec,
                response_format=response_format,
                request_kwargs={**params, "max_tokens": output_tokens},
            )
        except IncompleteCompletionError as exc:
            raw, stats, error_kind = exc.content, exc.stats, "output_limit"
        except RuntimeError as exc:
            if not is_permanent_error(exc):
                raise
            raw, stats, error_kind = "", {}, "context_limit"
        attempts.append(
            {
                **{
                    k: stats.get(k)
                    for k in (
                        "finish_reason",
                        "prompt_tokens",
                        "completion_tokens",
                        "reasoning_tokens",
                        "provider_request_id",
                        "served_model",
                        "response_cost",
                        "response_ms",
                    )
                },
                "output_budget": output_tokens,
                "error_kind": error_kind,
            }
        )
        if error_kind != "output_limit" or attempt:
            break
        # Without published limits a larger retry is unverified. Keep the original failure.
        ceiling = min(
            limits.max_output_tokens or output_tokens,
            (limits.context_window or 0) - input_tokens,
            64000,
        )
        increased = min(output_tokens * 2, ceiling)
        if increased <= output_tokens or time.monotonic() - started > 180:
            break
        output_tokens = increased
    stats = {
        **stats,
        "context": context,
        "attempts": attempts,
        "error_kind": error_kind,
        "judge": {"context": context, "attempts": attempts, "error_kind": error_kind},
        "response_ms": round((time.monotonic() - started) * 1000),
        "response_cost": sum(a["response_cost"] or 0 for a in attempts)
        if all(a["response_cost"] is not None for a in attempts)
        else None,
    }
    if error_kind:
        return JudgeOutcome(parsed=None, raw=raw, stats=stats, judge_trace_id=trace_id)
    parsed = parse_structured(raw, response_format)
    return JudgeOutcome(parsed=parsed, raw=raw, stats=stats, judge_trace_id=trace_id)
