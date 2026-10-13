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
from overbae.innermind.metrics import Metric, answers_for_capability, mean_of
from overbae.models import EvalMatrix, EvalScore, Span

logger = logging.getLogger(__name__)

EVAL_SCORE_BATCH = 500
TRACE_SPAN_CAP = 2_000
MAX_CONCURRENT_SPANS = 64
# A claim older than this belongs to a worker that died; the sweep takes it over.
CLAIM_TTL = timedelta(minutes=10)
# Judging passes per span, counted before the judges are called so a killed or hung pass
# counts too. A failing row on the last pass, or every row still missing once the passes are
# spent, is recorded as an error metric and the span is scored.
MAX_ATTEMPTS = 3
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

    def applies(self, row: EvalMatrix, span: Span) -> bool:
        return matches(row, span)

    def rows(
        self, span: Span, matrix: list[EvalMatrix], trace: dict[str, Span]
    ) -> list[EvalMatrix]:
        rows: dict[str, EvalMatrix] = {}
        for row in matrix:
            if not self.applies(row, span):
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
        judge: bool = True,
        last_attempt: bool = False,
    ) -> Span:
        """Writes "score" only once the result is final; a span without it is retried.

        The span is judged with what has arrived: an ancestor or the root that lands later
        never changes its score. A row's metric is kept in ``attrs["partial"]`` the moment
        it is computed, so a retry judges only the rows that failed. A failing row raises
        its error unless this is the ``last_attempt``; then it, like every missing row when
        ``judge`` is off, is recorded as an error metric and the span is scored with the
        rest."""
        trace = trace or {span.span_id: span}
        attrs = span.eval_attributes

        intent = evidence.read_intent(span, trace, fallback_intent)
        rows = self.rows(span, matrix, trace)
        span.eval_attributes = {**attrs, "intent": intent or fallback_intent}
        nodes = list(trace.values())
        done: dict[str, dict] = dict(attrs.get("partial") or {})
        # Rows with the same expectation are judged once per span.
        distinct = (
            {(row.type, row.source): row for row in rows if row.name not in done}.values()
            if judge
            else []
        )
        outcomes = await asyncio.gather(
            *(
                evaluator_factory.get_evaluator(row, self.project_id, self.explain_model).score(
                    row, span, nodes
                )
                for row in distinct
            ),
            return_exceptions=True,
        )
        by_source = {}
        failed: list[Exception] = []
        for row, outcome in zip(distinct, outcomes, strict=True):
            if isinstance(outcome, Exception):
                failed.append(outcome)
            elif isinstance(outcome, BaseException):
                raise outcome
            else:
                by_source[(row.type, row.source)] = outcome
        for row in rows:
            if row.name not in done and (row.type, row.source) in by_source:
                done[row.name] = by_source[(row.type, row.source)].as_dict(row.type)

        if failed and not last_attempt:
            span.eval_attributes = {
                **span.eval_attributes,
                "partial": done,
                "error": f"{type(failed[0]).__name__}: {failed[0]}",
            }
            raise failed[0]
        for row in rows:
            done.setdefault(
                row.name,
                Metric(row.name, error=f"scoring failed after {MAX_ATTEMPTS} attempts").as_dict(
                    row.type
                ),
            )
        metrics = {row.name: done[row.name] for row in rows}
        span.eval_attributes = {
            "version": attrs.get("version", 0) + 1,
            "intent": intent or fallback_intent,
            "fingerprint": evidence.fingerprint(span),
            "score": mean_of(metric["score"] for metric in metrics.values()),
            "metrics": metrics,
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


def unscored_spans(limit: int) -> list[tuple[str, str]]:
    """``(trace_id, span_id)`` of unscored spans past the grace period, newest first, so
    old spans cannot starve fresh ones."""
    now = timezone.now()
    return list(
        Span.objects.exclude(eval_attributes__has_key="score")
        .filter(received_at__lt=now - SWEEP_GRACE)
        .filter(Q(eval_claimed_at__isnull=True) | Q(eval_claimed_at__lt=now - CLAIM_TTL))
        .order_by("-received_at")
        .values_list("trace_id", "span_id")[:limit]
    )


async def enabled_matrix(project_id) -> list[EvalMatrix]:
    return [row async for row in EvalMatrix.objects.filter(project_id=project_id, enabled=True)]


async def load_traces(
    project_id, spans: list[Span], *, neighbours: bool
) -> dict[str, dict[str, Span]]:
    """What a batch reads of its traces: the roots and each span's ancestors, plus its
    parent's children and its own when a deterministic row reads them. The rest of a
    trace is never loaded, so one span does not read a large trace whole."""
    trace_ids = {span.trace_id for span in spans}
    edges = [
        edge
        async for edge in Span.objects.filter(project_id=project_id, trace_id__in=trace_ids)
        .order_by("start_time_ns")
        .values_list("span_id", "parent_span_id")[: TRACE_SPAN_CAP * len(trace_ids)]
    ]
    parent_of = dict(edges)
    children: dict[str, list[str]] = defaultdict(list)
    for span_id, parent in edges:
        if parent:
            children[parent].append(span_id)
    wanted = {span_id for span_id, parent in edges if not parent}
    for span in spans:
        node = span.parent_span_id
        while node in parent_of and node not in wanted:
            wanted.add(node)
            node = parent_of[node]
        if neighbours:
            wanted.update(children[span.span_id])
            if span.parent_span_id:
                wanted.update(children[span.parent_span_id])

    traces: dict[str, dict[str, Span]] = defaultdict(dict)
    for span in spans:
        traces[span.trace_id][span.span_id] = span
    wanted -= {span.span_id for span in spans}
    async for span in Span.objects.filter(span_id__in=wanted).order_by("start_time_ns"):
        traces[span.trace_id][span.span_id] = span
    return traces


@sync_to_async
def record_attempt(span: Span) -> bool:
    """Stores a pass's attempt count before its judges run; False once the span is scored."""
    return bool(
        Span.objects.filter(span_id=span.span_id)
        .exclude(eval_attributes__has_key="score")
        .update(eval_attributes=span.eval_attributes)
    )


@sync_to_async
def persist(span: Span) -> bool:
    """Writes one span's result unless another pass already scored it, so a span's score and
    its history row are written once. Returns whether this call stored a final score."""
    with transaction.atomic():
        stored = (
            Span.objects.filter(span_id=span.span_id)
            .exclude(eval_attributes__has_key="score")
            .update(eval_attributes=span.eval_attributes, eval_claimed_at=None)
        )
        final = bool(stored) and "score" in span.eval_attributes
        if final:
            EvalScore.objects.create(
                project_id=span.project_id,
                source_type=EvalScore.SourceType.SPAN,
                source_id=span.span_id,
                score=span.eval_attributes["score"],
                metrics=span.eval_attributes["metrics"],
            )
    return final


async def score_project_spans(project_id, spans: list[Span]) -> list[Span]:
    """Scores and stores each span as it finishes; returns the spans that gained a final
    score."""
    matrix = await enabled_matrix(project_id)
    traces = await load_traces(
        project_id,
        spans,
        neighbours=any(row.type == EvalMatrix.Type.DETERMINISTIC for row in matrix),
    )
    slots = asyncio.Semaphore(MAX_CONCURRENT_SPANS)

    async def score(span: Span) -> Span | None:
        trace = traces[span.trace_id]
        fallback = span.capability.description if span.capability_id else ""
        processor = span_processor_factory.get_processor(span)
        async with slots:
            # Claimed only now, so a span waiting for a slot never holds a claim that expires.
            if not await sync_to_async(claim)([span.span_id]):
                return None
            attempts = span.eval_attributes.get("attempts", 0)
            spent = attempts >= MAX_ATTEMPTS
            if not spent:
                attempts += 1
                span.eval_attributes = {**span.eval_attributes, "attempts": attempts}
                if not await record_attempt(span):
                    return None
            try:
                await processor.evaluate(
                    span,
                    matrix,
                    trace,
                    fallback_intent=fallback,
                    judge=not spent,
                    last_attempt=attempts >= MAX_ATTEMPTS,
                )
            except Exception as exc:  # noqa: BLE001 — still no "score", so the sweep retries it
                logger.warning("Scoring span %s failed: %s", span.span_id, exc)
                span.eval_attributes = {
                    **span.eval_attributes,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            return span if await persist(span) else None

    return [span for span in await asyncio.gather(*map(score, spans)) if span]


async def score_spans(span_ids: list[str]) -> list[str]:
    """Scores a batch, storing each span as it finishes; returns the traces that gained a
    final span. A span that already holds a score is never judged again."""
    spans = [
        span
        async for span in Span.objects.filter(span_id__in=span_ids)
        .exclude(eval_attributes__has_key="score")
        .select_related("capability")
    ]
    by_project: dict[str, list[Span]] = defaultdict(list)
    for span in spans:
        by_project[span.project_id].append(span)
    finals: list[Span] = []
    for project_id, project_spans in by_project.items():
        finals += await score_project_spans(project_id, project_spans)
    return sorted({span.trace_id for span in finals})
