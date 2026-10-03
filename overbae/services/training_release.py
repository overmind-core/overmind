import os
from pathlib import Path

import modal

from modal_shared.training_release import identity


def current():
    environment = os.environ.get("MODAL_ENVIRONMENT", "").strip()
    if not environment:
        raise ValueError("Set MODAL_ENVIRONMENT explicitly before preparing or launching training.")
    return {**identity(Path(__file__).resolve().parents[2]), "environment": environment}


def for_job(job):
    release = (job.requested_configuration or {}).get("runtime")
    if not release:
        raise ValueError(
            "This job has no pinned worker release. Reconcile its runtime before dispatch."
        )
    return release


def verify(release):
    observed = modal.Function.from_name(
        release["app"], "release_identity", environment_name=release["environment"]
    ).remote()
    if any(
        observed.get(key) != release[key]
        for key in ("app", "release", "processor", "training", "data_format")
    ):
        raise ValueError("The deployed training release does not match the pinned runtime.")
    return observed
