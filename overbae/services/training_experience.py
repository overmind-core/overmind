import atexit
import logging
import math
import os
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from celery.signals import worker_process_shutdown, worker_shutdown
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from posthog import Posthog

logger = logging.getLogger(__name__)
_lock = Lock()
_client = None
_connection = None
FINISHED = {"completed", "failed", "interrupted", "skipped"}


def flush(**_):
    with _lock:
        if _client is not None and _connection[0] == os.getpid():
            _client.flush(timeout_seconds=2)


atexit.register(flush)
worker_process_shutdown.connect(flush, weak=False)
worker_shutdown.connect(flush, weak=False)


def _capture(event, event_id, project_id, properties):
    global _client, _connection
    if not settings.POSTHOG_PROJECT_TOKEN:
        return
    connection = (os.getpid(), settings.POSTHOG_PROJECT_TOKEN, settings.POSTHOG_HOST)
    try:
        with _lock:
            if _connection != connection:
                if _client is not None and _connection[0] == os.getpid():
                    _client.shutdown()
                _client = Posthog(
                    connection[1],
                    host=connection[2],
                    timeout=1,
                    max_retries=0,
                    flush_interval=1,
                    max_queue_size=1000,
                    enable_local_evaluation=False,
                )
                _connection = connection
            _client.capture(
                event,
                uuid=str(event_id),
                distinct_id=f"project:{project_id}",
                properties={**properties, "$process_person_profile": False},
            )
    except Exception:
        logger.warning("Training product analytics unavailable")


def number(value):
    return value if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _emit(job, event, identity, properties):
    if not settings.POSTHOG_PROJECT_TOKEN:
        return
    started_at, project_id = job.started_at, job.project_id
    props = {
        "schema_version": 1,
        "job_id": str(job.pk),
        "project_id": str(job.project_id),
        **properties,
    }
    event_id = uuid5(NAMESPACE_URL, f"overmind:training:{job.pk}:{event}:{identity}")

    def deliver():
        _capture(
            event,
            event_id,
            project_id,
            {
                **props,
                "seconds_from_job_start": number((timezone.now() - started_at).total_seconds())
                if started_at
                else None,
            },
        )

    transaction.on_commit(deliver, robust=True)


def check_received(job, check, previous_state, previous_delivery, *, timing=None):
    now = timezone.now().isoformat()
    delivery = dict(previous_delivery)
    generation = check.metrics.get("generation") or {}
    props = {
        "check_id": str(check.pk),
        "step": check.step,
        "attempt": check.attempt,
        "stream": check.stream if check.stream in {"development", "final_development"} else "other",
        "state": check.state,
        "loss_rows_expected": number(check.coverage.get("expected")),
        "loss_rows_scored": number(check.coverage.get("scored")),
        "generation_rows_expected": number(generation.get("expected")),
        "generation_rows_scored": number(generation.get("scored")),
        "duration_seconds": number(check.facts.get("duration_seconds")),
        "optimizer_seconds": number((timing or {}).get("optimizer_seconds")),
        "monitoring_seconds": number((timing or {}).get("monitoring_seconds")),
    }
    usable = (
        number(check.metrics.get("eval_loss")) is not None
        and props["loss_rows_expected"] is not None
        and props["loss_rows_expected"] > 0
        and props["loss_rows_scored"] == props["loss_rows_expected"]
    )
    if usable and not delivery.get("first_result_at"):
        delivery["first_result_at"] = now
        _emit(job, "training_result_available", check.pk, props)
    if check.state in FINISHED and previous_state != check.state:
        delivery["finished_at"] = now
        _emit(job, "training_check_finished", f"{check.pk}:{check.state}", props)
    check.facts = {**check.facts, "delivery": delivery}


def checkpoint_received(job, checkpoint, record, *, created):
    verified = record.get("verification", {}).get("reload_verified") is True
    previous = checkpoint.verification.get("reload_verified") is True
    resume = record.get("verification", {}).get("resume_supported")
    if (
        record["state"] == "available"
        and verified
        and (created or checkpoint.state != "available" or not previous)
    ):
        _emit(
            job,
            "training_checkpoint_available",
            checkpoint.pk,
            {
                "checkpoint_id": str(checkpoint.pk),
                "step": checkpoint.step,
                "attempt": checkpoint.attempt,
                "resume_supported": resume if type(resume) is bool else None,
            },
        )
