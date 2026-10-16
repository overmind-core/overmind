"""Live span, trajectory and signal scoring, and evaluation runs:

ingest -> score_spans -> score_trajectories
run_eval_run -> dispatch_generation -> generate_row ... -> score_eval_run
"""

from __future__ import annotations

import logging
from collections import defaultdict

from celery import current_app, shared_task
from celery.exceptions import SoftTimeLimitExceeded
from django.core.cache import cache

from overbae.core import async_loop
from overbae.innermind import admission, generation, runs, signals, span_processor, trajectory
from overbae.models import EvalRun, EvalRunRow, Span

logger = logging.getLogger(__name__)

SWEEP_LIMIT = 2_000
GENERATION_WAKEUP_KEY = "eval-generation:wakeup"


def wake_live_scoring() -> None:
    try:
        if cache.add("live-scoring:wakeup", "1", timeout=15):
            dispatch_live_scoring.delay()
    except Exception:
        logger.exception("Live scoring wakeup failed; periodic admission resumes it")


@shared_task(name="overbae.tasks.innermind.dispatch_live_scoring")
def dispatch_live_scoring() -> dict:
    cache.delete("live-scoring:wakeup")
    with current_app.connection_for_write(
        connect_timeout=5,
        transport_options={"socket_connect_timeout": 5, "socket_timeout": 5},
    ) as connection:
        return admission.dispatch(
            lambda work_id, token: execute_live_scoring.apply_async(
                kwargs={"work_id": work_id}, task_id=token, connection=connection, retry=False
            )
        )


@shared_task(
    name="overbae.tasks.innermind.execute_live_scoring",
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    time_limit=300,
)
def execute_live_scoring(self, *, work_id: int) -> dict:
    item = admission.claim(work_id, self.request.id)
    if item is None:
        return {"skipped": True}
    error = ""
    try:
        if item.kind == "span":
            trace_ids = async_loop.run(span_processor.score_spans([item.key]))
            if trace_ids:
                score_trajectories(trace_ids=trace_ids)
        elif item.kind == "trajectory":
            async_loop.run(trajectory.score_traces([item.key]))
        elif item.kind == "signals":
            async_loop.run(signals.SignalRunner(str(item.project_id)).run())
        else:
            raise ValueError(f"Unknown live scoring kind: {item.kind}")
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        admission.finish(work_id, self.request.id, error)
        wake_live_scoring()
    return {"completed": True}


@shared_task(name="overbae.tasks.innermind.score_spans")
def score_spans(*, span_ids: list[str]) -> dict:
    spans = list(
        Span.objects.filter(span_id__in=span_ids)
        .exclude(eval_attributes__has_key="score")
        .values_list("trace_id", "span_id")
    )
    enqueue_span_scoring(spans)
    return {"spans": len(spans)}


def enqueue_span_scoring(spans: list[tuple[str, str]]) -> None:
    by_project = defaultdict(list)
    for project_id, span_id in (
        Span.objects.filter(span_id__in=[span_id for _, span_id in spans])
        .exclude(eval_attributes__has_key="score")
        .values_list("project_id", "span_id")
    ):
        by_project[project_id].append(span_id)
    for project_id, span_ids in by_project.items():
        admission.enqueue(project_id, "span", span_ids)
    if by_project:
        wake_live_scoring()


@shared_task(name="overbae.tasks.innermind.sweep_unscored_spans")
def sweep_unscored_spans() -> dict:
    from django.db.models import Q
    from django.utils import timezone

    now = timezone.now()
    waiting = (
        Span.objects.exclude(eval_attributes__has_key="score")
        .filter(received_at__lt=now - span_processor.SWEEP_GRACE)
        .filter(
            Q(eval_claimed_at__isnull=True) | Q(eval_claimed_at__lt=now - span_processor.CLAIM_TTL)
        )
    )
    count = 0
    for project_id in waiting.order_by().values_list("project_id", flat=True).distinct():
        spans = list(
            waiting.filter(project_id=project_id)
            .order_by("received_at")
            .values_list("trace_id", "span_id")[:SWEEP_LIMIT]
        )
        enqueue_span_scoring(spans)
        count += len(spans)
    return {"spans": count}


@shared_task(name="overbae.tasks.innermind.score_trajectories")
def score_trajectories(*, trace_ids: list[str] | None = None) -> dict:
    trace_ids = trace_ids or trajectory.unscored_trajectory_ids()
    by_project = defaultdict(list)
    for project_id, trace_id in (
        Span.objects.filter(trace_id__in=trace_ids, parent_span_id__isnull=True)
        .exclude(eval_attributes__has_key="trajectory")
        .values_list("project_id", "trace_id")
    ):
        by_project[project_id].append(trace_id)
    for project_id, keys in by_project.items():
        admission.enqueue(project_id, "trajectory", keys)
    if by_project:
        wake_live_scoring()
    return {"queued": sum(map(len, by_project.values()))}


@shared_task(name="overbae.tasks.innermind.run_signals")
def run_signals() -> dict:
    projects = signals.projects_with_recent_spans()
    for project_id in projects:
        admission.enqueue(project_id, "signals", ["signals"])
    wake_live_scoring()
    return {"projects": len(projects)}


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
