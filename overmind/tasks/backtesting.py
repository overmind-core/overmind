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
import uuid as uuid_module
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
    normalize_llm_response_output,
    LLM_PROVIDER_BY_MODEL,
    normalize_model_name,
)
from overmind.core.model_resolver import get_available_backtest_models
from overmind.models.iam.projects import Project
from overmind.tasks.evaluations import _evaluate_correctness_with_llm, _format_criteria
from overmind.tasks.agent_discovery import _get_span_input_text_merged
from overmind.tasks.agentic_span_processor import _safe_parse_json
from overmind.tasks.task_lock import with_task_lock
from overmind.utils import calculate_llm_usage_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default models to test during backtesting.
# At runtime, get_default_backtest_models() filters this to providers that
# have an API key configured.  The full list is kept for reference /
# documentation and for callers that pass an explicit list.
# ---------------------------------------------------------------------------
_ALL_BACKTEST_MODELS: list[str] = [
    # OpenAI
    "gpt-5-mini",
    "gpt-5.2",
    "gpt-5-nano",
    # Anthropic
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    # Google Gemini
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]


def get_default_backtest_models() -> list[str]:
    """Return backtest models filtered to providers with available API keys."""
    return get_available_backtest_models()


# Minimum scored spans required before a prompt is eligible for backtesting
MIN_SPANS_FOR_BACKTESTING = 10
# Maximum spans to use per model during a backtest run
MAX_SPANS_FOR_BACKTESTING = 50

# Max concurrent (model × span) evaluations running in parallel.
# Matches the evaluations.py pattern – bounded by a shared asyncio.Semaphore
# so we don't overwhelm LLM provider rate-limits.
_MAX_CONCURRENT_BACKTESTS = 5

# Performance thresholds for model recommendations
_PERF_TOLERANCE = 0.05  # 5 percentage-point tolerance for speed/cost alternatives
_PERF_DISQUALIFY = 0.15  # 15pp drop → model is disqualified entirely

# Thresholds at which to re-run backtesting: 50, 100, 200, 500, 1000, 2000...
_INITIAL_THRESHOLDS = [50, 100, 200, 500, 1000]


def _next_backtest_threshold(last_count: int) -> int:
    """Return the next scored-span count at which backtesting should re-run."""
    for t in _INITIAL_THRESHOLDS:
        if last_count < t:
            return t
    return ((last_count // 1000) + 1) * 1000


# ---------------------------------------------------------------------------
# Provider-aware scheduling helpers
# ---------------------------------------------------------------------------


def _interleave_models_by_provider(models: list[str]) -> list[str]:
    """Reorder models so that consecutive entries target different providers.

    E.g. [gpt-5-mini, gpt-5.2, claude-opus, claude-sonnet, gemini-3-pro, gemini-3-flash]
      →  [gpt-5-mini, claude-opus, gemini-3-pro, gpt-5.2, claude-sonnet, gemini-3-flash]

    This spreads load across providers when tasks are processed through a
    concurrency-limited semaphore.
    """
    by_provider: dict[str, list[str]] = defaultdict(list)
    for model in models:
        provider = LLM_PROVIDER_BY_MODEL.get(model, "unknown")
        by_provider[provider].append(model)

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
        if model == current_model:
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


async def _fetch_spans_for_backtesting(prompt_id: str, limit: int) -> list[SpanModel]:
    """Fetch spans with inputs for backtesting (excludes system-generated spans)."""
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        stmt = (
            select(SpanModel)
            .where(
                and_(
                    SpanModel.prompt_id == prompt_id,
                    SpanModel.input.isnot(None),
                    SpanModel.exclude_system_spans(),
                )
            )
            .order_by(SpanModel.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _get_prompt_criteria(prompt_id: str) -> dict[str, list[str]]:
    """Get the evaluation criteria for a prompt_id.

    Raises ValueError if prompt not found or has no criteria.
    """
    AsyncSessionLocal = get_session_local()
    async with AsyncSessionLocal() as session:
        project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
        project_uuid = UUID(project_id_str)

        stmt = select(Prompt).where(
            and_(
                Prompt.project_id == project_uuid,
                Prompt.version == version,
                Prompt.slug == slug,
            )
        )
        result = await session.execute(stmt)
        prompt = result.scalar_one_or_none()

        if not prompt:
            raise ValueError(f"Prompt not found: {prompt_id}")

        criteria_dict = prompt.evaluation_criteria
        if (
            not criteria_dict
            or "correctness" not in criteria_dict
            or not criteria_dict["correctness"]
        ):
            raise ValueError(
                f"Prompt {prompt_id} does not have evaluation criteria. "
                "Please generate or define criteria before running backtesting."
            )

        return criteria_dict


def _run_model_on_input(
    model_name: str,
    input_text: str,
    messages: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a single model on an input and collect metrics.

    When ``messages`` is provided it is forwarded directly to ``call_llm``
    so the full conversation (including tool-call and tool-result turns) is
    replayed.  When ``tools`` is provided the tool definitions are forwarded
    so the model can make tool-call decisions.

    This is deliberately *synchronous* so it can be offloaded to a thread
    via ``asyncio.to_thread`` without blocking the event loop.
    """
    try:
        start_time = time.time()

        output, stats = call_llm(
            input_text=input_text,
            system_prompt=None,
            model=model_name,
            messages=messages,
            tools=tools,
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

    Improvements over the original sequential version:
    - All (model × span) pairs are evaluated **concurrently** via
      ``asyncio.gather`` bounded by ``_MAX_CONCURRENT_BACKTESTS``.
    - Models are interleaved by provider so concurrent requests spread
      across OpenAI / Anthropic / Gemini rather than hammering one.
    - After scoring, a recommendation heuristic produces an overall
      verdict (best performer, fastest, cheapest, best overall) and
      creates a ``Suggestion`` record when a switch is warranted.
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
        # 1. Fetch spans & criteria
        # ---------------------------------------------------------------
        logger.info(f"Fetching {span_count} spans for backtesting prompt {prompt_id}")
        spans = await _fetch_spans_for_backtesting(prompt_id, span_count)
        if not spans:
            raise ValueError(f"No spans found for backtesting prompt {prompt_id}")
        logger.info(f"Found {len(spans)} spans for backtesting")

        criteria_dict = await _get_prompt_criteria(prompt_id)
        criteria_text = _format_criteria(criteria_dict["correctness"])

        # Fetch project/agent context for evaluation prompts
        project_description: str | None = None
        agent_description: str | None = None
        async with AsyncSessionLocal() as ctx_session:
            try:
                project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
                project_uuid = UUID(project_id_str)
                prompt_result = await ctx_session.execute(
                    select(Prompt).where(
                        and_(
                            Prompt.project_id == project_uuid,
                            Prompt.version == version,
                            Prompt.slug == slug,
                        )
                    )
                )
                prompt_obj = prompt_result.scalar_one_or_none()
                if prompt_obj and prompt_obj.agent_description:
                    agent_description = prompt_obj.agent_description.get("description")
                project_result = await ctx_session.execute(
                    select(Project).where(Project.project_id == project_uuid)
                )
                project_obj = project_result.scalar_one_or_none()
                if project_obj and project_obj.description:
                    project_description = project_obj.description
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Could not fetch project/agent context for backtesting: {e}"
                )

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
        # 4. Process items concurrently (semaphore-bounded)
        # ---------------------------------------------------------------
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_BACKTESTS)

        async def _process_item(
            model_name: str, span: SpanModel, input_text: str
        ) -> dict[str, Any]:
            async with semaphore:
                parsed_span_input = _safe_parse_json(span.input)
                input_data = parsed_span_input or {}
                span_response_type = (span.metadata_attributes or {}).get(
                    "response_type"
                )

                if span_response_type:
                    # --------------------------------------------------------
                    # Tool-calling span: replay with full conversation + tools
                    # --------------------------------------------------------
                    # Pass the original message list directly so the model
                    # receives the same context (user turns, prior tool calls,
                    # tool results) that the source span had.
                    call_messages = (
                        parsed_span_input
                        if isinstance(parsed_span_input, list)
                        else None
                    )
                    call_tools = (span.metadata_attributes or {}).get(
                        "available_tools"
                    ) or []

                    model_result = await asyncio.to_thread(
                        _run_model_on_input,
                        model_name,
                        input_text,
                        messages=call_messages,
                        tools=call_tools if call_tools else None,
                    )

                    # Preserve response_type / is_agentic so the correct judge
                    # branch is used (tool-call or tool-answer evaluator).
                    backtest_metadata = span.metadata_attributes or {}
                    # The model received the full conversation, so evaluate
                    # against it (includes tool results in the input).
                    eval_input_data = input_data
                    # Output is already normalised to the message-list format
                    # by _run_model_on_input; the evaluator handles both dict
                    # and list formats via _safe_parse_json.
                    output_data = model_result.get("output") or ""
                else:
                    # --------------------------------------------------------
                    # Plain / legacy span: existing plain-text behaviour
                    # --------------------------------------------------------
                    model_result = await asyncio.to_thread(
                        _run_model_on_input, model_name, input_text
                    )

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
                    # Output is already normalised to the message-list format
                    output_data = model_result.get("output") or ""

                # Sync correctness eval → offload to thread
                eval_score = 0.0
                if model_result["success"] and model_result.get("output"):
                    eval_score = await asyncio.to_thread(
                        _evaluate_correctness_with_llm,
                        input_data=eval_input_data,
                        output_data=output_data,
                        criteria_text=criteria_text,
                        project_description=project_description,
                        agent_description=agent_description,
                        span_metadata=backtest_metadata,
                    )

                # Persist result span
                result_span_id = str(uuid.uuid4())
                current_time_nano = int(time.time() * 1_000_000_000)

                async with AsyncSessionLocal() as db:
                    result_span = SpanModel(
                        span_id=result_span_id,
                        operation=f"backtest:{model_name}",
                        start_time_unix_nano=current_time_nano,
                        end_time_unix_nano=current_time_nano
                        + int(model_result["latency_ms"] * 1_000_000),
                        input=input_data,
                        output=output_data if model_result.get("output") else None,
                        status_code=1 if model_result["success"] else 2,
                        metadata_attributes={
                            "backtest": True,
                            "backtest_run_id": str(backtest_run_id),
                            "source_span_id": span.span_id,
                            "model": model_name,
                            "latency_ms": model_result["latency_ms"],
                            "cost": model_result["cost"],
                            "input_tokens": model_result["input_tokens"],
                            "output_tokens": model_result["output_tokens"],
                            "error": model_result["error"],
                            "available_tools": call_tools if span_response_type else [],
                        },
                        feedback_score={"correctness": eval_score},
                        trace_id=span.trace_id,
                        prompt_id=prompt_id,
                    )
                    db.add(result_span)
                    await db.commit()

                return {
                    "model_name": model_name,
                    "span_id": span.span_id,
                    "result_span_id": result_span_id,
                    "input": input_data,
                    "output": model_result.get("output"),
                    "latency_ms": model_result["latency_ms"],
                    "cost": model_result["cost"],
                    "input_tokens": model_result["input_tokens"],
                    "output_tokens": model_result["output_tokens"],
                    "eval_score": eval_score,
                    "success": model_result["success"],
                    "error": model_result["error"],
                }

        all_results = await asyncio.gather(
            *[_process_item(m, s, t) for m, s, t in work_items],
            return_exceptions=True,
        )

        # Gracefully handle any per-item exceptions
        processed_results: list[dict[str, Any]] = []
        for i, res in enumerate(all_results):
            if isinstance(res, Exception):
                m_name, sp, _ = work_items[i]
                logger.error(f"Error processing {m_name} on span {sp.span_id}: {res}")
                processed_results.append(
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
                processed_results.append(res)

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

        if success_count == 0:
            final_status = JobStatus.FAILED
            logger.error(
                f"Backtesting job {job_id} failed: 0/{total_items} items succeeded"
            )
        elif error_count > 0:
            final_status = JobStatus.PARTIALLY_COMPLETED
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
                        "recommendations": recommendations,
                        "suggestion_id": results.get("suggestion_id"),
                        "parameters": existing_params,
                    }
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
            backtest_run.completed_at = datetime.utcnow()
            await session.commit()
            logger.info(f"Marked backtest run {backtest_run_id} as completed")

        return results

    except Exception as e:
        logger.error(f"Backtesting failed: {str(e)}")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(BacktestRun).where(
                    BacktestRun.backtest_run_id == backtest_run_id
                )
            )
            backtest_run = result.scalar_one_or_none()
            if backtest_run:
                backtest_run.status = "failed"
                backtest_run.completed_at = datetime.utcnow()
            await session.commit()

        try:
            async with AsyncSessionLocal() as session:
                job_result = await session.execute(
                    select(Job).where(Job.job_id == UUID(job_id))
                )
                job = job_result.scalar_one_or_none()
                if job:
                    from overmind.api.v1.endpoints.jobs import JobStatus as _JS

                    job.status = _JS.FAILED.value
                    job.result = {"error": str(e)}
                    await session.commit()
        except Exception as job_exc:
            logger.exception(f"Failed to update job status to failed: {job_exc}")

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
        models: Optional list of models to test (for user-triggered validation)

    Returns:
        Tuple of (is_eligible, error_message, stats)
        - is_eligible: True if all checks pass
        - error_message: Reason if checks fail, None otherwise
        - stats: Dictionary with check results for debugging
    """
    from overmind.api.v1.endpoints.jobs import JobType, JobStatus

    prompt_id = prompt.prompt_id
    stats = {}

    # Check 1: Evaluation criteria exists
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

    # Check 2: Prompt used recently (last 7 days)
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

    # Check 3: Minimum scored spans
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
            "Not enough evaluated requests have been collected yet to run backtesting. Keep using your application and try again later.",
            stats,
        )

    # Check 4: Threshold-based re-run guard (don't re-run until enough new spans)
    last_count = 0
    last_job_q = await session.execute(
        select(Job.result)
        .where(
            and_(
                Job.project_id == prompt.project_id,
                Job.prompt_slug == prompt.slug,
                Job.job_type == JobType.MODEL_BACKTESTING.value,
                Job.status == JobStatus.COMPLETED.value,
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
            "Not enough new requests have been collected since the last backtest. Continue using your application — backtesting will run automatically when ready.",
            stats,
        )

    # Check 5: Minimum available spans (for running the backtest itself)
    available_q = await session.execute(
        select(func.count(SpanModel.span_id)).where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.input.isnot(None),
                SpanModel.exclude_system_spans(),
            )
        )
    )
    available_span_count = available_q.scalar() or 0
    stats["available_spans"] = available_span_count

    if available_span_count < MIN_SPANS_FOR_BACKTESTING:
        return (
            False,
            "Not enough request data is available for backtesting yet. Keep using your application and try again later.",
            stats,
        )

    # Check 6: No existing PENDING/RUNNING backtesting job
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

    # Check 7: At least one model specified (for user-triggered)
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
            job_id=uuid_module.uuid4(),
            job_type=JobType.MODEL_BACKTESTING.value,
            project_id=prompt.project_id,
            prompt_slug=prompt.slug,
            status=JobStatus.PENDING.value,
            triggered_by_user_id=None,  # system-triggered
            celery_task_id=celery_task_id,
            result={
                "parameters": {
                    "prompt_id": prompt_id,
                    "models": get_default_backtest_models(),
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
            # Get latest version of each prompt slug
            subquery = (
                select(
                    Prompt.project_id,
                    Prompt.slug,
                    func.max(Prompt.version).label("max_version"),
                )
                .group_by(Prompt.project_id, Prompt.slug)
                .subquery()
            )

            result = await session.execute(
                select(Prompt).join(
                    subquery,
                    and_(
                        Prompt.project_id == subquery.c.project_id,
                        Prompt.slug == subquery.c.slug,
                        Prompt.version == subquery.c.max_version,
                    ),
                )
            )
            latest_prompts = result.scalars().all()

            logger.info(
                f"Backtesting check: found {len(latest_prompts)} prompts to evaluate"
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
                except Exception as exc:
                    msg = (
                        f"Failed to check/create backtesting job for "
                        f"{prompt.prompt_id}: {exc}"
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
