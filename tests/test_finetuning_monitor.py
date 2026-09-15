from __future__ import annotations

import uuid
from datetime import UTC
from unittest.mock import MagicMock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import FinetuningJob, Project, ProjectMembership, User
from overbae.services.finetuning_runner import (
    PollSnapshot,
    parse_together_metrics,
    progress_from_snapshot,
)

pytestmark = pytest.mark.django_db


def _auth_client(user) -> APIClient:
    c = APIClient()
    token = RefreshToken.for_user(user)
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return c


def _setup():
    u = User.objects.create_user(
        email=f"ft-mon-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )
    p = Project.objects.create(name="p")
    ProjectMembership.objects.create(user=u, project=p)
    ds = frozen_dataset(p, TRAIN_ROWS, name="ds")
    return u, p, ds


def test_parse_together_metrics_splits_series():
    raw = [
        {
            "train/global_step": 1,
            "train/loss": 2.43,
            "train/grad_norm": 1.21,
            "train/learning_rate": 1e-5,
        },
        {
            "train/global_step": 2,
            "train/loss": 2.11,
            "train/grad_norm": 0.94,
            "train/learning_rate": 9e-6,
        },
        {"train/global_step": 2, "eval/loss": 2.05},
    ]
    loss, lr, grad = parse_together_metrics(raw)
    assert len(loss) == 2
    assert loss[1]["train_loss"] == 2.11
    assert loss[1]["eval_loss"] == 2.05
    assert [p["value"] for p in lr] == [1e-5, 9e-6]
    assert [p["value"] for p in grad] == [1.21, 0.94]


def test_progress_from_snapshot_computes_percent_and_eta():
    from datetime import datetime, timedelta

    started = datetime.now(UTC) - timedelta(seconds=100)
    snap = PollSnapshot(
        state="running",
        step=50,
        total_steps=100,
        train_loss=1.2,
        estimated_finish=int(started.timestamp()) + 200,
        loss_series=[{"step": 50, "train_loss": 1.2}],
        checkpoints=[{"step": 50, "path": "x"}],
    )
    progress = progress_from_snapshot(snap, started_at=started)
    assert progress["percent"] == 50.0
    assert progress["trained_steps"] == 50
    assert progress["total_steps"] == 100
    assert progress["eta_seconds"] is not None
    assert progress["eta_seconds"] > 0
    assert progress["metrics"]["loss"][0]["train_loss"] == 1.2
    assert len(progress["checkpoints"]) == 1


def test_progress_blob_thins_huge_series():
    """A 59k-step run must not persist 59k points per save."""
    from overbae.services.finetuning_runner import MAX_PERSISTED_SERIES_POINTS, thin_series

    points = [{"step": s, "train_loss": 1.0} for s in range(1, 59_113)]
    snap = PollSnapshot(state="running", step=59_112, total_steps=59_112, loss_series=points)
    progress = progress_from_snapshot(snap)
    loss = progress["metrics"]["loss"]
    assert len(loss) <= MAX_PERSISTED_SERIES_POINTS + 1
    assert loss[-1]["step"] == 59_112  # latest point survives thinning
    assert loss[0]["step"] == 1

    short = [{"step": s, "value": 0.1} for s in range(1, 100)]
    assert thin_series(short) is short


def test_record_deploy_stage_appends_and_caps_activity():
    from overbae.services.finetuning_runner import MAX_ACTIVITY_LINES
    from overbae.tasks.model_deployment import record_deploy_stage

    u, p, ds = _setup()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.DEPLOYING,
        progress={"phase": "finalizing", "activity": [{"ts": 1, "message": "training done"}]},
    )

    record_deploy_stage(job.pk, "Downloading checkpoint from Baseten…")
    job.refresh_from_db()
    # scrub_backend_names redacts vendor names at the write boundary.
    assert [a["message"] for a in job.progress["activity"]] == [
        "training done",
        "Downloading checkpoint from the provider…",
    ]
    assert job.progress["phase"] == "finalizing"  # rest of the blob untouched

    for i in range(MAX_ACTIVITY_LINES + 5):
        record_deploy_stage(job.pk, f"stage {i}")
    job.refresh_from_db()
    activity = job.progress["activity"]
    assert len(activity) == MAX_ACTIVITY_LINES
    assert activity[-1]["message"] == f"stage {MAX_ACTIVITY_LINES + 4}"


def test_cancel_calls_remote_runner_and_sets_cancelled():
    u, p, ds = _setup()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        remote_job_id="proj-1:job-remote-1",
        celery_task_id="celery-abc",
    )

    mock_runner = MagicMock()
    with (
        patch("overbae.services.finetuning_runner.get_runner", return_value=mock_runner),
        patch("overbae.celery.app") as celery_app,
    ):
        r = _auth_client(u).post(reverse("finetuningjob-cancel", kwargs={"id": job.id}))

    assert r.status_code == 200
    mock_runner.cancel.assert_called_once_with("proj-1:job-remote-1")
    celery_app.control.revoke.assert_called_once_with("celery-abc", terminate=True)
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.CANCELLED


def test_cancel_still_succeeds_when_remote_cancel_fails():
    u, p, ds = _setup()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        remote_job_id="proj-1:job-xyz",
    )
    mock_runner = MagicMock()
    mock_runner.cancel.side_effect = RuntimeError("provider down")
    with patch("overbae.services.finetuning_runner.get_runner", return_value=mock_runner):
        r = _auth_client(u).post(reverse("finetuningjob-cancel", kwargs={"id": job.id}))
    assert r.status_code == 200
    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.CANCELLED
    assert "remote cancel warning" in job.error_message


def test_loss_curves_reads_progress_metrics():
    u, p, ds = _setup()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.SUCCEEDED,
        progress={
            "trained_steps": 2,
            "total_steps": 2,
            "percent": 100.0,
            "metrics": {
                "loss": [
                    {"step": 1, "train_loss": 2.0, "eval_loss": 1.5},
                    {"step": 2, "train_loss": 1.5, "eval_loss": 0.9},
                ],
                "learning_rate": [],
                "grad_norm": [],
            },
            "checkpoints": [
                {
                    "step": 2,
                    "path": "ft:ckpt",
                    "type": "checkpoint",
                    "has_eval": True,
                    "valid_loss": 0.9,
                    "upload_status": "uploaded",
                }
            ],
        },
    )
    r = _auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
    assert r.status_code == 200
    assert r.data["steps"] == [1, 2]
    assert r.data["train_loss"] == [2.0, 1.5]
    assert r.data["eval_loss"] == [1.5, 0.9]
    assert r.data["epochs"] == [1, 2]
    assert r.data["valid_loss"] == [1.5, 0.9]
    assert len(r.data["checkpoints"]) == 1
    assert r.data["progress"]["percent"] == 100.0


def test_loss_curves_falls_back_to_epoch_losses():
    u, p, ds = _setup()
    job = FinetuningJob.objects.create(
        project=p,
        dataset=ds,
        base_model="m",
        status=FinetuningJob.Status.SUCCEEDED,
        result={
            "epoch_losses": [
                {"epoch": 1, "train_loss": 1.2, "valid_loss": 0.8},
            ],
            "model": "ft-x",
        },
    )
    r = _auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
    assert r.status_code == 200
    assert r.data["steps"] == [1]
    assert r.data["train_loss"] == [1.2]
    assert r.data["eval_loss"] == [0.8]
