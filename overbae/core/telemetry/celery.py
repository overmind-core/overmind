import time

from celery import signals

from overbae.core.telemetry.analytics import Event, capture

_started: dict[str, float] = {}


@signals.task_prerun.connect
def _task_started(task_id, **_):
    _started[task_id] = time.monotonic()


@signals.task_postrun.connect
def _task_finished(task_id, task, kwargs, retval, state, **_):
    # On FAILURE and RETRY Celery passes the exception as retval.
    started = _started.pop(task_id, None)
    capture(
        Event.TASK_FINISHED,
        kwargs.get("project_id"),
        task=task.name,
        queue=(task.request.delivery_info or {}).get("routing_key"),
        state=state,
        duration_ms=round((time.monotonic() - started) * 1000) if started else None,
        retries=task.request.retries,
        worker=task.request.hostname,
        exception_type=type(retval).__name__ if isinstance(retval, BaseException) else None,
    )
