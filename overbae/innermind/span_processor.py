from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from overbae.innermind import evidence
from overbae.innermind.evals import evaluator_factory, expectation_rows, matches
from overbae.innermind.metrics import answers_for_capability, mean_score
from overbae.models import EvalMatrix, EvalScore, Span

logger = logging.getLogger(__name__)

EVAL_SCORE_BATCH = 500
TRACE_SPAN_CAP = 2_000
MAX_CONCURRENT_SPANS = 64
# A claim older than this belongs to a worker that died; the sweep takes it over.
CLAIM_TTL = timedelta(minutes=10)
# Spans waiting on an ancestor are deferred; past this many passes they are scored
# with what has arrived, so an orphan never waits forever.
MAX_DEFERRALS = 30
# The ingest task owns fresh spans; the sweep only re-drives older ones.
SWEEP_GRACE = timedelta(seconds=90)


class SpanProcessor:
    """Scores one span against the matching matrix rows and its own expectations."""

    def __init__(self, project_id: str, explain_model: str = ""):
        self.project_id = str(project_id)
        self.explain_model = explain_model

    def takes_unscoped_expectations(self, span: Span, trace: dict[str, Span]) -> bool:
        """Unscoped llm rows describe the capability's answer."""
        return answers_for_capability(span, trace)

    def rows(
        self, span: Span, matrix: list[EvalMatrix], trace: dict[str, Span]
    ) -> list[EvalMatrix]:
        rows: dict[str, EvalMatrix] = {}
        for row in matrix:
            if not matches(row, span):
                continue
            unscoped = not row.span_type and not row.span_name
            if (
                row.type == EvalMatrix.Type.LLM
                and unscoped
                and not self.takes_unscoped_expectations(span, trace)
            ):
                continue
            rows[row.name] = row
        for row in expectation_rows(span):
            rows.setdefault(row.name, row)
        return list(rows.values())

    async def evaluate(
        self,
        span: Span,
        matrix: list[EvalMatrix],
        trace: dict[str, Span] | None = None,
        *,
        fallback_intent: str = "",
        final: bool = False,
    ) -> Span:
        """Writes "score" only once the result is final; a span without it is retried."""
        trace = trace or {span.span_id: span}
        intent = evidence.read_intent(span, trace, fallback_intent)
        rows = self.rows(span, matrix, trace)
        needs_trace = any(row.type == EvalMatrix.Type.DETERMINISTIC for row in rows)
        root_arrived = any(node.is_root for node in trace.values())
        if not final and (intent is None or (needs_trace and not root_arrived)):
            span.eval_attributes = {
                **span.eval_attributes,
                "retry": span.eval_attributes.get("retry", 0) + 1,
            }
            return span

        span.eval_attributes = {
            "version": span.eval_attributes.get("version", 0) + 1,
            "intent": intent or fallback_intent,
        }
        nodes = list(trace.values())
        metrics = await asyncio.gather(
            *(
                evaluator_factory.get_evaluator(row, self.project_id, self.explain_model).score(
                    row, span, nodes
                )
                for row in rows
            )
        )
        span.eval_attributes |= {
            "score": mean_score(metrics),
            "metrics": {
                row.name: metric.as_dict(row.type)
                for row, metric in zip(rows, metrics, strict=True)
            },
        }
        return span


class LLMSpanProcessor(SpanProcessor):
    pass


class WorkflowSpanProcessor(SpanProcessor):
    pass


class ToolSpanProcessor(SpanProcessor):
    def takes_unscoped_expectations(self, span: Span, trace: dict[str, Span]) -> bool:
        return False


class RetrievalSpanProcessor(ToolSpanProcessor):
    pass


class GenericSpanProcessor(SpanProcessor):
    pass


class SpanProcessorFactory:
    processors: dict[str, type[SpanProcessor]] = {
        Span.SpanType.LLM_CALL: LLMSpanProcessor,
        Span.SpanType.WORKFLOW: WorkflowSpanProcessor,
        Span.SpanType.TOOL_CALL: ToolSpanProcessor,
        "tool": ToolSpanProcessor,
        Span.SpanType.RETRIEVAL: RetrievalSpanProcessor,
    }

    def get_processor(self, span: Span) -> SpanProcessor:
        return self.processors.get(span.span_type, GenericSpanProcessor)(span.project_id)


span_processor_factory = SpanProcessorFactory()


def claim(span_ids: list[str]) -> list[str]:
    """Stale-safe claim: a span is taken by one worker at a time, and a dead
    worker's claim expires after ``CLAIM_TTL``."""
    now = timezone.now()
    with transaction.atomic():
        claimed = list(
            Span.objects.select_for_update(skip_locked=True)
            .filter(span_id__in=span_ids)
            .exclude(eval_attributes__has_key="score")
            .filter(Q(eval_claimed_at__isnull=True) | Q(eval_claimed_at__lt=now - CLAIM_TTL))
            .values_list("span_id", flat=True)
        )
        Span.objects.filter(span_id__in=claimed).update(eval_claimed_at=now)
    return claimed


def unscored_span_ids(limit: int) -> list[str]:
    now = timezone.now()
    return list(
        Span.objects.exclude(eval_attributes__has_key="score")
        .filter(received_at__lt=now - SWEEP_GRACE)
        .filter(Q(eval_claimed_at__isnull=True) | Q(eval_claimed_at__lt=now - CLAIM_TTL))
        .order_by("received_at")
        .values_list("span_id", flat=True)[:limit]
    )


async def enabled_matrix(project_id) -> list[EvalMatrix]:
    return [row async for row in EvalMatrix.objects.filter(project_id=project_id, enabled=True)]


async def load_traces(project_id, trace_ids: set[str]) -> dict[str, dict[str, Span]]:
    traces: dict[str, dict[str, Span]] = defaultdict(dict)
    async for span in Span.objects.filter(project_id=project_id, trace_id__in=trace_ids).order_by(
        "start_time_ns"
    )[: TRACE_SPAN_CAP * len(trace_ids)]:
        traces[span.trace_id][span.span_id] = span
    return traces


async def score_project_spans(project_id, spans: list[Span]) -> list[Span]:
    matrix = await enabled_matrix(project_id)
    traces = await load_traces(project_id, {span.trace_id for span in spans})
    slots = asyncio.Semaphore(MAX_CONCURRENT_SPANS)

    async def score(span: Span) -> Span:
        trace = traces.get(span.trace_id) or {}
        trace[span.span_id] = span
        fallback = span.capability.description if span.capability_id else ""
        final = span.eval_attributes.get("retry", 0) >= MAX_DEFERRALS
        try:
            async with slots:
                return await span_processor_factory.get_processor(span).evaluate(
                    span, matrix, trace, fallback_intent=fallback, final=final
                )
        except Exception as exc:  # noqa: BLE001 — still no "score", so the sweep retries it
            logger.warning("Scoring span %s failed: %s", span.span_id, exc)
            span.eval_attributes = {**span.eval_attributes, "error": f"{type(exc).__name__}: {exc}"}
            return span

    return await asyncio.gather(*(score(span) for span in spans))


async def released_span_ids(spans: list[Span]) -> list[str]:
    """Spans deferred while their root was missing, in traces whose root just arrived."""
    trace_ids = {span.trace_id for span in spans if span.is_root}
    if not trace_ids:
        return []
    return [
        span_id
        async for span_id in Span.objects.filter(
            trace_id__in=trace_ids, eval_attributes__has_key="retry", eval_claimed_at__isnull=True
        )
        .exclude(eval_attributes__has_key="score")
        .values_list("span_id", flat=True)
    ]


async def score_spans(span_ids: list[str]) -> tuple[list[str], list[str]]:
    """Claims, scores and writes one batch; returns the traces that gained a final span
    and the deferred spans this batch's roots released."""
    claimed = await sync_to_async(claim)(span_ids)
    if not claimed:
        return [], []
    spans = [
        span async for span in Span.objects.filter(span_id__in=claimed).select_related("capability")
    ]
    by_project: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_project[span.project_id].append(span)
    scored: list[Span] = []
    for project_id, project_spans in by_project.items():
        scored += await score_project_spans(project_id, project_spans)

    finals = [span for span in scored if "score" in span.eval_attributes]
    # Scores first: a failed span update retries and appends one extra history row.
    await EvalScore.objects.abulk_create(
        [
            EvalScore(
                project_id=span.project_id,
                source_type=EvalScore.SourceType.SPAN,
                source_id=span.span_id,
                score=span.eval_attributes["score"],
                metrics=span.eval_attributes["metrics"],
            )
            for span in finals
        ],
        batch_size=EVAL_SCORE_BATCH,
    )
    for span in scored:
        span.eval_claimed_at = None
    await Span.objects.abulk_update(scored, fields=["eval_attributes", "eval_claimed_at"])
    return sorted({span.trace_id for span in finals}), await released_span_ids(spans)
