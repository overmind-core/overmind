from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.utils import timezone

from overbae.models import DeployedModel, FinetuningJob, Project
from overbae.tasks.cleanup_modal import plan_run_retention

pytestmark = pytest.mark.django_db


def _job(
    *,
    status: str,
    age_days: int,
    provider: str = "modal",
    deployment: str | None = None,
) -> str:
    """Returns the run_id, which is the stable half of remote_job_id."""
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        provider=provider,
        base_model="Qwen/Qwen3-1.7B",
        status=status,
        remote_job_id=f"{run_id}:fc-{uuid.uuid4().hex[:8]}",
    )
    FinetuningJob.objects.filter(pk=job.pk).update(
        completed_at=timezone.now() - timedelta(days=age_days)
    )
    if deployment is not None:
        DeployedModel.objects.create(
            project=project,
            finetuning_job=job,
            model_id=f"ft-{uuid.uuid4().hex[:8]}",
            base_model_id="Qwen/Qwen3-1.7B",
            status=deployment,
        )
    return run_id


def _plan(run_ids, *, archived: bool = True, age_days: int = 0):
    """`plan_run_retention` takes run id -> mtime; job-backed cases age off the job row, so a
    single fresh timestamp is enough for everything except the orphan rule."""
    mtime = (timezone.now() - timedelta(days=age_days)).timestamp()
    runs = dict.fromkeys(run_ids, mtime)
    with patch(
        "overbae.services.finetuning_checkpoints.checkpoint_archive_exists",
        return_value=archived,
    ):
        return plan_run_retention(runs)


def test_failed_run_is_purged_whole():
    """A failed run is never archived to S3 and can never deploy, so nothing in it is reachable."""
    run_id = _job(status=FinetuningJob.Status.FAILED, age_days=30)

    plan = _plan([run_id])

    assert plan["purge"] == [run_id]
    assert plan["trim"] == []


def test_cancelled_run_is_purged_whole():
    run_id = _job(status=FinetuningJob.Status.CANCELLED, age_days=30)

    assert _plan([run_id])["purge"] == [run_id]


def test_succeeded_run_is_trimmed_never_purged():
    """The run's log stays even when everything else goes, so the directory is never dropped
    whole for a job that succeeded."""
    run_id = _job(status=FinetuningJob.Status.SUCCEEDED, age_days=30)

    plan = _plan([run_id])

    assert plan["trim"] == [run_id]
    assert plan["purge"] == []


def test_deployed_and_archived_run_drops_its_checkpoint():
    """READY means the serving copy is on the weights volume and the archive covers a rebuild,
    so nothing reads `final/` again."""
    run_id = _job(
        status=FinetuningJob.Status.SUCCEEDED,
        age_days=30,
        deployment=DeployedModel.Status.READY,
    )

    assert _plan([run_id])["drop_final"] == [run_id]


def test_undeployed_run_keeps_its_checkpoint():
    """Trained but never deployed: `final/` is the only thing that lets it deploy volume→volume."""
    run_id = _job(status=FinetuningJob.Status.SUCCEEDED, age_days=30)

    assert _plan([run_id])["drop_final"] == []


def test_run_still_deploying_keeps_its_checkpoint():
    run_id = _job(
        status=FinetuningJob.Status.SUCCEEDED,
        age_days=30,
        deployment=DeployedModel.Status.DEPLOYING,
    )

    assert _plan([run_id])["drop_final"] == []


def test_unconfirmed_archive_keeps_the_checkpoint():
    """The archive spawn is fire-and-forget, so a missing zip is a live possibility — and
    deleting `final/` on top of it would leave the weights nowhere."""
    run_id = _job(
        status=FinetuningJob.Status.SUCCEEDED,
        age_days=30,
        deployment=DeployedModel.Status.READY,
    )

    assert _plan([run_id], archived=False)["drop_final"] == []


def test_checkpoint_is_kept_during_the_grace_period():
    """Same age gate as the dataset trim: a redeploy could still be reading the run."""
    run_id = _job(
        status=FinetuningJob.Status.SUCCEEDED,
        age_days=1,
        deployment=DeployedModel.Status.READY,
    )

    assert _plan([run_id])["drop_final"] == []


def test_recent_runs_are_left_alone():
    fresh_fail = _job(status=FinetuningJob.Status.FAILED, age_days=1)
    fresh_ok = _job(status=FinetuningJob.Status.SUCCEEDED, age_days=0)

    plan = _plan([fresh_fail, fresh_ok])

    assert plan["purge"] == []
    assert plan["trim"] == []


def test_running_job_is_never_touched():
    run_id = _job(status=FinetuningJob.Status.RUNNING, age_days=30)

    plan = _plan([run_id])

    assert plan["purge"] == []
    assert plan["trim"] == []


def test_unknown_run_dir_is_reported_not_deleted():
    """A run dir can exist before its job row commits, so an unmatched id is never assumed dead."""
    plan = _plan(["run-orphan"])

    assert plan["orphans"] == ["run-orphan"]
    assert plan["purge"] == []
    assert plan["trim"] == []


def test_non_modal_jobs_do_not_match_a_run_dir():
    """Baseten run ids share the volume's namespace only by coincidence."""
    run_id = _job(status=FinetuningJob.Status.FAILED, age_days=30, provider="baseten")

    plan = _plan([run_id])

    assert plan["purge"] == []
    assert plan["orphans"] == [run_id]


def test_stale_orphan_is_purged():
    """Scripts write run dirs here that no job ever owned — calibration, smoke and benchmark
    harnesses. Nothing addresses them, so past the window they are the bulk of the volume."""
    plan = _plan(["cal-attrib-baseline-65536-50c34e"], age_days=30)

    assert plan["purge"] == ["cal-attrib-baseline-65536-50c34e"]
    assert plan["orphans"] == []


def test_orphan_is_aged_off_the_volume_not_the_job_table():
    """The whole point: an orphan has no row to age against, so the timestamp has to be the
    file's own."""
    fresh, stale = "probe-fresh", "probe-stale"
    now = timezone.now()
    runs = {
        fresh: (now - timedelta(days=1)).timestamp(),
        stale: (now - timedelta(days=90)).timestamp(),
    }

    plan = plan_run_retention(runs)

    assert plan["purge"] == [stale]
    assert plan["orphans"] == [fresh]
