"""Live span, trajectory and signal scoring, and evaluation runs:

ingest -> score_spans -> score_trajectories
run_eval_run -> dispatch_generation -> generate_row ... -> score_eval_run
"""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import batched

from celery import current_app, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache

from overbae.core import async_loop
from overbae.innermind import generation, runs, signals, span_processor, trajectory
from overbae.models import EvalRun, EvalRunRow

logger = logging.getLogger(__name__)

SPAN_BATCH = 200
SWEEP_LIMIT = 2_000
GENERATION_WAKEUP_KEY = "eval-generation:wakeup"
SIGNALS_LOCK_SECONDS = 30 * 60


@shared_task(name="overbae.tasks.innermind.score_spans")
def score_spans(*, span_ids: list[str]) -> dict:
    trace_ids = async_loop.run(span_processor.score_spans(span_ids))
    if trace_ids:
        score_trajectories.delay(trace_ids=trace_ids)
    return {"traces": len(trace_ids)}


def enqueue_span_scoring(spans: list[tuple[str, str]]) -> None:
    """Queues ``(trace_id, span_id)`` pairs in batches that keep a trace together, so each
    batch loads a trace once."""
    by_trace: dict[str, list[str]] = defaultdict(list)
    for trace_id, span_id in spans:
        by_trace[trace_id].append(span_id)
    batch: list[str] = []
    for span_ids in by_trace.values():
        if batch and len(batch) + len(span_ids) > SPAN_BATCH:
            score_spans.delay(span_ids=batch)
            batch = []
        for chunk in batched(span_ids, SPAN_BATCH, strict=False):
            batch += chunk
            if len(batch) >= SPAN_BATCH:
                score_spans.delay(span_ids=batch)
                batch = []
    if batch:
        score_spans.delay(span_ids=batch)


@shared_task(name="overbae.tasks.innermind.sweep_unscored_spans")
def sweep_unscored_spans() -> dict:
    spans = span_processor.unscored_spans(SWEEP_LIMIT)
    enqueue_span_scoring(spans)
    return {"spans": len(spans)}


@shared_task(name="overbae.tasks.innermind.score_trajectories")
def score_trajectories(*, trace_ids: list[str] | None = None) -> dict:
    trace_ids = trace_ids or trajectory.unscored_trajectory_ids()
    return {"scored": async_loop.run(trajectory.score_traces(trace_ids))}


@shared_task(name="overbae.tasks.innermind.run_signals")
def run_signals() -> dict:
    created = 0
    for project_id in signals.projects_with_recent_spans():
        # A run outlasting the beat would otherwise overlap the next one; the timeout frees
        # the lock of a worker that died mid-run.
        lock = f"signals:run:{project_id}"
        if not cache.add(lock, "1", timeout=SIGNALS_LOCK_SECONDS):
            continue
        try:
            created += async_loop.run(signals.SignalRunner(project_id).run())
        except Exception:
            logger.exception("Signals failed for project %s", project_id)
        finally:
            cache.delete(lock)
    return {"events": created}


@shared_task(name="overbae.tasks.innermind.run_eval_run")
def run_eval_run(*, eval_run_id: str) -> dict:
    outcome = runs.start(eval_run_id)
    if outcome == "generate":
        wake_generation_dispatcher()
    elif outcome == "score":
        hand_to_scoring(eval_run_id)
    return {"outcome": outcome or "skipped"}


def hand_to_scoring(eval_run_id: str) -> None:
    task_id = runs.hand_to_scoring(eval_run_id)
    if task_id:
        score_eval_run.apply_async(kwargs={"eval_run_id": eval_run_id}, task_id=task_id)


def wake_generation_dispatcher() -> None:
    # Every dispatch serialises on the scheduler row lock; a pending wakeup covers every
    # completion before it starts, and the 30s beat covers a lost message.
    try:
        if not cache.add(GENERATION_WAKEUP_KEY, "1", timeout=30):
            return
        try:
            dispatch_generation.delay()
        except Exception:
            cache.delete(GENERATION_WAKEUP_KEY)
            raise
    except Exception:
        logger.exception("generation dispatcher wakeup failed; the beat resumes it")


@shared_task(name="overbae.tasks.innermind.dispatch_generation")
def dispatch_generation() -> dict:
    try:
        cache.delete(GENERATION_WAKEUP_KEY)
    except Exception:
        logger.warning("generation wakeup key not cleared; it expires on its own", exc_info=True)
    with current_app.connection_for_write(
        connect_timeout=5,
        transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
    ) as connection:

        def publish(row_id: str, task_id: str) -> None:
            generate_row.apply_async(
                kwargs={"row_id": row_id}, task_id=task_id, connection=connection, retry=False
            )

        def publish_scoring(run_id: str, task_id: str) -> None:
            score_eval_run.apply_async(
                kwargs={"eval_run_id": run_id}, task_id=task_id, connection=connection, retry=False
            )

        return generation.dispatch(publish, publish_scoring)


# A claimed admission is never replayed after a worker loss; the dispatcher records an
# unknown outcome once the running lease expires. Prefork enforces the time limit.
@shared_task(
    name="overbae.tasks.innermind.generate_row",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=1200,
    time_limit=1320,
)
def generate_row(self, *, row_id: str) -> str:
    if not generation.claim(row_id, self.request.id):
        return row_id
    try:
        row = EvalRunRow.objects.select_related("run__cell", "run__model_ref").get(pk=row_id)
        generation.generate(row)
    except generation.GenerationStoppedError:
        pass
    except SoftTimeLimitExceeded:
        record_row_error(row_id, "generation timed out (exceeded soft_time_limit)")
    except Exception as exc:  # noqa: BLE001 — the row records the failure; the run carries on
        logger.exception("generation failed for row %s", row_id)
        record_row_error(row_id, str(exc))
    finally:
        generation.finish(row_id, self.request.id)
        wake_generation_dispatcher()
    return row_id


def record_row_error(row_id: str, error: str) -> None:
    EvalRunRow.objects.filter(pk=row_id).exclude(run__status__in=EvalRun.TERMINAL).update(
        error=error
    )


@shared_task(
    name="overbae.tasks.innermind.score_eval_run",
    bind=True,
    soft_time_limit=7000,
    time_limit=7200,
)
def score_eval_run(self, *, eval_run_id: str) -> dict:
    try:
        scored = runs.score(eval_run_id, self.request.id)
    except SoftTimeLimitExceeded:
        runs.fail(eval_run_id, "scoring timed out")
        scored = False
    return {"scored": scored}


@shared_task(name="overbae.tasks.innermind.reap_stalled_runs")
def reap_stalled_runs() -> dict:
    return {"reaped": runs.reap_stalled()}
