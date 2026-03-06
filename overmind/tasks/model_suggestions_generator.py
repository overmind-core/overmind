"""
Task to generate proactive model recommendations based on agent description.

Runs immediately after agent description creation so users can see which models
to consider before any backtesting jobs are triggered by the scheduler.

Unlike backtesting (which measures real performance on historical spans), these
suggestions are LLM-generated based on the agent's purpose, known model
characteristics, and the actual cost/latency observed in production spans.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from celery import shared_task
from pydantic import BaseModel, Field
from sqlalchemy import select, and_

from sqlalchemy.orm.attributes import flag_modified

from overmind.core.llms import (
    call_llm,
    try_json_parsing,
    get_model_description,
    get_model_reasoning_info,
    normalize_model_name,
)
from overmind.core.model_resolver import (
    TaskType,
    get_available_providers,
    resolve_model,
    BACKTEST_MODELS_BY_PROVIDER,
)
from overmind.db.session import get_session_local
from overmind.models.prompts import Prompt
from overmind.models.traces import SpanModel
from overmind.tasks.utils.prompts import (
    MODEL_SUGGESTIONS_SYSTEM_PROMPT,
    MODEL_SUGGESTIONS_GENERATION_PROMPT,
)
from overmind.utils import calculate_llm_usage_cost

logger = logging.getLogger(__name__)

_SPAN_SAMPLE_LIMIT = 100


# ---------------------------------------------------------------------------
# Span usage stats
# ---------------------------------------------------------------------------


async def _fetch_span_usage_stats(session, prompt_id: str) -> dict[str, Any]:
    """
    Compute average token counts, latency, and per-call cost from recent spans.

    Returns a dict with keys:
        has_data          – False when no usable spans were found
        current_model     – most-used model name (normalized)
        sample_size       – number of spans analysed
        avg_input_tokens  – average gen_ai.usage.input_tokens
        avg_output_tokens – average gen_ai.usage.output_tokens
        avg_latency_ms    – average call duration in milliseconds
        avg_cost_usd      – average cost per call at current model pricing
    """
    result = await session.execute(
        select(
            SpanModel.metadata_attributes,
            SpanModel.start_time_unix_nano,
            SpanModel.end_time_unix_nano,
        )
        .where(
            and_(
                SpanModel.prompt_id == prompt_id,
                SpanModel.metadata_attributes.isnot(None),
                SpanModel.exclude_system_spans(),
            )
        )
        .order_by(SpanModel.start_time_unix_nano.desc())
        .limit(_SPAN_SAMPLE_LIMIT)
    )
    rows = result.all()

    if not rows:
        return {"has_data": False}

    model_counts: dict[str, int] = {}
    input_tokens_list: list[int] = []
    output_tokens_list: list[int] = []
    latencies_ms: list[float] = []
    costs: list[float] = []

    for meta, start_ns, end_ns in rows:
        if not meta:
            continue

        # Model
        raw_model = meta.get("gen_ai.request.model") or meta.get("model", "")
        if raw_model:
            norm = normalize_model_name(raw_model)
            model_counts[norm] = model_counts.get(norm, 0) + 1

        # Tokens
        in_tok = int(meta.get("gen_ai.usage.input_tokens", 0) or 0)
        out_tok = int(meta.get("gen_ai.usage.output_tokens", 0) or 0)
        if in_tok or out_tok:
            input_tokens_list.append(in_tok)
            output_tokens_list.append(out_tok)

        # Latency
        if start_ns and end_ns and end_ns > start_ns:
            latencies_ms.append((end_ns - start_ns) / 1_000_000)

        # Cost
        if raw_model and (in_tok or out_tok):
            cost = calculate_llm_usage_cost(raw_model, in_tok, out_tok)
            if cost > 0:
                costs.append(cost)

    if not model_counts:
        return {"has_data": False}

    current_model = max(model_counts, key=lambda m: model_counts[m])
    n = len(input_tokens_list) or 1

    return {
        "has_data": True,
        "current_model": current_model,
        "sample_size": len(rows),
        "avg_input_tokens": sum(input_tokens_list) / n,
        "avg_output_tokens": sum(output_tokens_list) / n,
        "avg_latency_ms": sum(latencies_ms) / len(latencies_ms)
        if latencies_ms
        else 0.0,
        "avg_cost_usd": sum(costs) / len(costs) if costs else 0.0,
    }


# ---------------------------------------------------------------------------
# Model list + cost projections
# ---------------------------------------------------------------------------


def _format_available_models(span_stats: dict[str, Any] | None) -> str:
    """
    Build a human-readable model list filtered to configured providers.

    Each entry includes:
    - Capability description
    - Projected per-call cost (when span token data is available)
    - Reasoning capabilities (supported levels, whether required, adaptive vs budget-token)
    """
    available_providers = get_available_providers()

    has_tokens = bool(
        span_stats
        and span_stats.get("has_data")
        and (
            span_stats.get("avg_input_tokens", 0)
            or span_stats.get("avg_output_tokens", 0)
        )
    )
    avg_in = int(span_stats.get("avg_input_tokens", 0)) if has_tokens else 0
    avg_out = int(span_stats.get("avg_output_tokens", 0)) if has_tokens else 0

    lines: list[str] = []
    for provider, models in BACKTEST_MODELS_BY_PROVIDER.items():
        if provider not in available_providers:
            continue
        for model in models:
            description = get_model_description(model)

            # Cost projection
            if has_tokens:
                projected = calculate_llm_usage_cost(model, avg_in, avg_out)
                cost_str = (
                    f" | projected cost/call: ${projected:.6f}"
                    if projected > 0
                    else " | projected cost/call: N/A"
                )
            else:
                cost_str = ""

            # Reasoning capabilities
            r = get_model_reasoning_info(model)
            if not r["supports_reasoning"]:
                reasoning_str = " | reasoning: not supported"
            elif r["reasoning_required"]:
                levels = ", ".join(r["reasoning_levels"])
                reasoning_str = f" | reasoning: always on (effort levels: {levels})"
            elif r["adaptive_mode"] is False:
                reasoning_str = (
                    " | reasoning: optional (budget-token, enable with effort: on)"
                )
            else:
                levels = ", ".join(r["reasoning_levels"])
                reasoning_str = f" | reasoning: optional (effort levels: {levels})"

            lines.append(
                f"- {model} ({provider}): {description}{cost_str}{reasoning_str}"
            )

    return "\n".join(lines) if lines else "No models available."


def _format_span_stats(span_stats: dict[str, Any]) -> str:
    """Render the span usage stats into a concise human-readable block."""
    if not span_stats.get("has_data"):
        return "No production usage data available yet."

    cost_str = (
        f"${span_stats['avg_cost_usd']:.6f}/call"
        if span_stats["avg_cost_usd"] > 0
        else "N/A"
    )
    return (
        f"Current model in production: {span_stats['current_model']}\n"
        f"Sample size: {span_stats['sample_size']} spans\n"
        f"Average input tokens:  {span_stats['avg_input_tokens']:.0f}\n"
        f"Average output tokens: {span_stats['avg_output_tokens']:.0f}\n"
        f"Average latency:       {span_stats['avg_latency_ms']:.0f} ms\n"
        f"Average cost per call: {cost_str}\n"
        f"(Projected costs per model in <AvailableModels> use the same avg token counts.)"
    )


# ---------------------------------------------------------------------------
# LLM response schemas
# ---------------------------------------------------------------------------


class _ModelRecommendation(BaseModel):
    model: str
    provider: str
    category: Literal["best_overall", "most_capable", "fastest", "cheapest"]
    reasoning_effort: str | None = Field(
        default=None,
        description=(
            "Recommended reasoning setting for this model. "
            "null = no reasoning (use for fast/cheap tasks or models without reasoning). "
            "For adaptive models: 'low', 'medium', 'high', or 'max' (Opus 4.6 only). "
            "For budget-token models (effort: on): set to 'on'. "
            "For reasoning-required models: must be set to a valid effort level."
        ),
    )
    reason: str


class _ModelSuggestionsResponse(BaseModel):
    recommendations: list[_ModelRecommendation]


# ---------------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------------


async def _generate_model_suggestions(prompt_id: str) -> dict[str, Any]:
    """
    Generate LLM-based model recommendations for a prompt.

    Reads the agent description and recent span usage stats (cost/latency/tokens),
    computes projected costs for all available models, and calls the LLM to
    produce recommendations. Result stored in ``prompt.backtest_model_suggestions``.
    """
    AsyncSessionLocal = get_session_local()

    logger.info(f"Generating model suggestions for prompt {prompt_id}")

    async with AsyncSessionLocal() as session:
        try:
            project_id_str, version, slug = Prompt.parse_prompt_id(prompt_id)
            project_uuid = UUID(project_id_str)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid prompt_id format: {e}")
            raise

        prompt_result = await session.execute(
            select(Prompt).where(
                and_(
                    Prompt.project_id == project_uuid,
                    Prompt.version == version,
                    Prompt.slug == slug,
                )
            )
        )
        prompt = prompt_result.scalar_one_or_none()
        if not prompt:
            raise ValueError(f"Prompt not found: {prompt_id}")

        agent_desc_data = prompt.agent_description or {}
        agent_description = agent_desc_data.get("description", "").strip()
        if not agent_description:
            raise ValueError(
                f"Prompt {prompt_id} has no agent description yet — "
                "model suggestions require an agent description to exist first."
            )

        # Fetch production span stats (may have no data for brand-new agents)
        span_stats = await _fetch_span_usage_stats(session, prompt_id)
        if span_stats.get("has_data"):
            logger.info(
                f"Span stats for {prompt_id}: model={span_stats['current_model']}, "
                f"n={span_stats['sample_size']}, "
                f"avg_in={span_stats['avg_input_tokens']:.0f}, "
                f"avg_out={span_stats['avg_output_tokens']:.0f}, "
                f"latency={span_stats['avg_latency_ms']:.0f}ms, "
                f"cost=${span_stats['avg_cost_usd']:.6f}"
            )
        else:
            logger.info(
                f"No span data found for {prompt_id}; proceeding without cost context"
            )

        available_models_text = _format_available_models(span_stats)
        if available_models_text == "No models available.":
            logger.warning(
                f"No backtest models available for prompt {prompt_id} — skipping model suggestions"
            )
            return {
                "prompt_id": prompt_id,
                "skipped": True,
                "reason": "No models available",
            }

        current_usage_stats = _format_span_stats(span_stats)

        prompt_text = MODEL_SUGGESTIONS_GENERATION_PROMPT.format(
            agent_description=agent_description,
            available_models=available_models_text,
            current_usage_stats=current_usage_stats,
        )

        response, stats = call_llm(
            prompt_text,
            system_prompt=MODEL_SUGGESTIONS_SYSTEM_PROMPT,
            model=resolve_model(TaskType.AGENT_DESCRIPTION),
            response_format=_ModelSuggestionsResponse,
        )
        logger.debug(
            f"Model suggestions generated for prompt {prompt_id}: "
            f"latency={stats['response_ms']}ms, cost=${stats['response_cost']:.6f}"
        )

        result_data = try_json_parsing(response)

        recommendations = result_data.get("recommendations", [])

        if not recommendations:
            raise ValueError("LLM returned no model recommendations")

        suggestions_payload: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "recommendations": recommendations,
        }

        prompt.backtest_model_suggestions = suggestions_payload
        flag_modified(prompt, "backtest_model_suggestions")
        await session.commit()

        logger.info(
            f"Stored {len(recommendations)} model suggestion(s) for prompt {prompt_id}"
        )

        return {
            "prompt_id": prompt_id,
            "recommendations_count": len(recommendations),
        }


@shared_task(name="model_suggestions_generator.generate_model_suggestions")
def generate_model_suggestions_task(prompt_id: str) -> dict[str, Any]:
    """
    Celery task to generate proactive model recommendations for a newly discovered prompt.

    Called immediately after agent description creation so users can see model
    recommendations without waiting for a backtesting job to run.

    Args:
        prompt_id: String ID of the prompt (format: {project_id}_{version}_{slug})

    Returns:
        Dict with generation results.
    """

    async def _run() -> dict[str, Any]:
        from overmind.db.session import dispose_engine

        try:
            result = await _generate_model_suggestions(prompt_id)
            logger.info(f"Generated model suggestions for prompt {prompt_id}")
            return result
        except Exception:
            logger.exception(
                f"Failed to generate model suggestions for prompt {prompt_id}"
            )
            raise
        finally:
            await dispose_engine()

    return asyncio.run(_run())
