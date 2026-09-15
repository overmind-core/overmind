"""Reclaim scratch on Modal Volumes after a deploy has landed.

overmind-sft: a training run leaves dataset, logs and checkpoint behind, and scripts leave run
dirs no job ever owned. Serving never mounts this Volume.

overmind-weights: ``.staging/`` is the unzip handoff for merge/quantize. After READY it is
dead — the serving copy is ``.adapters/`` or ``/weights/{id}/``, and S3 covers rebuilds.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

_SFT_APP = "overmind-sft"
_REGISTER_APP = "overmind-register"

_TRIM_AFTER = timedelta(days=3)
_PURGE_AFTER = timedelta(days=7)

# A run directory with no job row is either a script's scratch (calibration, smoke, benchmark
# harnesses all write here) or a run whose row is gone. Neither is reachable: the deploy path
# addresses a run through its job. The only thing this could race is a run whose directory exists
# before its row commits, so the window is set far beyond any training run's lifetime.
_ORPHAN_PURGE_AFTER = timedelta(days=14)

_DEAD = ("failed", "cancelled")


def _final_is_redundant(job) -> bool:
    """Whether the deploy already extracted everything ``final/`` holds.

    Two conditions, and both have to be true. A READY deployment means the serving artifact
    exists on the weights Volume — a merged FP8 checkpoint, or the adapter under ``.adapters/``,
    which is a byte-identical copy. A confirmed S3 archive covers the rebuild case, when the
    deployment is later deleted and its weights go with it.
    """
    from overbae.models import DeployedModel
    from overbae.services.finetuning_checkpoints import checkpoint_archive_exists

    deployed = DeployedModel.objects.filter(finetuning_job=job).only("status").first()
    if deployed is None or deployed.status != DeployedModel.Status.READY:
        return False
    return checkpoint_archive_exists(job)


def plan_run_retention(runs: Mapping[str, float], *, now=None) -> dict[str, list[str]]:
    """Sort run directories that exist on the Volume into purge / trim / drop_final / leave.

    ``runs`` maps run id to the mtime of its newest file, as reported by ``list_run_ids``. Driven
    by what the Volume reports rather than by the job table, so a run pruned once is never
    reconsidered — the job row outlives its scratch.
    """
    from overbae.models import FinetuningJob

    now = now or timezone.now()
    jobs = (
        FinetuningJob.objects.filter(provider=FinetuningJob.Provider.MODAL)
        .exclude(remote_job_id="")
        .only("id", "status", "completed_at", "updated_at", "remote_job_id", "triggered_by_id")
    )
    by_run = {job.remote_job_id.split(":", 1)[0]: job for job in jobs}

    purge: list[str] = []
    trim: list[str] = []
    drop_final: list[str] = []
    orphans: list[str] = []

    for run_id, mtime in runs.items():
        job = by_run.get(run_id)
        if job is None:
            if now - datetime.fromtimestamp(mtime, tz=UTC) > _ORPHAN_PURGE_AFTER:
                purge.append(run_id)
            else:
                orphans.append(run_id)
            continue

        settled = job.completed_at or job.updated_at
        if settled is None:
            continue
        age = now - settled

        if job.status in _DEAD and age > _PURGE_AFTER:
            # Never archived to S3 and not deployable, so the whole directory is dead.
            purge.append(run_id)
        elif job.status == FinetuningJob.Status.SUCCEEDED and age > _TRIM_AFTER:
            trim.append(run_id)
            if _final_is_redundant(job):
                drop_final.append(run_id)

    return {"purge": purge, "trim": trim, "drop_final": drop_final, "orphans": orphans}


@shared_task(name="overbae.tasks.cleanup_modal.prune_modal_sft_volume")
def prune_modal_sft_volume() -> dict:
    import modal

    env = os.environ.get("MODAL_ENVIRONMENT") or None

    def _fn(name: str):
        return modal.Function.from_name(_SFT_APP, name, environment_name=env)

    try:
        runs = _fn("list_run_ids").remote()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prune_modal_sft_volume: could not list runs: %s", exc)
        result: dict = {"skipped": "unreachable"}
        runs = None
    else:
        plan = plan_run_retention(runs)
        result = {"candidates": len(runs), "orphans": len(plan["orphans"])}

        if plan["purge"] or plan["trim"]:
            try:
                result["runs"] = _fn("prune_runs").remote(
                    purge=plan["purge"], trim=plan["trim"], drop_final=plan["drop_final"]
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("prune_modal_sft_volume: prune_runs failed: %s", exc)

    try:
        result["staging"] = modal.Function.from_name(
            _REGISTER_APP, "prune_spent_staging", environment_name=env
        ).remote()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prune_modal_sft_volume: prune_spent_staging failed: %s", exc)

    logger.info("prune_modal_sft_volume: %s", result)
    return result
