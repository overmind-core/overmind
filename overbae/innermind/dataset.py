"""Scores a run's dataset rows: each row and its output become one unsaved root span,
graded by the run's frozen matrix like a live trace."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from asgiref.sync import sync_to_async

from overbae.core.decisions import DecisionError
from overbae.innermind import generation
from overbae.innermind.span_processor import EVAL_SCORE_BATCH, SpanProcessor
from overbae.models import EvalMatrix, EvalRun, EvalRunRow, EvalScore, Span
from overbae.services.datasets import rows as row_store

MAX_CONCURRENT_ROWS = 64
ROW_ATTEMPTS = 3
RETRY_SECONDS = 5


def as_attribute(value: Any) -> str:
    """Ingested spans carry OTLP-flattened strings; rows match them so readers and checks agree."""
    if value is None or isinstance(value, str):
        return value or ""
    return json.dumps(value, ensure_ascii=False, default=str)


class DatasetRowProcessor(SpanProcessor):
    span_name = "dataset_row"

    def __init__(self, run: EvalRun):
        super().__init__(run.project_id, run.judge_model)
        self.run = run
        self.capability_id = run.dataset.capability_id if run.dataset_id else None

    def applies(self, row: EvalMatrix, span: Span) -> bool:
        """A dataset row is the capability's answer, so it takes every answer-level matrix
        row; a ``span_name`` names where that answer is produced live, which no dataset row
        is called."""
        return row.span_type in ("", span.span_type)

    def process(self, row: row_store.DatasetRow, run_row: EvalRunRow) -> Span:
        row = generation.prepared(row)
        seed = generation.row_seed(row)
        attributes = {
            "input": as_attribute(seed.messages or row.input),
            "output": as_attribute(run_row.output),
            "expected": as_attribute(generation.reference_of(row, self.run.mode)),
        }
        if self.run.mode == EvalRun.Mode.RECORDED and run_row.output == row.expected_output:
            del attributes["expected"]
        span_id = f"{run_row.row_index:016x}"
        return Span(
            span_id=span_id,
            trace_id=f"{self.run.pk.hex[:16]}{span_id}",
            project_id=self.run.project_id,
            capability_id=self.capability_id,
            span_type=Span.SpanType.LLM_CALL,
            name=self.span_name,
            attributes={k: v for k, v in attributes.items() if v},
            eval_attributes={},
        )


def scoreable(run: EvalRun) -> list[tuple[row_store.DatasetRow, EvalRunRow]]:
    run_rows = list(run.rows.filter(error="", degraded=False).exclude(output=None))
    if not run_rows:
        return []
    dataset_rows = {
        row.index: row for row in row_store.rows(run.cell, [r.row_index for r in run_rows])
    }
    return [(dataset_rows[r.row_index], r) for r in run_rows if r.row_index in dataset_rows]


async def score_rows(run: EvalRun) -> int:
    """Appends one EvalScore per scoreable row and returns how many were written. The
    run must arrive with ``dataset__capability`` and ``cell`` loaded."""
    pairs = await sync_to_async(scoreable)(run)
    processor = DatasetRowProcessor(run)
    matrix = [EvalMatrix.from_snapshot(data) for data in run.matrix]
    fallback = (
        run.dataset.capability.description if run.dataset_id and run.dataset.capability_id else ""
    )
    slots = asyncio.Semaphore(MAX_CONCURRENT_ROWS)

    async def score(row: row_store.DatasetRow, run_row: EvalRunRow) -> EvalScore | None:
        span = processor.process(row, run_row)
        for attempt in range(ROW_ATTEMPTS):
            try:
                async with slots:
                    await processor.evaluate(span, matrix, fallback_intent=fallback)
                break
            except DecisionError as exc:
                if attempt + 1 == ROW_ATTEMPTS:
                    await EvalRunRow.objects.filter(pk=run_row.pk).aupdate(
                        error=f"scoring failed: Jev {exc.reason}"
                    )
                    return None
                await asyncio.sleep(RETRY_SECONDS * 2**attempt)
        return EvalScore(
            project_id=run.project_id,
            source_type=EvalScore.SourceType.DATASET_ROW,
            source_id=f"{run.cell_id}:{run_row.row_index}",
            run=run,
            score=span.eval_attributes["score"],
            metrics=span.eval_attributes["metrics"],
        )

    results = await asyncio.gather(*(score(row, run_row) for row, run_row in pairs))
    scores = [score for score in results if score is not None]
    await EvalScore.objects.abulk_create(scores, batch_size=EVAL_SCORE_BATCH)
    return len(scores)
