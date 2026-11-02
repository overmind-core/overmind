from celery import shared_task

from overbae.services import rl_training
from overbae.tasks.utils.task_lock import with_task_lock

POLL_SECONDS = 4


@shared_task(name="overbae.tasks.rl_training.dispatch_rl_run")
def dispatch_rl_run(run_id: str) -> None:
    if rl_training.dispatch(run_id):
        poll_rl_run.apply_async((run_id,), countdown=POLL_SECONDS)


@shared_task(name="overbae.tasks.rl_training.poll_rl_run")
def poll_rl_run(run_id: str) -> None:
    if not rl_training.poll(run_id):
        poll_rl_run.apply_async((run_id,), countdown=POLL_SECONDS)


@shared_task(name="overbae.tasks.rl_training.reconcile_rl_runs")
# A lost countdown would stall a run; beat re-drives every active one.
@with_task_lock(lock_name="rl_training_reconciler", timeout=30)
def reconcile_rl_runs() -> dict:
    queued = rl_training.queued_run_ids()
    active = rl_training.active_run_ids()
    for run_id in queued:
        dispatch_rl_run.delay(run_id)
    for run_id in active:
        poll_rl_run.delay(run_id)
    return {"queued": len(queued), "active": len(active)}
