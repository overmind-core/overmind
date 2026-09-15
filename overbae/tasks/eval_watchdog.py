"""``aggregate_run`` (the chord callback) is the only task that flips an EvalRun to COMPLETED,
and ``eval.py``'s ``link_error`` only fires for tasks that raise — neither covers a chord barrier
that silently never completes.

Staleness anchor: the pipeline mutates EvalRun exclusively via ``QuerySet.update()``, which
bypasses ``auto_now``, so the eval tasks stamp ``updated_at`` by hand; scoring progress is read
from ``Score.created_at`` instead. Stalled means neither advanced.
"""

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Max
from django.utils import timezone

from overbae.tasks.utils.task_lock import with_task_lock

logger = logging.getLogger(__name__)

# Zero DB progress this long while still RUNNING means a wedged barrier or worker.
# Wide enough to clear slow LLM-judge batches and the header tasks' retry backoff.
EVAL_RUN_STALL_MINUTES = 30


@shared_task(name="overbae.tasks.eval_watchdog.reap_stalled_eval_runs")
@with_task_lock(lock_name="eval_watchdog", timeout=900)
def reap_stalled_eval_runs() -> dict:
    from overbae.models import EvalRun, Score
    from overbae.tasks.eval import _fail_run, aggregate_run

    cutoff = timezone.now() - timedelta(minutes=EVAL_RUN_STALL_MINUTES)
    running = list(EvalRun.objects.filter(status=EvalRun.Status.RUNNING, updated_at__lt=cutoff))
    if not running:
        return {"reaped": 0, "finalized": 0}

    reaped = 0
    finalized = 0
    for run in running:
        last_score = Score.objects.filter(run=run).aggregate(m=Max("created_at"))["m"]
        last_activity = max(ts for ts in (run.updated_at, last_score) if ts is not None)
        if last_activity >= cutoff:
            continue

        # Every score is present but the callback never fired: finalize via the
        # same aggregation path rather than failing a run that actually finished.
        if _all_scores_present(run):
            try:
                aggregate_run(None, eval_run_id=str(run.id))
                finalized += 1
                logger.warning(
                    "eval_watchdog: finalized stalled run %s (all scores present)", run.id
                )
                continue
            except Exception:  # noqa: BLE001 — fall through to a loud failure
                logger.exception("eval_watchdog: finalize failed for %s; marking FAILED", run.id)

        if _fail_run(
            str(run.id),
            f"evaluation stalled: scoring did not complete within {EVAL_RUN_STALL_MINUTES} min "
            "(chord barrier did not fire)",
        ):
            reaped += 1
            logger.warning("eval_watchdog: marked stalled run %s FAILED", run.id)

    return {"reaped": reaped, "finalized": finalized}


def _all_scores_present(run) -> bool:
    """Mirrors the ``score_total`` math in
    :func:`overbae.api.eval_serializers.compute_run_progress`. Runs with errored or sampled-out
    samples deliberately never clear this bar — failing loudly beats presenting partial results
    as complete."""
    from overbae.models import EvalSample, Score

    total = EvalSample.objects.filter(run=run).count()
    evaluator_count = run.run_evaluators.filter(enabled=True).count()
    if not total or not evaluator_count:
        return False
    score_total = total * evaluator_count
    scored = (
        Score.objects.filter(run=run, sample__isnull=False)
        .values("sample_id", "run_evaluator_id")
        .distinct()
        .count()
    )
    return scored >= score_total
