"""
Task to run model backtesting for prompt templates.

Supports both:
- Manual trigger via API (user picks models or uses defaults)
- Scheduled periodic check (Celery Beat) that auto-creates jobs for
  prompts that meet eligibility criteria, mirroring the prompt-tuning pattern.
"""

import asyncio
import logging
import time
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, and_, func

from overmind.db.session import get_session_local
from overmind.models.jobs import Job
from overmind.models.prompts import Prompt
from overmind.models.suggestions import Suggestion as SuggestionModel
from overmind.models.traces import SpanModel, BacktestRun
from overmind.core.llms import (
    call_llm,
    get_thinking_budget_tokens,
    is_adaptive_mode,
    normalize_llm_response_output,
    LLM_PROVIDER_BY_MODEL,
    normalize_model_name,
)
from overmind.core.model_resolver import get_available_backtest_models
from overmind.models.iam.projects import Project
from overmind.tasks.evaluations import _evaluate_correctness_with_llm, _format_criteria
from overmind.tasks.agent_discovery import _get_span_input_text_merged
from overmind.tasks.agentic_span_processor import _safe_parse_json
from overmind.tasks.utils.task_lock import with_task_lock
from overmind.utils import calculate_llm_usage_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default models to test during backtesting.
# At runtime, get_default_backtest_models() filters this to providers that
# have an API key configured.  The full list is kept for reference /
# documentation and for callers that pass an explicit list.
# ---------------------------------------------------------------------------


def get_default_backtest_models() -> list[str]:
    """Return backtest models filtered to providers with available API keys."""
    return get_available_backtest_models()


def _models_from_suggestions(prompt: "Prompt") -> list[str]:
    """Derive model keys from ``prompt.backtest_model_suggestions``.

    Each recommendation's ``reasoning_effort`` field controls the key suffix:
      None        → '{model}'                  (no reasoning)
      'on'        → '{model}:reasoning'         (budget-token manual mode)
      'low'|...   → '{model}:reasoning-{effort}' (adaptive / effort-based)

    Returns an empty list when no suggestions are stored yet.
    """
    suggestions = prompt.backtest_model_suggestions
    if not suggestions:
        return []
    models: list[str] = []
    seen: set[str] = set()
    for rec in suggestions.get("recommendations", []):
        model_name = rec.get("model")
        if not model_name:
            continue
        effort = rec.get("reasoning_effort")
        if effort is None:
            key = model_name
        elif effort == "on":
            key = f"{model_name}:reasoning"
        else:
            key = f"{model_name}:reasoning-{effort}"
        if key not in seen:
            models.append(key)
            seen.add(key)
    return models


# Minimum scored spans required before a prompt is eligible for backtesting
MIN_SPANS_FOR_BACKTESTING = 10
# Maximum spans to use per model during a backtest run
MAX_SPANS_FOR_BACKTESTING = 50

# Max concurrent (model × span) evaluations running in parallel.
# Matches the evaluations.py pattern – bounded by a shared asyncio.Semaphore
# so we don't overwhelm LLM provider rate-limits.
_MAX_CONCURRENT_BACKTESTS = 5

# Number of result spans committed per DB transaction during Phase 4c.
# Keeps individual transactions small so a transient DB error (connection drop,
# constraint violation) only loses one chunk instead of the entire backtest run.
# At MAX_SPANS_FOR_BACKTESTING=50 × ~10 models = 500 items, this means ≤10 commits.
# Named _BACKTEST_PERSIST_CHUNK_SIZE (not _PERSIST_CHUNK_SIZE) to avoid confusion
# with evaluations.py's _EVAL_PERSIST_CHUNK_SIZE, which uses a different value (200).
_BACKTEST_PERSIST_CHUNK_SIZE = 50

# Per-call timeout for LLM invocations inside _process_item.  A hung provider
# connection would otherwise hold a semaphore slot indefinitely, stalling the
# entire backtest job.  asyncio.wait_for raises TimeoutError on expiry;
# asyncio.gather(return_exceptions=True) records it as a per-item failure.
_LLM_CALL_TIMEOUT_S = 120

# Performance thresholds for model recommendations
_PERF_TOLERANCE = 0.05  # 5 percentage-point tolerance for speed/cost alternatives
_PERF_DISQUALIFY = 0.15  # 15pp drop → model is disqualified entirely

# Thresholds at which to re-run backtesting: 30, 100, 200, 500, 1000, 2000...
_INITIAL_THRESHOLDS = [30, 100, 200, 500, 1000]


def _next_backtest_threshold(last_count: int) -> int:
    """Return the next scored-span count at which backtesting should re-run."""
    for t in _INITIAL_THRESHOLDS:
        if last_count < t:
            return t
    return ((last_count // 1000) + 1) * 1000


def _previous_backtest_threshold(last_count: int) -> int:
    """Return the previous threshold so the next threshold <= last_count.

    Used when criteria change to roll back the threshold by one step, causing
    backtesting to re-run with the updated scoring logic.

    Examples:
        last_count=120 (crossed threshold 100) -> returns 50
        -> next threshold = 100, and 120 >= 100, so backtest re-triggers.
    """
    if last_count <= 0:
        return 0

    all_thresholds = [0] + list(_INITIAL_THRESHOLDS)
    t = _INITIAL_THRESHOLDS[-1] + 1000
    while t <= last_count:
        all_thresholds.append(t)
        t += 1000

    applicable = [t for t in all_thresholds if t <= last_count]
    if len(applicable) < 2:
        return 0
    return applicable[-2]


def invalidate_backtesting_metadata(prompt: "Prompt") -> None:
    """Roll back the backtesting threshold by one step when evaluation criteria
    or agent description changes.

    This is a mirror of ``invalidate_prompt_improvement_metadata`` in
    ``prompt_improvement.py``. It causes the next threshold test to pass with
    the current span count so backtesting re-runs using the updated scoring.

    Idempotent: if ``criteria_invalidated`` is already set, it is a no-op
    so rapid successive criteria updates don't double-roll the threshold.
    """
    meta = prompt.backtest_metadata or {}

    if meta.get("criteria_invalidated"):
        return

    last_count = meta.get("last_backtest_span_count", 0)
    previous_count = _previous_backtest_threshold(last_count)

    prompt.backtest_metadata = {
        **meta,
        "last_backtest_span_count": previous_count,
        "criteria_invalidated": True,
    }


# ---------------------------------------------------------------------------
# Provider-aware scheduling helpers
# ---------------------------------------------------------------------------


def _base_model_from_key(model_key: str) -> str:
    """Extract base model name from a backtest key.

    Handles keys like 'gpt-5.2:reasoning-medium', 'claude-opus-4-5:reasoning'.
    """
    return model_key.split(":reasoning")[0] if ":reasoning" in model_key else model_key


def _reasoning_mode_from_key(model_key: str) -> str | None:
    """Derive the reasoning_mode label from a backtest model key.

    Returns the effort level (e.g. 'medium'), 'enabled' for manual budget-token
    mode, or None when no reasoning is used.
    """
    if ":reasoning" not in model_key:
        return None
    if ":reasoning-" in model_key:
        return model_key.split(":reasoning-", 1)[1]
    return "enabled"


def _interleave_models_by_provider(models: list[str]) -> list[str]:
    """Reorder models so that consecutive entries target different providers.

    E.g. [gpt-5-mini, gpt-5.2, claude-opus, claude-sonnet, gemini-3.1-pro, gemini-3-flash]
      →  [gpt-5-mini, claude-opus, gemini-3.1-pro, gpt-5.2, claude-sonnet, gemini-3-flash]

    This spreads load across providers when tasks are processed through a
    concurrency-limited semaphore. Handles keys like 'gpt-5.2:reasoning-medium'.
    """
    by_provider: dict[str, list[str]] = defaultdict(list)
    for model_key in models:
        base = _base_model_from_key(model_key)
        provider = LLM_PROVIDER_BY_MODEL.get(base)
        if provider is None:
            logger.debug(
                f"_interleave_models_by_provider: model '{base}' not found in "
                "LLM_PROVIDER_BY_MODEL — grouped under 'unknown'. "
                "Update LLM_PROVIDER_BY_MODEL if this is a new model."
            )
            provider = "unknown"
        by_provider[provider].append(model_key)

    result: list[str] = []
    queues = list(by_provider.values())
    max_len = max((len(q) for q in queues), default=0)
    for i in range(max_len):
        for queue in queues:
            if i < len(queue):
                result.append(queue[i])
    return result


# ---------------------------------------------------------------------------
# Current-model detection & baseline helpers
# ---------------------------------------------------------------------------


def _detect_current_model(spans: list[SpanModel]) -> str | None:
    """Return the most-commonly used model across the input spans."""
    model_counts: Counter = Counter()
    for span in spans:
        meta = span.metadata_attributes or {}
        model = meta.get("gen_ai.request.model")
        if model:
            model_counts[normalize_model_name(model)] += 1
    if model_counts:
        return model_counts.most_common(1)[0][0]
    return None


def _compute_baseline_metrics(spans: list[SpanModel]) -> dict[str, float]:
    """Compute cost / latency / performance baseline from the original spans."""
    latencies: list[float] = []
    costs: list[float] = []
    scores: list[float] = []

    for span in spans:
        # Latency
        if span.start_time_unix_nano and span.end_time_unix_nano:
            latency_ms = (
                span.end_time_unix_nano - span.start_time_unix_nano
            ) / 1_000_000
            if latency_ms > 0:
                latencies.append(latency_ms)

        # Cost
        meta = span.metadata_attributes or {}
        model = meta.get("gen_ai.request.model", "")
        input_tokens = meta.get("gen_ai.usage.input_tokens", 0)
        output_tokens = meta.get("gen_ai.usage.output_tokens", 0)
        if model and (input_tokens or output_tokens):
            cost = calculate_llm_usage_cost(
                model, int(input_tokens), int(output_tokens)
            )
            costs.append(cost)

        # Performance score (only for already-scored spans)
        if span.feedback_score and "correctness" in span.feedback_score:
            scores.append(float(span.feedback_score["correctness"]))

    return {
        "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
        "avg_cost_per_request": sum(costs) / len(costs) if costs else 0,
        "avg_eval_score": sum(scores) / len(scores) if scores else 0,
        "scored_span_count": len(scores),
        "total_spans": len(spans),
    }


# ---------------------------------------------------------------------------
# Recommendation heuristic
# ---------------------------------------------------------------------------


def _generate_recommendations(
    current_model: str | None,
    baseline: dict[str, float],
    model_metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    """Produce model-switch recommendations from backtest aggregate metrics.

    Heuristic:
    - Disqualify any model whose performance drops >15 pp below baseline.
    - *Top performer*: highest eval score that actually beats the baseline.
    - *Fastest*: lowest latency among models within 5 pp of baseline perf.
    - *Cheapest*: lowest cost among models within 5 pp of baseline perf.
    - *Best overall*: weighted combination (perf×3 + latency×1 + cost×1).
    - If the current model is already best on all fronts → acknowledge.
    """
    b_score = baseline.get("avg_eval_score", 0)
    b_latency = baseline.get("avg_latency_ms", 0)
    b_cost = baseline.get("avg_cost_per_request", 0)
    has_baseline_score = baseline.get("scored_span_count", 0) > 0

    recs: dict[str, Any] = {
        "current_model": {
            "name": current_model or "unknown",
            "avg_eval_score": round(b_score, 4),
            "avg_latency_ms": round(b_latency, 2),
            "avg_cost_per_request": round(b_cost, 6),
            "scored_span_count": baseline.get("scored_span_count", 0),
        },
    }

    # Filter out current model and disqualified models
    candidates: dict[str, dict[str, float]] = {}
    for model, metrics in model_metrics.items():
        if _base_model_from_key(model) == current_model:
            continue
        if metrics.get("success_rate", 0) == 0:
            continue
        if has_baseline_score:
            score_drop = b_score - metrics.get("avg_eval_score", 0)
            if score_drop > _PERF_DISQUALIFY:
                continue
        candidates[model] = metrics

    if not candidates:
        recs["verdict"] = "current_is_best"
        recs["summary"] = (
            f"The current model ({current_model or 'unknown'}) remains the best choice. "
            f"No alternative model met the performance threshold."
        )
        return recs

    # --- Top performer (must beat baseline) ---
    top_model = max(candidates, key=lambda m: candidates[m].get("avg_eval_score", 0))
    top = candidates[top_model]
    if not has_baseline_score or top.get("avg_eval_score", 0) > b_score:
        perf_delta = (
            ((top["avg_eval_score"] - b_score) / b_score * 100) if b_score > 0 else 0
        )
        recs["top_performer"] = {
            "model": top_model,
            "avg_eval_score": round(top["avg_eval_score"], 4),
            "performance_delta_pct": round(perf_delta, 2),
            "avg_latency_ms": round(top.get("avg_latency_ms", 0), 2),
            "avg_cost_per_request": round(top.get("avg_cost_per_request", 0), 6),
            "reason": (
                f"Best performance: "
                f"{'+' if perf_delta >= 0 else ''}{perf_delta:.1f}% improvement vs current model"
            ),
        }

    # --- Speed / cost candidates (within tolerance) ---
    within_tol: dict[str, dict[str, float]] = {}
    for model, metrics in candidates.items():
        if (
            not has_baseline_score
            or (b_score - metrics.get("avg_eval_score", 0)) <= _PERF_TOLERANCE
        ):
            within_tol[model] = metrics

    # Fastest
    if within_tol:
        fastest_model = min(
            within_tol,
            key=lambda m: within_tol[m].get("avg_latency_ms", float("inf")),
        )
        fastest = within_tol[fastest_model]
        if b_latency > 0 and fastest.get("avg_latency_ms", 0) < b_latency:
            lat_improv = (b_latency - fastest["avg_latency_ms"]) / b_latency * 100
            sd = b_score - fastest.get("avg_eval_score", 0)
            perf_note = (
                f" Performance: {abs(sd) * 100:.1f}pp {'drop' if sd > 0 else 'gain'}."
            )
            recs["fastest"] = {
                "model": fastest_model,
                "avg_eval_score": round(fastest.get("avg_eval_score", 0), 4),
                "performance_delta_pp": round(-sd, 4),
                "avg_latency_ms": round(fastest["avg_latency_ms"], 2),
                "latency_improvement_pct": round(lat_improv, 2),
                "avg_cost_per_request": round(
                    fastest.get("avg_cost_per_request", 0), 6
                ),
                "reason": (f"Latency reduced by {lat_improv:.0f}%.{perf_note}"),
            }

    # Cheapest
    if within_tol:
        cheapest_model = min(
            within_tol,
            key=lambda m: within_tol[m].get("avg_cost_per_request", float("inf")),
        )
        cheapest = within_tol[cheapest_model]
        if b_cost > 0 and cheapest.get("avg_cost_per_request", 0) < b_cost:
            cost_improv = (b_cost - cheapest["avg_cost_per_request"]) / b_cost * 100
            sd = b_score - cheapest.get("avg_eval_score", 0)
            perf_note = (
                f" Performance: {abs(sd) * 100:.1f}pp {'drop' if sd > 0 else 'gain'}."
            )
            recs["cheapest"] = {
                "model": cheapest_model,
                "avg_eval_score": round(cheapest.get("avg_eval_score", 0), 4),
                "performance_delta_pp": round(-sd, 4),
                "avg_cost_per_request": round(cheapest["avg_cost_per_request"], 6),
                "cost_improvement_pct": round(cost_improv, 2),
                "avg_latency_ms": round(cheapest.get("avg_latency_ms", 0), 2),
                "reason": (f"Cost reduced by {cost_improv:.0f}%.{perf_note}"),
            }

    # --- Best overall (weighted composite) ---
    best_model: str | None = None
    best_combined = float("-inf")

    for model, metrics in within_tol.items():
        perf_gain = (
            (metrics.get("avg_eval_score", 0) - b_score) / max(b_score, 0.01) * 100
        )
        lat_gain = (
            (b_latency - metrics.get("avg_latency_ms", 0)) / max(b_latency, 1) * 100
        )
        cost_gain = (
            (b_cost - metrics.get("avg_cost_per_request", 0))
            / max(b_cost, 0.000001)
            * 100
        )
        # Weight: performance is most important, then latency, then cost
        combined = perf_gain * 3.0 + lat_gain * 1.0 + cost_gain * 1.0
        if combined > best_combined:
            best_combined = combined
            best_model = model

    if best_model and best_combined > 0:
        best = within_tol[best_model]
        perf_delta = best.get("avg_eval_score", 0) - b_score
        lat_delta_pct = (
            (b_latency - best.get("avg_latency_ms", 0)) / max(b_latency, 1) * 100
        )
        cost_delta_pct = (
            (b_cost - best.get("avg_cost_per_request", 0)) / max(b_cost, 0.000001) * 100
        )
        # Build clear reason parts
        reason_parts = [
            f"Performance: {'+' if perf_delta >= 0 else ''}{perf_delta * 100:.1f}pp"
        ]
        if lat_delta_pct > 0:
            reason_parts.append(f"latency reduced by {lat_delta_pct:.0f}%")
        elif lat_delta_pct < 0:
            reason_parts.append(f"latency increased by {abs(lat_delta_pct):.0f}%")
        if cost_delta_pct > 0:
            reason_parts.append(f"cost reduced by {cost_delta_pct:.0f}%")
        elif cost_delta_pct < 0:
            reason_parts.append(f"cost increased by {abs(cost_delta_pct):.0f}%")
        recs["best_overall"] = {
            "model": best_model,
            "avg_eval_score": round(best.get("avg_eval_score", 0), 4),
            "avg_latency_ms": round(best.get("avg_latency_ms", 0), 2),
            "avg_cost_per_request": round(best.get("avg_cost_per_request", 0), 6),
            "reason": f"Best overall trade-off: {', '.join(reason_parts)}",
        }
        recs["verdict"] = "switch_recommended"
        recs["summary"] = (
            f"Consider switching from {current_model or 'unknown'} to {best_model}. "
            + recs["best_overall"]["reason"]
            + "."
        )
    elif "top_performer" in recs:
        tp = recs["top_performer"]
        recs["verdict"] = "consider_top_performer"
        recs["summary"] = (
            f"{tp['model']} offers {tp['reason']}, but may come with higher "
            f"cost or latency. Consider switching if performance is the priority."
        )
    else:
        recs["verdict"] = "current_is_best"
        recs["summary"] = (
            f"The current model ({current_model or 'unknown'}) provides the best "
            f"overall trade-off. No alternative offers a meaningful improvement "
            f"without sacrificing performance."
        )

    return recs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_backtest_model_key(model_key: str) -> tuple[str, str | None, int | None]:
    """Parse a backtest model key into (base_model, reasoning_effort, thinking_budget_tokens).

    Key formats:
      'gpt-5.2'                   → (gpt-5.2, None, None)        — no reasoning
      'gpt-5.2:reasoning-medium'  → (gpt-5.2, 'medium', None)    — effort-based reasoning
      'gpt-5.2:reasoning-low'     → (gpt-5.2, 'low', None)
      'claude-opus-4-5:reasoning' → (claude-opus-4-5, None, 8000) — manual budget-token mode
    """
    base_model = (
        model_key.split(":reasoning")[0] if ":reasoning" in model_key else model_key
    )

    if ":reasoning" not in model_key:
        return base_model, None, None

    if ":reasoning-" in model_key:
        effort = model_key.split(":reasoning-", 1)[1]
        return base_model, effort, None

    # ':reasoning' suffix without an effort level → manual budget-token mode (adaptive_mode=False)
    if is_adaptive_mode(base_model) is False:
        budgets = get_thinking_budget_tokens(base_model)
        return base_model, None, budgets[0] if budgets else None
    return base_model, None, None


def _run_model_on_input(
    model_name: str,
    input_text: str,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    *,
    model_key: str | None = None,
) -> dict[str, Any]:
    """Run a single model on an input and collect metrics.

    When ``model_key`` is provided (e.g. 'gpt-5.2:reasoning-medium' or plain
    'gpt-5.2' for no reasoning), it controls reasoning_effort/thinking_budget_tokens.
    When ``messages`` is provided it is forwarded directly to ``call_llm``
    so the full conversation (including tool-call and tool-result turns) is
    replayed.  When ``tools`` is provided the tool definitions are forwarded
    so the model can make tool-call decisions.

    This is deliberately *synchronous* so it can be offloaded to a thread
    via ``asyncio.to_thread`` without blocking the event loop.
    """
    base_model, reasoning_effort, thinking_budget_tokens = (
        _parse_backtest_model_key(model_key) if model_key else (model_name, None, None)
    )
    try:
        start_time = time.time()

        output, stats = call_llm(
            input_text=input_text,
            system_prompt=None,
            model=base_model,
            messages=messages,
            tools=tools,
            reasoning_effort=reasoning_effort,
            thinking_budget_tokens=thinking_budget_tokens,
        )
        output = normalize_llm_response_output(output)

        end_time = time.time()
        latency_ms = (end_time - start_time) * 1000

        return {
            "output": output,
            "latency_ms": latency_ms,
            "cost": stats.get("response_cost", 0),
            "input_tokens": stats.get("prompt_tokens", 0),
            "output_tokens": stats.get("completion_tokens", 0),
            "success": True,
            "error": None,
        }
    except Exception as e:
        logger.error(f"Error running model {model_name}: {str(e)}")
        return {
            "output": None,
            "latency_ms": 0,
            "cost": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "success": False,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# Inference helper (module-level so it can be tested in isolation)
# ---------------------------------------------------------------------------


async def _run_inference(
    model_name: str,
    span: SpanModel,
    input_text: str,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Run model inference for one (model, span) pair.

    Args:
        model_name: Backtest model key (e.g. ``"openai/gpt-5-mini"``).
        span: Source span whose input is replayed.
        input_text: Pre-extracted plain-text input for template matching.
        semaphore: Concurrency limiter shared across all Phase A calls.

    Returns:
        Dict with inference result fields consumed by Phase B scoring.
    """
    parsed_span_input = _safe_parse_json(span.input)
    input_data = parsed_span_input or {}
    span_response_type = (span.metadata_attributes or {}).get("response_type")

    if span_response_type:
        # Tool-calling span: replay with full conversation + tools
        call_messages = (
            parsed_span_input if isinstance(parsed_span_input, list) else None
        )
        call_tools = (span.metadata_attributes or {}).get("available_tools") or []

        llm_kwargs: dict[str, Any] = {
            "messages": call_messages,
            "tools": call_tools if call_tools else None,
            "model_key": model_name,
        }

        # Preserve response_type / is_agentic so the correct judge
        # branch is used (tool-call or tool-answer evaluator).
        backtest_metadata = span.metadata_attributes or {}
        eval_input_data = input_data
    else:
        # Plain / legacy span: existing plain-text behaviour.
        # call_tools is unused for plain spans; initialise here so it is
        # always defined before the shared code below references it.
        call_tools = []

        llm_kwargs = {"model_key": model_name}

        # Strip response_type / is_agentic so we don't route into
        # the tool-call judge for a plain-text completion.
        backtest_metadata = {
            k: v
            for k, v in (span.metadata_attributes or {}).items()
            if k not in ("response_type", "is_agentic")
        }
        # Strip tool/assistant messages — the model only saw
        # user/system messages, so judging against tool results
        # it never received would be unfair.
        if isinstance(parsed_span_input, list):
            eval_input_data = [
                msg
                for msg in parsed_span_input
                if not isinstance(msg, dict)
                or msg.get("role") not in ("tool", "assistant", "function")
            ]
        else:
            eval_input_data = input_data

    async with semaphore:
        model_result = await asyncio.wait_for(
            asyncio.to_thread(
                _run_model_on_input,
                model_name,
                input_text,
                **llm_kwargs,
            ),
            timeout=_LLM_CALL_TIMEOUT_S,
        )

    output_data = model_result.get("output") or ""

    return {
        "model_name": model_name,
        "span": span,
        "input_data": input_data,
        "eval_input_data": eval_input_data,
        "output_data": output_data,
        "backtest_metadata": backtest_metadata,
        "call_tools": call_tools,
        "span_response_type": span_response_type,
        "model_result": model_result,
    }


async def _score_inference(
    item: dict[str, Any],
    semaphore: asyncio.Semaphore,
    criteria_text: str,
    project_description: str | None,
    agent_description: str | None,
) -> dict[str, Any]:
    """Score one inference result; returns the item with eval fields added.

    Args:
        item: Inference result dict produced by ``_run_inference``.
        semaphore: Concurrency limiter shared across all Phase B calls.
        criteria_text: Formatted correctness criteria string.
        project_description: Optional project context for the judge.
        agent_description: Optional agent context for the judge.

    Returns:
        The same dict with ``eval_score`` and ``eval_reason`` keys added.
    """
    model_result = item["model_result"]
    eval_score = 0.0
    eval_reason: str | None = None

    if model_result["success"] and model_result.get("output"):
        async with semaphore:
            eval_score, eval_reason = await asyncio.wait_for(
                asyncio.to_thread(
                    _evaluate_correctness_with_llm,
                    input_data=item["eval_input_data"],
                    output_data=item["output_data"],
                    criteria_text=criteria_text,
                    project_description=project_description,
                    agent_description=agent_description,
                    span_metadata=item["backtest_metadata"],
                ),
                timeout=_LLM_CALL_TIMEOUT_S,
            )

    return {**item, "eval_score": eval_score, "eval_reason": eval_reason}


# ---------------------------------------------------------------------------
# Result span builder
# ---------------------------------------------------------------------------


def _build_result_span(
    item: dict[str, Any],
    span_id: str,
    *,
    backtest_run_id: uuid.UUID,
    prompt_id: str,
) -> SpanModel:
    """Build a ``SpanModel`` for one (model, span) inference + scoring result.

    Extracted as a module-level function so it can be tested in isolation
    without running a full backtesting job.

    Args:
        item: Inference + scoring result dict produced by ``_score_inference``.
        span_id: Pre-assigned UUID string for the new span.
        backtest_run_id: UUID of the current backtest run (written to metadata).
        prompt_id: Prompt ID to associate the result span with.

    Returns:
        An unsaved ``SpanModel`` ready to be added to a DB session.
    """
    model_name = item["model_name"]
    span = item["span"]
    model_result = item["model_result"]
    eval_score = item["eval_score"]
    eval_reason = item["eval_reason"]
    output_data = item["output_data"]
    call_tools = item["call_tools"]
    span_response_type = item["span_response_type"]
    current_time_nano = int(time.time() * 1_000_000_000)
    return SpanModel(
        span_id=span_id,
        operation=f"backtest:{model_name}",
        start_time_unix_nano=current_time_nano,
        end_time_unix_nano=current_time_nano
        + int(model_result["latency_ms"] * 1_000_000),
        input=item["input_data"],
        output=output_data if model_result.get("output") else None,
        status_code=1 if model_result["success"] else 2,
        metadata_attributes={
            "backtest": True,
            "backtest_run_id": str(backtest_run_id),
            "source_span_id": span.span_id,
            "model": _base_model_from_key(model_name),
            "reasoning_mode": _reasoning_mode_from_key(model_name),
            "latency_ms": model_result["latency_ms"],
            "cost": model_result["cost"],
            "input_tokens": model_result["input_tokens"],
            "output_tokens": model_result["output_tokens"],
            "error": model_result["error"],
            "available_tools": call_tools if span_response_type else [],
        },
        feedback_score=(
            {
                "correctness": eval_score,
                "correctness_reason": eval_reason,
            }
            if eval_reason
            else {"correctness": eval_score}
        ),
        trace_id=span.trace_id,
        prompt_id=prompt_id,
    )


# ---------------------------------------------------------------------------
# Core backtesting execution
# ---------------------------------------------------------------------------


async def _run_backtesting(
    prompt_id: str,
    models: list[str],
    span_count: int,
    user_id: str,
    organisation_id: str | None = None,
    *,
    job_id: str,
    celery_task_id: str | None = None,
) -> dict[str, Any]:
    """Run backtesting for multiple models on a set of spans.

    Performance design:
    - All (model × span) pairs are evaluated **concurrently** via
      ``asyncio.gather`` bounded by ``_MAX_CONCURRENT_BACKTESTS``.
    - Execution is split into two independent phases so each semaphore slot
      holds only one LLM call at a time instead of two sequential ones:
        Phase A — model inference: replay all (model × span) pairs concurrently.
        Phase B — correctness scoring: fan out judge calls concurrently over
                  all successful inference results.
    - All result spans are persisted in a single bulk INSERT after both phases
      complete, replacing the previous per-item session-per-commit pattern.
    - Models are interleaved by provider so concurrent requests spread
      across OpenAI / Anthropic / Gemini rather than hammering one.
    - Setup data (spans, criteria, prompt/project context) is fetched in a
      single DB session instead of three separate round-trips.
    """
    from overmind.api.v1.endpoints.jobs import JobStatus

    AsyncSessionLocal = get_session_local()
    backtest_run_id = uuid.uuid4()

    logger.info(f"Running backtesting for {prompt_id}")

    # -- Create backtest run record --
    async with AsyncSessionLocal() as session:
        backtest_run = BacktestRun(
            backtest_run_id=backtest_run_id,
            prompt_id=prompt_id,
            models=models,
            status="running",
            celery_task_id=celery_task_id,
        )
        session.add(backtest_run)
        await session.commit()
        logger.info(f"Created backtest run: {backtest_run_id}")

    try:
        # ---------------------------------------------------------------
        # 1. Fetch spans, criteria, and context in a single DB session
        # ---------------------------------------------------------------
        logger.info(f"Fetching {span_count} spans for backtesting prompt {prompt_id}")
        project_description: str | None = None
        agent_description: str | None = None
        criteria_text: str = ""
        spans: list[SpanModel] = []

        async with AsyncSessionLocal() as setup_session:
            # Spans
            stmt = (
                select(SpanModel)
                .where(
                    and_(
                        SpanModel.prompt_id == prompt_id,
                        SpanModel.input.isnot(None),
                        SpanModel.exclude_system_spans(),
                        SpanModel.feedback_score.has_key("correctness"),
                    )
                )
                .order_by(SpanModel.created_at.asc())
                .limit(span_count)
            )
            spans_result = await setup_session.execute(stmt)
            spans = list(spans_result.scalars().all())
            # Detach spans from the session before it closes so that accessing
            # their columns outside this block never triggers a lazy-load that
            # would raise DetachedInstanceError / MissingGreenlet.
            for span in spans:
                setup_session.expunge(span)

            if not spans:
                raise ValueError(f"No spans found for backtesting prompt {prompt_id}")
            logger.info(f"Found {len(spans)} spans for backtesting")

            # Criteria + agent/project context — all from the same session
            try:
                project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
                project_uuid = UUID(project_id_str)

                prompt_result = await setup_session.execute(
                    select(Prompt).where(
                        and_(
                            Prompt.project_id == project_uuid,
                            Prompt.version == version,
                            Prompt.slug == slug,
                        )
                    )
                )
                prompt_obj = prompt_result.scalar_one_or_none()
                if not prompt_obj:
                    raise ValueError(f"Prompt not found: {prompt_id}")

                criteria_dict = prompt_obj.evaluation_criteria
                if (
                    not criteria_dict
                    or "correctness" not in criteria_dict
                    or not criteria_dict["correctness"]
                ):
                    raise ValueError(
                        f"Prompt {prompt_id} does not have evaluation criteria."
                    )
                criteria_text = _format_criteria(criteria_dict["correctness"])

                if prompt_obj.agent_description:
                    agent_description = prompt_obj.agent_description.get("description")

                project_result = await setup_session.execute(
                    select(Project).where(Project.project_id == project_uuid)
                )
                project_obj = project_result.scalar_one_or_none()
                if project_obj and project_obj.description:
                    project_description = project_obj.description

            except (ValueError, TypeError) as e:
                logger.warning(f"Could not fetch criteria/context for backtesting: {e}")
                raise

        # ---------------------------------------------------------------
        # 2. Detect current model & compute baseline
        # ---------------------------------------------------------------
        current_model = _detect_current_model(spans)
        baseline_metrics = _compute_baseline_metrics(spans)
        logger.info(
            f"Current model: {current_model}, baseline: "
            f"score={baseline_metrics['avg_eval_score']:.3f}, "
            f"latency={baseline_metrics['avg_latency_ms']:.0f}ms, "
            f"cost=${baseline_metrics['avg_cost_per_request']:.6f}"
        )

        # ---------------------------------------------------------------
        # 3. Build work items – interleaved by provider
        # ---------------------------------------------------------------
        ordered_models = _interleave_models_by_provider(models)

        # Iterate span-first, model-second so that with the interleaved
        # model order consecutive items naturally target different providers.
        work_items: list[tuple] = []
        for span in spans:
            input_text = _get_span_input_text_merged(span)
            if not input_text:
                logger.warning(f"Skipping span {span.span_id}: no input text available")
                continue
            for model_name in ordered_models:
                work_items.append((model_name, span, input_text))

        logger.info(
            f"Processing {len(work_items)} (model × span) pairs across "
            f"{len(models)} models with concurrency={_MAX_CONCURRENT_BACKTESTS}"
        )

        # ---------------------------------------------------------------
        # 4a. Phase A — model inference (concurrent, semaphore-bounded)
        #
        # Each slot holds exactly one LLM call.  Scoring is deferred to
        # Phase B so the semaphore is not held across two sequential calls.
        #
        # NOTE: inference_semaphore and score_semaphore intentionally share
        # the same bound (_MAX_CONCURRENT_BACKTESTS).  The phases are strictly
        # sequential — Phase B's asyncio.gather only starts after Phase A's
        # asyncio.gather fully resolves — so at most _MAX_CONCURRENT_BACKTESTS
        # LLM calls are in-flight at any moment.  Do NOT merge the phases into
        # a single gather; that would hold a semaphore slot across two
        # sequential LLM calls (inference + scoring) per item.
        # ---------------------------------------------------------------
        inference_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BACKTESTS)

        inference_raw = await asyncio.gather(
            *[_run_inference(m, s, t, inference_semaphore) for m, s, t in work_items],
            return_exceptions=True,
        )

        # Separate successful inferences from failures.
        # all_failures accumulates both Phase A (inference) and Phase B (scoring)
        # failures so they flow into processed_results as a single flat list.
        inference_results: list[dict[str, Any]] = []
        all_failures: list[dict[str, Any]] = []
        for i, res in enumerate(inference_raw):
            m_name, sp, _ = work_items[i]
            if isinstance(res, Exception):
                logger.error(
                    f"Inference error for {m_name} on span {sp.span_id}: {res}"
                )
                all_failures.append(
                    {
                        "model_name": m_name,
                        "span_id": sp.span_id,
                        "success": False,
                        "error": str(res),
                        "eval_score": 0.0,
                        "latency_ms": 0,
                        "cost": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                    }
                )
            else:
                inference_results.append(res)

        logger.info(
            f"Phase A complete: {len(inference_results)} inference(s) succeeded, "
            f"{len(all_failures)} failed"
        )

        # ---------------------------------------------------------------
        # 4b. Phase B — correctness scoring (concurrent, semaphore-bounded)
        #
        # Fan out all judge calls independently of inference so the
        # semaphore is not held across two sequential LLM calls.
        # ---------------------------------------------------------------
        score_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BACKTESTS)

        scored_raw = await asyncio.gather(
            *[
                _score_inference(
                    item,
                    semaphore=score_semaphore,
                    criteria_text=criteria_text,
                    project_description=project_description,
                    agent_description=agent_description,
                )
                for item in inference_results
            ],
            return_exceptions=True,
        )

        scored_results: list[dict[str, Any]] = []
        for i, res in enumerate(scored_raw):
            item = inference_results[i]
            if isinstance(res, Exception):
                logger.error(
                    f"Scoring error for {item['model_name']} on span "
                    f"{item['span'].span_id}: {res}"
                )
                all_failures.append(
                    {
                        "model_name": item["model_name"],
                        "span_id": item["span"].span_id,
                        "success": False,
                        "error": str(res),
                        "eval_score": 0.0,
                        "latency_ms": item["model_result"]["latency_ms"],
                        "cost": item["model_result"]["cost"],
                        "input_tokens": item["model_result"]["input_tokens"],
                        "output_tokens": item["model_result"]["output_tokens"],
                    }
                )
            else:
                scored_results.append(res)

        logger.info(f"Phase B complete: {len(scored_results)} item(s) scored")

        # ---------------------------------------------------------------
        # 4c. Persist result spans in chunks
        #
        # Committing in _BACKTEST_PERSIST_CHUNK_SIZE batches instead of one giant
        # transaction limits the blast radius of a transient DB error: only
        # the current chunk is lost, not the entire backtest run.
        # Each chunk is retried once on failure before giving up.
        # ---------------------------------------------------------------
        result_span_ids: list[str] = []

        # _build_result_span is a module-level helper; pass the run-scoped
        # identifiers explicitly so it remains independently testable.

        # Pre-assign span IDs so the aggregation loop below can zip them
        # regardless of which chunks succeeded.
        for _ in scored_results:
            result_span_ids.append(str(uuid.uuid4()))

        persisted_count = 0
        failed_chunk_ranges: list[str] = []  # e.g. ["0–49", "100–149"]
        for chunk_start in range(0, len(scored_results), _BACKTEST_PERSIST_CHUNK_SIZE):
            chunk_items = scored_results[
                chunk_start : chunk_start + _BACKTEST_PERSIST_CHUNK_SIZE
            ]
            chunk_ids = result_span_ids[
                chunk_start : chunk_start + _BACKTEST_PERSIST_CHUNK_SIZE
            ]
            chunk_end = chunk_start + len(chunk_items) - 1

            for attempt in range(2):
                try:
                    async with AsyncSessionLocal() as db:
                        for item, span_id in zip(chunk_items, chunk_ids):
                            db.add(
                                _build_result_span(
                                    item,
                                    span_id,
                                    backtest_run_id=backtest_run_id,
                                    prompt_id=prompt_id,
                                )
                            )
                        await db.commit()
                    persisted_count += len(chunk_items)
                    break
                except Exception as persist_exc:
                    if attempt == 0:
                        logger.warning(
                            f"Chunk persist failed (items {chunk_start}–{chunk_end}), "
                            f"retrying: {persist_exc}"
                        )
                    else:
                        logger.error(
                            f"Chunk persist failed after retry (items {chunk_start}–"
                            f"{chunk_end}), skipping: {persist_exc}"
                        )
                        failed_chunk_ranges.append(f"{chunk_start}–{chunk_end}")

        if failed_chunk_ranges:
            logger.error(
                f"Persist failures: {len(failed_chunk_ranges)} chunk(s) lost "
                f"({', '.join(failed_chunk_ranges)})"
            )
        logger.info(
            f"Persisted {persisted_count}/{len(scored_results)} result span(s) "
            f"in chunks of {_BACKTEST_PERSIST_CHUNK_SIZE}"
        )

        # Build the flat processed_results list consumed by aggregation below
        processed_results: list[dict[str, Any]] = list(all_failures)
        for item, result_span_id in zip(scored_results, result_span_ids):
            model_result = item["model_result"]
            processed_results.append(
                {
                    "model_name": item["model_name"],
                    "span_id": item["span"].span_id,
                    "result_span_id": result_span_id,
                    "input": item["input_data"],
                    "output": model_result.get("output"),
                    "latency_ms": model_result["latency_ms"],
                    "cost": model_result["cost"],
                    "input_tokens": model_result["input_tokens"],
                    "output_tokens": model_result["output_tokens"],
                    "eval_score": item["eval_score"],
                    "success": model_result["success"],
                    "error": model_result["error"],
                }
            )

        # ---------------------------------------------------------------
        # 5. Aggregate results per model
        # ---------------------------------------------------------------
        results_by_model: dict[str, list[dict]] = defaultdict(list)
        for r in processed_results:
            results_by_model[r["model_name"]].append(r)

        results: dict[str, Any] = {
            "backtest_run_id": str(backtest_run_id),
            "prompt_id": prompt_id,
            "span_count": len(spans),
            "models": models,
            "user_id": user_id,
            "organisation_id": organisation_id,
            "current_model": current_model,
            "baseline_metrics": baseline_metrics,
            "model_results": {},
        }

        model_agg_metrics: dict[str, dict[str, float]] = {}

        for model_name in models:
            model_items = results_by_model.get(model_name, [])
            successful = [r for r in model_items if r.get("success")]

            if successful:
                avg_latency = sum(r["latency_ms"] for r in successful) / len(successful)
                total_cost = sum(r["cost"] for r in successful)
                avg_cost = total_cost / len(successful)
                total_in_tok = sum(r["input_tokens"] for r in successful)
                total_out_tok = sum(r["output_tokens"] for r in successful)
                avg_eval = sum(r["eval_score"] for r in successful) / len(successful)
            else:
                avg_latency = total_cost = avg_cost = 0
                total_in_tok = total_out_tok = 0
                avg_eval = 0

            agg = {
                "avg_latency_ms": avg_latency,
                "total_cost": total_cost,
                "avg_cost_per_request": avg_cost,
                "total_input_tokens": total_in_tok,
                "total_output_tokens": total_out_tok,
                "avg_input_tokens": total_in_tok / len(successful) if successful else 0,
                "avg_output_tokens": total_out_tok / len(successful)
                if successful
                else 0,
                "avg_eval_score": avg_eval,
                "success_rate": len(successful) / len(model_items)
                if model_items
                else 0,
            }

            results["model_results"][model_name] = {
                "individual_results": model_items,
                "aggregate_metrics": agg,
            }
            model_agg_metrics[model_name] = agg

            logger.info(
                f"Completed backtesting for model {model_name}: "
                f"avg_latency={avg_latency:.2f}ms, total_cost=${total_cost:.4f}, "
                f"avg_eval_score={avg_eval:.2f}"
            )

        # ---------------------------------------------------------------
        # 6. Generate recommendations & overall verdict
        # ---------------------------------------------------------------
        recommendations = _generate_recommendations(
            current_model=current_model,
            baseline=baseline_metrics,
            model_metrics=model_agg_metrics,
        )
        results["recommendations"] = recommendations
        logger.info(
            f"Backtest verdict: {recommendations.get('verdict')} – "
            f"{recommendations.get('summary')}"
        )

        # ---------------------------------------------------------------
        # 7. Create a Suggestion when a model switch is recommended
        # ---------------------------------------------------------------
        if recommendations.get("verdict") in (
            "switch_recommended",
            "consider_top_performer",
        ):
            try:
                project_id_str, _version, slug = Prompt.parse_prompt_id(prompt_id)
                best = recommendations.get("best_overall") or recommendations.get(
                    "top_performer", {}
                )
                rec_model = best.get("model", "unknown")

                suggestion_title = f"Model Backtest: Consider switching to {rec_model}"
                suggestion_description = recommendations.get("summary", "")

                suggestion_scores = {
                    "current_model": current_model,
                    "recommended_model": rec_model,
                    "current_avg_score": round(
                        baseline_metrics.get("avg_eval_score", 0), 4
                    ),
                    "recommended_avg_score": round(best.get("avg_eval_score", 0), 4),
                    "current_avg_latency_ms": round(
                        baseline_metrics.get("avg_latency_ms", 0), 2
                    ),
                    "recommended_avg_latency_ms": round(
                        best.get("avg_latency_ms", 0), 2
                    ),
                    "current_avg_cost": round(
                        baseline_metrics.get("avg_cost_per_request", 0), 6
                    ),
                    "recommended_avg_cost": round(
                        best.get("avg_cost_per_request", 0), 6
                    ),
                    "spans_tested": len(spans),
                    "models_tested": len(models),
                }

                async with AsyncSessionLocal() as session:
                    suggestion = SuggestionModel(
                        prompt_slug=slug,
                        project_id=UUID(project_id_str),
                        job_id=UUID(job_id) if job_id else None,
                        title=suggestion_title,
                        description=suggestion_description,
                        new_prompt_text=None,
                        new_prompt_version=None,
                        scores=suggestion_scores,
                        status="pending",
                    )
                    session.add(suggestion)
                    await session.commit()
                    results["suggestion_id"] = str(suggestion.suggestion_id)
                    logger.info(
                        f"Created backtest suggestion: {suggestion.suggestion_id}"
                    )
            except Exception as exc:
                logger.error(f"Failed to create backtest suggestion: {exc}")

        # ---------------------------------------------------------------
        # 8. Update Job status based on success/failure counts
        # ---------------------------------------------------------------
        total_items = len(processed_results)
        success_count = sum(1 for r in processed_results if r.get("success"))
        error_count = total_items - success_count

        verdict = recommendations.get("verdict")
        if success_count == 0 and total_items == 0 and verdict == "current_is_best":
            final_status = JobStatus.COMPLETED
            logger.info(
                f"Backtesting job {job_id} completed: current model is best, no alternates to test"
            )
        elif success_count == 0:
            final_status = JobStatus.FAILED
            logger.error(
                f"Backtesting job {job_id} failed: 0/{total_items} items succeeded"
            )
        elif error_count > 0 or failed_chunk_ranges:
            # Degrade to PARTIALLY_COMPLETED when either LLM calls failed OR
            # some result spans could not be persisted after retry.  The latter
            # means the job's result data is incomplete even though inference
            # succeeded, so reporting COMPLETED would be misleading.
            final_status = JobStatus.PARTIALLY_COMPLETED
            if failed_chunk_ranges:
                logger.warning(
                    f"Backtesting job {job_id} partially completed: "
                    f"{success_count}/{total_items} items succeeded, "
                    f"{len(failed_chunk_ranges)} persist chunk(s) lost "
                    f"({', '.join(failed_chunk_ranges)})"
                )
            else:
                logger.warning(
                    f"Backtesting job {job_id} partially completed: {success_count}/{total_items} items succeeded"
                )
        else:
            final_status = JobStatus.COMPLETED
            logger.info(
                f"Backtesting job {job_id} completed: {success_count}/{total_items} items succeeded"
            )

        try:
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if job:
                    job.status = final_status.value
                    # Preserve scored_count_at_creation from the initial parameters
                    # so validate_backtesting_eligibility can read it and advance
                    # the threshold guard on the next scheduler run.
                    existing_params = (job.result or {}).get("parameters", {})
                    job.result = {
                        "backtest_run_id": str(backtest_run_id),
                        "prompt_id": prompt_id,
                        "current_model": current_model,
                        "models_tested": len(models),
                        "spans_tested": len(spans),
                        "spans_succeeded": success_count,
                        "spans_failed": error_count,
                        "persist_failures": failed_chunk_ranges or None,
                        "recommendations": recommendations,
                        "suggestion_id": results.get("suggestion_id"),
                        "parameters": existing_params,
                    }

                    # Advance threshold for COMPLETED and PARTIALLY_COMPLETED so the
                    # scheduler doesn't re-trigger until enough new spans accumulate.
                    if final_status in (
                        JobStatus.COMPLETED,
                        JobStatus.PARTIALLY_COMPLETED,
                    ):
                        scored_count_at_creation = existing_params.get(
                            "scored_count_at_creation", 0
                        )
                        prompt_result = await session.execute(
                            select(Prompt)
                            .where(
                                and_(
                                    Prompt.project_id == job.project_id,
                                    Prompt.slug == job.prompt_slug,
                                )
                            )
                            .order_by(Prompt.version.desc())
                            .limit(1)
                        )
                        prompt_obj = prompt_result.scalar_one_or_none()
                        if prompt_obj is not None:
                            existing_backtest_meta = prompt_obj.backtest_metadata or {}
                            # Advance threshold and clear any criteria_invalidated flag
                            # so the next criteria change can trigger another rollback.
                            prompt_obj.backtest_metadata = {
                                k: v
                                for k, v in existing_backtest_meta.items()
                                if k != "criteria_invalidated"
                            } | {"last_backtest_span_count": scored_count_at_creation}

                    await session.commit()
                    logger.info(f"Updated job {job_id} to {final_status.value}")
        except Exception as exc:
            logger.error(f"Failed to update job status: {exc}")

        # ---------------------------------------------------------------
        # 9. Mark backtest run as completed
        # ---------------------------------------------------------------
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(BacktestRun).where(
                    BacktestRun.backtest_run_id == backtest_run_id
                )
            )
            backtest_run = result.scalar_one()
            backtest_run.status = "completed"
            backtest_run.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(f"Marked backtest run {backtest_run_id} as completed")

        return results

    except Exception as e:
        logger.error(f"Backtesting failed: {str(e)}")

        # Mark the BacktestRun record as failed regardless of retry decision.
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(BacktestRun).where(
                    BacktestRun.backtest_run_id == backtest_run_id
                )
            )
            backtest_run = result.scalar_one_or_none()
            if backtest_run:
                backtest_run.status = "failed"
                backtest_run.completed_at = datetime.now(timezone.utc)
            await session.commit()

        # Retry up to 3 times by resetting the job to "pending" so the
        # reconciler re-dispatches it.  On the final failure, mark the job
        # as FAILED and advance the threshold so it does not re-trigger at
        # the same span count.
        MAX_JOB_RETRIES = 3
        try:
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if job:
                    from overmind.api.v1.endpoints.jobs import JobStatus as _JS

                    existing_params = (job.result or {}).get("parameters", {})
                    retry_count = existing_params.get("retry_count", 0)

                    if retry_count < MAX_JOB_RETRIES:
                        # Schedule a retry via the reconciler.
                        existing_params["retry_count"] = retry_count + 1
                        job.status = _JS.PENDING.value
                        job.result = {
                            **(job.result or {}),
                            "parameters": existing_params,
                            "last_error": str(e),
                        }
                        logger.warning(
                            f"Backtesting job {job_id} failed (attempt {retry_count + 1}/{MAX_JOB_RETRIES}), "
                            f"resetting to pending for retry"
                        )
                    else:
                        # Final failure — mark job FAILED and advance threshold.
                        job.status = _JS.FAILED.value
                        job.result = {
                            **(job.result or {}),
                            "parameters": existing_params,
                            "error": str(e),
                        }
                        logger.error(
                            f"Backtesting job {job_id} failed after {MAX_JOB_RETRIES} retries, "
                            f"marking as failed and advancing threshold"
                        )

                        # Advance threshold so the scheduler skips this span count.
                        scored_count_at_creation = existing_params.get(
                            "scored_count_at_creation", 0
                        )
                        if scored_count_at_creation:
                            prompt_result = await session.execute(
                                select(Prompt)
                                .where(
                                    and_(
                                        Prompt.project_id == job.project_id,
                                        Prompt.slug == job.prompt_slug,
                                    )
                                )
                                .order_by(Prompt.version.desc())
                                .limit(1)
                            )
                            prompt_obj = prompt_result.scalar_one_or_none()
                            if prompt_obj is not None:
                                existing_backtest_meta = (
                                    prompt_obj.backtest_metadata or {}
                                )
                                prompt_obj.backtest_metadata = {
                                    **existing_backtest_meta,
                                    "last_backtest_span_count": scored_count_at_creation,
                                }

                    await session.commit()
        except Exception:
            logger.exception("Failed to update job status after backtesting failure")

        raise


# ---------------------------------------------------------------------------
# Celery task: execute a single backtesting run (dispatched by reconciler)
# ---------------------------------------------------------------------------


@shared_task(name="backtesting.run_model_backtesting", bind=True)
def run_model_backtesting_task(
    self,
    prompt_id: str,
    models: list[str],
    span_count: int,
    user_id: str,
    organisation_id: str | None = None,
    *,
    job_id: str,
) -> dict[str, Any]:
    """Celery task to run model backtesting (dispatched by job reconciler)."""

    async def _run():
        from overmind.db.session import dispose_engine

        try:
            results = await _run_backtesting(
                prompt_id=prompt_id,
                models=models,
                span_count=span_count,
                user_id=user_id,
                organisation_id=organisation_id,
                celery_task_id=self.request.id,
                job_id=job_id,
            )
            return results
        finally:
            await dispose_engine()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Scheduled check: auto-create backtesting jobs for eligible prompts
# ---------------------------------------------------------------------------


async def validate_backtesting_eligibility(
    prompt: Prompt, session, models: list[str] | None = None
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """
    Validate if a prompt is eligible for backtesting.

    Used by both user-triggered (API) and system-triggered (Celery beat) paths
    so that all eligibility logic lives in one place.

    Args:
        prompt: The Prompt to validate
        session: Database session
        models: Optional list of models to test.  Pass a list (even empty) for
            user-triggered calls; leave as ``None`` for system-triggered calls.
            The distinction controls which checks are applied:

            ``models is None``  → **system-triggered** (Celery beat).
                All checks run, including the span-count threshold re-run guard
                (Check 5), which prevents the scheduler from running redundant
                jobs before enough new data has arrived.

            ``models is not None`` → **user-triggered** (API).
                Check 5 is skipped.  Users explicitly choosing a different model
                selection should not be blocked by the scheduler's throttle — the
                threshold guard exists to prevent wasteful automated reruns, not
                to restrict manual exploration.

    Returns:
        Tuple of (is_eligible, error_message, stats)
        - is_eligible: True if all checks pass
        - error_message: Reason if checks fail, None otherwise
        - stats: Dictionary with check results for debugging
    """
    from overmind.api.v1.endpoints.jobs import JobType, JobStatus

    prompt_id = prompt.prompt_id
    stats = {}

    # Check 1: User has completed the initial agent review (aligned the judge)
    agent_desc = prompt.agent_description or {}
    if not agent_desc.get("initial_review_completed"):
        return (
            False,
            "The agent hasn't been reviewed yet. Please complete the initial agent review to align the judge before backtesting can run.",
            stats,
        )

    # Check 2: Evaluation criteria exists
    criteria_dict = prompt.evaluation_criteria
    if (
        not criteria_dict
        or "correctness" not in criteria_dict
        or not criteria_dict["correctness"]
    ):
        return (
            False,
            "Evaluation criteria haven't been configured yet. Please set up scoring rules before running backtesting.",
            stats,
        )

    # Check 3: Prompt used recently (last 7 days)
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    recent_q = await session.execute(
        select(SpanModel.span_id)
        .where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.created_at >= cutoff,
                SpanModel.exclude_system_spans(),
            )
        )
        .limit(1)
    )
    if recent_q.scalar_one_or_none() is None:
        return (
            False,
            "This prompt hasn't had any traffic in the past 7 days. It needs to be actively used before backtesting can run.",
            stats,
        )

    # Check 4: Minimum scored spans
    scored_q = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.feedback_score.has_key("correctness"),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    scored_count = scored_q.scalar() or 0
    stats["scored_count"] = scored_count

    if scored_count < MIN_SPANS_FOR_BACKTESTING:
        return (
            False,
            f"Backtesting requires at least {MIN_SPANS_FOR_BACKTESTING} evaluated requests, but only {scored_count} have been scored.",
            stats,
        )

    # Check 5: Threshold-based re-run guard (system-triggered only)
    # Skipped for user-triggered calls (models is not None) — users explicitly
    # choosing a new model selection should not be blocked by the scheduler's
    # throttle.  See docstring for the full rationale.
    #
    # Threshold state is tracked in prompt.backtest_metadata["last_backtest_span_count"]
    # (written on every terminal job state including PARTIALLY_COMPLETED). Falls back
    # to a job-record query for prompts that haven't yet had a job complete under the
    # new tracking scheme (backward compatibility).
    if models is None:
        last_count = 0

        # Primary: read from prompt metadata (set on every terminal job state)
        backtest_meta = prompt.backtest_metadata or {}
        if backtest_meta.get("last_backtest_span_count"):
            last_count = backtest_meta["last_backtest_span_count"]
        else:
            # Fallback: query job records for prompts without metadata yet
            last_job_q = await session.execute(
                select(Job.result)
                .where(
                    and_(
                        Job.project_id == prompt.project_id,
                        Job.prompt_slug == prompt.slug,
                        Job.job_type == JobType.MODEL_BACKTESTING.value,
                        Job.status.in_(
                            [
                                JobStatus.COMPLETED.value,
                                JobStatus.PARTIALLY_COMPLETED.value,
                            ]
                        ),
                    )
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            last_job_result = last_job_q.scalar_one_or_none()
            if last_job_result and isinstance(last_job_result, dict):
                last_count = last_job_result.get("parameters", {}).get(
                    "scored_count_at_creation", 0
                )

        next_threshold = _next_backtest_threshold(last_count)
        stats["last_scored_count"] = last_count
        stats["next_threshold"] = next_threshold

        if scored_count < next_threshold:
            return (
                False,
                f"{scored_count} evaluated request(s) collected so far — backtesting will run automatically once {next_threshold} are reached.",
                stats,
            )

    # Check 6: Minimum available spans (for running the backtest itself).
    # Only scored spans are eligible so each span has a baseline correctness
    # value to compare against. Must match the inline query in _run_backtesting.
    available_q = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.input.isnot(None),
                SpanModel.exclude_system_spans(),
                SpanModel.feedback_score.has_key("correctness"),
            )
        )
    )
    available_span_count = available_q.scalar() or 0
    stats["available_spans"] = available_span_count

    if available_span_count < MIN_SPANS_FOR_BACKTESTING:
        return (
            False,
            f"Backtesting requires at least {MIN_SPANS_FOR_BACKTESTING} requests with input data, but only {available_span_count} are available.",
            stats,
        )

    # Check 7: No existing PENDING/RUNNING backtesting job
    existing_q = await session.execute(
        select(Job).where(
            and_(
                Job.project_id == prompt.project_id,
                Job.prompt_slug == prompt.slug,
                Job.job_type == JobType.MODEL_BACKTESTING.value,
                Job.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value]),
            )
        )
    )
    existing_job = existing_q.scalar_one_or_none()
    if existing_job:
        return (
            False,
            "A backtesting job is already in progress. Please wait for it to finish.",
            stats,
        )

    # Check 8: At least one model specified (for user-triggered)
    if models is not None and len(models) == 0:
        return False, "At least one model must be specified", stats

    # All checks passed!
    logger.info(
        f"Prompt {prompt_id} is eligible for backtesting: {scored_count} scored spans, "
        f"{available_span_count} available spans"
    )
    return True, None, stats


async def _check_and_create_backtesting_job(
    prompt: Prompt, session, celery_task_id
) -> dict[str, Any] | None:
    """
    Validate a prompt's eligibility for backtesting and create a PENDING job if eligible.

    Used by the system-triggered (Celery beat) path only. The user-triggered (API)
    path calls ``validate_backtesting_eligibility`` directly and creates the job
    itself. Both paths share the same eligibility logic through that function.
    """
    from overmind.api.v1.endpoints.jobs import JobType, JobStatus

    prompt_id = prompt.prompt_id

    (
        is_eligible,
        error_message,
        validation_stats,
    ) = await validate_backtesting_eligibility(prompt, session)

    if not is_eligible:
        # Surface pre-existing job skips distinctly so the caller can count them.
        if error_message and "already" in error_message:
            logger.info(
                f"Backtesting job already in progress for {prompt_id}, skipping"
            )
            return {
                "prompt_id": prompt_id,
                "status": "job_already_exists",
            }

        logger.info(
            f"Prompt {prompt_id} not eligible for backtesting, skipping: {error_message}"
        )
        return None

    # All eligibility checks passed — create a PENDING job entry.
    scored_count = validation_stats.get("scored_count") if validation_stats else None
    available_span_count = (
        validation_stats.get("available_spans", 0) if validation_stats else 0
    )
    span_count = min(available_span_count, MAX_SPANS_FOR_BACKTESTING)

    try:
        job = Job(
            job_id=uuid.uuid4(),
            job_type=JobType.MODEL_BACKTESTING.value,
            project_id=prompt.project_id,
            prompt_slug=prompt.slug,
            status=JobStatus.PENDING.value,
            triggered_by_user_id=None,  # system-triggered
            celery_task_id=celery_task_id,
            result={
                "parameters": {
                    "prompt_id": prompt_id,
                    "models": _models_from_suggestions(prompt),
                    "span_count": span_count,
                    "user_id": "system",
                    "organisation_id": None,
                    "scored_count_at_creation": scored_count,
                },
                "validation_stats": validation_stats,
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        logger.info(
            f"Created PENDING backtesting job for {prompt_id}, job_id: {job.job_id}"
        )
        return {
            "prompt_id": prompt_id,
            "status": "job_created",
            "job_id": str(job.job_id),
            "scored_count": scored_count,
        }
    except Exception:
        logger.exception("Failed to create backtesting job")
        return None


async def _check_backtesting_candidates(
    celery_task_id: str,
) -> dict[str, Any]:
    """
    Iterate all latest prompts and create PENDING backtesting jobs for
    eligible ones.  Mirrors ``_improve_prompt_templates`` in prompt_improvement.
    """
    from overmind.db.session import dispose_engine

    try:
        AsyncSessionLocal = get_session_local()
        async with AsyncSessionLocal() as session:
            # Get the active prompt version per (project_id, slug).
            # Periodic backtesting only runs against the version the user has
            # accepted (is_active=True), not pending tuning candidates.
            from overmind.api.v1.endpoints.utils.agents import (
                get_latest_prompts_for_project,
            )
            from overmind.models.iam.projects import Project as ProjectModel

            proj_result = await session.execute(
                select(ProjectModel.project_id).where(ProjectModel.is_active.is_(True))
            )
            project_ids = [row[0] for row in proj_result.all()]

            latest_prompts: list[Prompt] = []
            for pid in project_ids:
                latest_prompts.extend(
                    await get_latest_prompts_for_project(pid, session)
                )

            logger.info(
                f"Backtesting check: found {len(latest_prompts)} active prompts to evaluate"
            )

            job_results: list[dict[str, Any]] = []
            errors: list[str] = []

            for prompt in latest_prompts:
                try:
                    res = await _check_and_create_backtesting_job(
                        prompt, session, celery_task_id
                    )
                    if res:
                        job_results.append(res)
                except Exception:
                    msg = (
                        f"Failed to check/create backtesting job for {prompt.prompt_id}"
                    )
                    logger.exception(msg)
                    errors.append(msg)

            summary = {
                "status": "success",
                "prompts_checked": len(latest_prompts),
                "jobs_created": len(
                    [r for r in job_results if r.get("status") == "job_created"]
                ),
                "jobs_already_exist": len(
                    [r for r in job_results if r.get("status") == "job_already_exists"]
                ),
                "job_results": job_results,
                "errors": errors,
            }

            logger.info(
                f"Backtesting check complete: {summary['prompts_checked']} checked, "
                f"{summary['jobs_created']} jobs created, "
                f"{summary['jobs_already_exist']} already exist, "
                f"{len(errors)} errors"
            )
            return summary

    except Exception as exc:
        logger.error(f"Backtesting candidate check failed: {exc}", exc_info=True)
        return {
            "status": "error",
            "error": str(exc),
            "prompts_checked": 0,
            "jobs_created": 0,
            "jobs_already_exist": 0,
            "job_results": [],
            "errors": [str(exc)],
        }
    finally:
        await dispose_engine()


@shared_task(name="backtesting.check_backtesting_candidates", bind=True)
@with_task_lock(lock_name="backtesting_check")
def check_backtesting_candidates(self) -> dict[str, Any]:
    """
    Celery periodic task: scan prompts and create PENDING backtesting jobs.

    Runs on schedule via Celery Beat.  The job reconciler will pick up
    the created PENDING jobs and dispatch the actual backtesting work.
    """
    return asyncio.run(_check_backtesting_candidates(celery_task_id=self.request.id))
