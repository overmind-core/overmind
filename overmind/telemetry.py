"""
Anonymous usage telemetry — reports aggregate feature-usage signals to PostHog.

What IS collected:
  - A random installation UUID (generated once, stored in Valkey — no TTL)
  - Overmind version
  - Which LLM providers are configured (boolean flags, no keys or values)
  - Which pipeline features have ever been used (boolean flags, no content)
  - A rough scale bucket derived from trace count (<1k / 1k–10k / 10k+)
  - Job intent split: manual vs auto-triggered, per job type
  - Prompt-tuning value signals: correctness delta, latency delta, suggestions
    per run, acceptance rate — all averaged across suggestions, no content
  - Backtesting value signals: score delta, latency delta, cost delta per
    switch recommendation — all averaged, no content

What is NOT collected:
  - Trace content, prompt text, LLM inputs or outputs
  - User emails, names, or any PII
  - Project names or any customer-identifiable data
  - Exact counts beyond the scale bucket

Opt out: set OVERMIND_ANALYTICS_ENABLED=false in your .env (or environment).
"""

import posthog
import logging
from datetime import datetime, timezone

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.config import APP_VERSION, settings
from overmind.models.iam.projects import Project
from overmind.models.iam.users import User
from overmind.models.jobs import Job
from overmind.models.prompts import Prompt
from overmind.models.suggestions import Suggestion
from overmind.models.traces import SpanModel, TraceModel

logger = logging.getLogger(__name__)

INSTALLATION_ID_KEY = "overmind:installation_id"

ph: posthog.Posthog | None = None


def _scale_bucket(trace_count: int) -> str:
    if trace_count < 1_000:
        return "small"
    if trace_count < 10_000:
        return "medium"
    return "large"


def _round(value: float | None, places: int = 4) -> float | None:
    return round(value, places) if value is not None else None


class TelemetryReporter:
    """Collects anonymous aggregate usage stats and ships them to PostHog."""

    async def collect(self, db: AsyncSession) -> dict:
        """Build the heartbeat payload from aggregate DB queries.

        All queries read only counts, averages, and deltas — never content.
        """
        from overmind.db.valkey import get_key

        installation_id = await get_key(INSTALLATION_ID_KEY) or "unknown"

        # ── basic feature flags ────────────────────────────────────────────
        # One COUNT per feature — no content is read, only existence
        agent_count = await db.scalar(select(func.count()).select_from(Prompt)) or 0

        # Spans scored by the LLM judge have 'judge_feedback' in feedback_score
        eval_count = (
            await db.scalar(
                select(func.count())
                .select_from(SpanModel)
                .where(SpanModel.feedback_score.has_key("judge_feedback"))
            )
            or 0
        )

        # Tuning suggestions persist even after jobs are cleaned up
        tuning_suggestion_count = (
            await db.scalar(
                select(func.count())
                .select_from(Suggestion)
                .where(Suggestion.scores.has_key("avg_correctness_new"))
            )
            or 0
        )

        # Backtest spans have operations prefixed with "backtest:"
        backtest_span_count = (
            await db.scalar(
                select(func.count())
                .select_from(SpanModel)
                .where(SpanModel.operation.like("backtest:%"))
            )
            or 0
        )

        proxy_count = (
            await db.scalar(
                select(func.count())
                .select_from(TraceModel)
                .where(TraceModel.source == "proxy")
            )
            or 0
        )

        trace_count = await db.scalar(select(func.count()).select_from(TraceModel)) or 0
        user_count = await db.scalar(select(func.count()).select_from(User)) or 0
        project_count = await db.scalar(select(func.count()).select_from(Project)) or 0

        # ── job intent: manual vs auto per job type ────────────────────────
        # triggered_by_user_id IS NULL  → system-triggered (auto)
        # triggered_by_user_id IS NOT NULL → user-triggered (manual)
        # Note: system-triggered terminal jobs are cleaned up after 24 h;
        # user-triggered jobs persist. Auto counts therefore reflect recent
        # pending/running activity while manual counts are cumulative.
        job_intent = await self._collect_job_intent(db)

        # ── prompt-tuning value signals ────────────────────────────────────
        # Derived from Suggestion.scores JSONB which stores before/after
        # correctness, latency and cost per improvement run.
        tuning_value = await self._collect_tuning_value(db)

        # ── backtesting value signals ──────────────────────────────────────
        # Derived from Suggestion.scores JSONB for backtest suggestions
        # (identified by the 'current_avg_score' key).
        backtest_value = await self._collect_backtest_value(db)

        return {
            "installation_id": installation_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "version": APP_VERSION,
            "provider_flags": {
                "openai": bool(settings.openai_api_key),
                "anthropic": bool(settings.anthropic_api_key),
                "gemini": bool(settings.gemini_api_key),
            },
            "features_active": {
                "agent_discovery": agent_count > 0,
                "llm_evaluation": eval_count > 0,
                "prompt_tuning": tuning_suggestion_count > 0,
                "backtesting": backtest_span_count > 0,
                "proxy": proxy_count > 0,
            },
            "scale_bucket": _scale_bucket(trace_count),
            "counts": {
                "users": user_count,
                "projects": project_count,
            },
            "job_intent": job_intent,
            "tuning_value": tuning_value,
            "backtest_value": backtest_value,
        }

    async def _collect_job_intent(self, db: AsyncSession) -> dict:
        """Manual vs auto split per relevant job type."""
        job_types = ["judge_scoring", "prompt_tuning", "model_backtesting"]
        result: dict = {}
        for job_type in job_types:
            row = (
                await db.execute(
                    select(
                        func.count(
                            case((Job.triggered_by_user_id.isnot(None), 1))
                        ).label("manual"),
                        func.count(case((Job.triggered_by_user_id.is_(None), 1))).label(
                            "auto"
                        ),
                    ).where(Job.job_type == job_type)
                )
            ).one()
            result[job_type] = {"manual": row.manual, "auto": row.auto}
        return result

    async def _collect_tuning_value(self, db: AsyncSession) -> dict:
        """Aggregate correctness/latency deltas from prompt-tuning suggestions.

        Tuning suggestions are identified by the presence of 'avg_correctness_new'
        in their scores JSONB. Deltas = new − old (positive = improvement).
        """
        row = (
            await db.execute(
                select(
                    func.count().label("total"),
                    func.count(func.distinct(Suggestion.job_id)).label("unique_jobs"),
                    func.avg(
                        cast(Suggestion.scores["avg_correctness_new"].astext, Float)
                        - cast(Suggestion.scores["avg_correctness_old"].astext, Float)
                    ).label("avg_correctness_delta"),
                    func.avg(
                        cast(Suggestion.scores["avg_latency_ms_new"].astext, Float)
                        - cast(Suggestion.scores["avg_latency_ms_old"].astext, Float)
                    ).label("avg_latency_delta_ms"),
                    func.count(case((Suggestion.status == "accepted", 1))).label(
                        "accepted"
                    ),
                    func.count(case((Suggestion.status == "dismissed", 1))).label(
                        "dismissed"
                    ),
                ).where(Suggestion.scores.has_key("avg_correctness_new"))
            )
        ).one()

        total = row.total or 0
        unique_jobs = row.unique_jobs or 0
        return {
            "suggestions_total": total,
            "unique_job_runs": unique_jobs,
            "avg_suggestions_per_run": (
                round(total / unique_jobs, 2) if unique_jobs > 0 else None
            ),
            "avg_correctness_delta": _round(row.avg_correctness_delta),
            "avg_latency_delta_ms": _round(row.avg_latency_delta_ms, 1),
            "accepted": row.accepted,
            "dismissed": row.dismissed,
        }

    async def _collect_backtest_value(self, db: AsyncSession) -> dict:
        """Aggregate score/latency/cost deltas from backtesting suggestions.

        Backtest suggestions are identified by the presence of 'current_avg_score'
        in their scores JSONB. Deltas = recommended − current (negative latency/cost
        means the recommended model is faster/cheaper).
        """
        row = (
            await db.execute(
                select(
                    func.count().label("total"),
                    func.count(func.distinct(Suggestion.job_id)).label("unique_jobs"),
                    func.avg(
                        cast(Suggestion.scores["recommended_avg_score"].astext, Float)
                        - cast(Suggestion.scores["current_avg_score"].astext, Float)
                    ).label("avg_score_delta"),
                    func.avg(
                        cast(
                            Suggestion.scores["recommended_avg_latency_ms"].astext,
                            Float,
                        )
                        - cast(
                            Suggestion.scores["current_avg_latency_ms"].astext, Float
                        )
                    ).label("avg_latency_delta_ms"),
                    func.avg(
                        cast(Suggestion.scores["recommended_avg_cost"].astext, Float)
                        - cast(Suggestion.scores["current_avg_cost"].astext, Float)
                    ).label("avg_cost_delta"),
                ).where(Suggestion.scores.has_key("current_avg_score"))
            )
        ).one()

        return {
            "suggestions_total": row.total or 0,
            "unique_job_runs": row.unique_jobs or 0,
            "avg_score_delta": _round(row.avg_score_delta),
            "avg_latency_delta_ms": _round(row.avg_latency_delta_ms, 1),
            "avg_cost_delta": _round(row.avg_cost_delta, 6),
        }

    def send(self, payload: dict) -> None:
        """Ship the payload to PostHog. Always silent on failure."""
        if not settings.posthog_api_key:
            logger.debug("PostHog API key not set — skipping telemetry send")
            return

        try:
            global ph
            if ph is None:
                ph = posthog.Posthog(
                    settings.posthog_api_key, host="https://eu.i.posthog.com"
                )

            installation_id = payload.get("installation_id", "unknown")
            properties = {k: v for k, v in payload.items() if k != "installation_id"}
            ph.capture("heartbeat", distinct_id=installation_id, properties=properties)
            logger.debug("Telemetry heartbeat sent (installation=%s)", installation_id)
        except Exception as exc:
            logger.debug("Telemetry send failed (non-critical): %s", exc)


def shutdown() -> None:
    """Shutdown the PostHog client."""
    global ph
    if ph is not None:
        ph.shutdown()
        ph = None
