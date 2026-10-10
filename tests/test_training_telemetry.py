import json
from unittest.mock import MagicMock, patch

import pytest

from modal_shared.training_telemetry import read_telemetry, record_heartbeat, record_stage
from overbae.models import Dataset, FinetuningJob, Project
from overbae.services import training_submission
from overbae.services.finetuning_runner import ModalRunner, PollSnapshot, progress_from_snapshot
from overbae.tasks.finetuning import _persist_snapshot_progress


def test_stages_and_recovery_cross_provider_and_progress_boundaries(tmp_path):
    record_stage(tmp_path, "initial_validation", completed=12, total=30, unit="decisions")
    first = read_telemetry(tmp_path)
    assert first["stage"] == "initial_validation" and first["completed"] == 12
    record_stage(tmp_path, "initial_validation", completed=30, total=30, unit="decisions")
    record_stage(tmp_path, "training", completed=4, total=100, unit="steps", restored_step=4)
    record_stage(tmp_path, "training", completed=5, total=100, unit="steps", checkpoint_step=5)
    telemetry = read_telemetry(tmp_path)
    assert telemetry["restored_step"] == 4 and telemetry["checkpoint_step"] == 5
    assert telemetry["heartbeat_at"] >= first["heartbeat_at"]
    snap = {
        "meta": {"status": "running"},
        "telemetry": telemetry,
        "metrics": [
            {
                "event": "BT_EVAL",
                "step": 0,
                "eval_loss": 1.2,
                "argmax_target_agreement": 0.6,
                "brier": 0.4,
                "decisions": 30,
            },
            {"event": "BT_PROGRESS", "step": 5, "total_steps": 100, "loss": 0.7, "num_tokens": 50},
        ],
    }
    function = MagicMock()
    function.remote.return_value = snap
    call = MagicMock()
    call.get.side_effect = TimeoutError()
    with (
        patch("modal.Function.from_name", return_value=function),
        patch("modal.FunctionCall.from_id", return_value=call),
    ):
        poll = ModalRunner(release={"app": "fixture-release", "environment": "test"}).poll(
            "run:call"
        )
    progress = progress_from_snapshot(poll)
    assert progress["stage"] == "training"
    assert progress["diagnostics"]["restored_step"] == 4
    assert progress["eval_history"][0]["brier"] == 0.4
    assert progress["eval_history"][0]["decisions"] == 30
    assert progress["eval_history"][0]["argmax_target_agreement"] == 0.6
    assert "target_probabilities" not in json.dumps(progress)


def test_silent_stage_heartbeat_keeps_work_counter_and_attempt_clocks(tmp_path):
    record_stage(tmp_path, "loading_weights", completed=12, total=100, unit="bytes")
    first = record_heartbeat(tmp_path, new_attempt=True)
    record_heartbeat(tmp_path)
    second = read_telemetry(tmp_path)
    assert second["completed"] == 12 and second["stage"] == "loading_weights"
    assert second["heartbeat_at"] >= first["heartbeat_at"]
    resumed = record_heartbeat(tmp_path, new_attempt=True)
    assert resumed["attempt"] == 2
    assert resumed["overall_started_at"] == first["overall_started_at"]


def test_startup_clocks_separate_heartbeat_progress_and_restarted_attempt(tmp_path):
    with patch("modal_shared.training_telemetry.time.time", return_value=100):
        record_heartbeat(tmp_path, new_attempt=True)
        record_stage(tmp_path, "loading_model")
    with patch("modal_shared.training_telemetry.time.time", return_value=120):
        record_heartbeat(tmp_path)
        record_stage(tmp_path, "loading_model")
    detail = read_telemetry(tmp_path)
    assert detail["heartbeat_at"] == detail["source_at"] == 120
    assert detail["last_progress_at"] == detail["stage_started_at"] == 100
    assert detail["completed"] is None
    with patch("modal_shared.training_telemetry.time.time", return_value=130):
        record_stage(tmp_path, "building_training_dataset", completed=250, total=1000, unit="rows")
    with patch("modal_shared.training_telemetry.time.time", return_value=140):
        record_stage(tmp_path, "building_training_dataset", completed=500)
    detail = read_telemetry(tmp_path)
    assert detail["last_progress_at"] == detail["heartbeat_at"] == 140
    assert detail["stage_started_at"] == 130
    with patch("modal_shared.training_telemetry.time.time", return_value=150):
        record_heartbeat(tmp_path, new_attempt=True)
        record_stage(tmp_path, "building_training_dataset", completed=0, total=1000, unit="rows")
    detail = read_telemetry(tmp_path)
    assert detail["attempt"] == 2
    assert detail["stage_started_at"] == detail["last_progress_at"] == 150
    with patch("modal_shared.training_telemetry.time.time", return_value=160):
        record_stage(tmp_path, "initializing_trainer")
    detail = read_telemetry(tmp_path)
    assert detail["completed"] is detail["total"] is detail["unit"] is None


@pytest.mark.django_db
def test_modal_preparation_and_gpu_stages_remain_visible_after_polling():
    project = Project.objects.create(name="Stage visibility", slug="stage-visibility")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(project=project, dataset=dataset, base_model="fixture")
    training_submission.claim(job)
    training_submission.record_pre_dispatch_stage(
        job, "preparing_base_model", "Checking base model weights"
    )

    snap = PollSnapshot(
        state="running",
        phase="training",
        stage="loading_model",
        diagnostics={"stage": "loading_model"},
        raw={"run_id": "fixture"},
    )
    _persist_snapshot_progress(job, snap, tick_evals=False)
    _persist_snapshot_progress(job, snap, tick_evals=False)
    job.refresh_from_db()
    assert job.progress["stage"] == "loading_model"
    assert [line["message"] for line in job.progress["activity"]] == [
        "Checking base model weights",
        "Loading base model onto GPU",
    ]


@pytest.mark.django_db
def test_modal_poll_without_stage_keeps_pre_dispatch_step():
    project = Project.objects.create(name="Stage visibility", slug="stage-visibility-empty")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(project=project, dataset=dataset, base_model="fixture")
    training_submission.claim(job)
    training_submission.record_pre_dispatch_stage(
        job, "starting_training_worker", "Base model weights ready; starting training worker"
    )

    snap = PollSnapshot(state="queued", raw={"run_id": "fixture"})
    _persist_snapshot_progress(job, snap, tick_evals=False)
    job.refresh_from_db()
    assert job.progress["stage"] == "starting_training_worker"
    assert [line["message"] for line in job.progress["activity"]] == [
        "Base model weights ready; starting training worker"
    ]


@pytest.mark.django_db
def test_transfer_observations_keep_counts_freshness_and_ownership_separate():
    project = Project.objects.create(name="Transfer visibility", slug="transfer-visibility")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, base_model="fixture", status="preparing"
    )
    training_submission.claim(job)
    first = {
        "stage": "uploading_selections",
        "completed": 0,
        "total": 320,
        "unit": "bytes",
        "source_at": 100,
        "run_id": "fixture",
    }
    training_submission.record_pre_dispatch_stage(
        job, "transferring", "Uploading row selections", diagnostics=first
    )
    training_submission.record_pre_dispatch_stage(
        job, "transferring", "Uploading row selections", diagnostics={**first, "source_at": 110}
    )
    assert job.progress["diagnostics"]["last_progress_at"] == 100
    assert job.progress["diagnostics"]["stage_started_at"] == 100
    assert job.progress["diagnostics"]["source_at"] == 110
    assert len(job.progress["activity"]) == 1
    training_submission.record_pre_dispatch_stage(
        job,
        "transferring",
        "Uploading row selections",
        diagnostics={**first, "completed": 320, "source_at": 120},
    )
    assert job.progress["diagnostics"]["last_progress_at"] == 120
    training_submission.record_pre_dispatch_stage(
        job, "transferring", "Uploading row selections", diagnostics=first
    )
    assert job.progress["diagnostics"]["completed"] == 320
    training_submission.record_pre_dispatch_stage(job, "preparing_base_model", "Checking weights")
    assert "completed" not in job.progress["diagnostics"]
    FinetuningJob.objects.filter(pk=job.pk).update(status="cancelled")
    with pytest.raises(training_submission.SubmissionUnresolvedError):
        training_submission.record_pre_dispatch_stage(
            job, "transferring", "Late observation", diagnostics=first
        )


@pytest.mark.django_db
@pytest.mark.parametrize("fail_upload", [False, True])
def test_transfer_counts_only_acknowledged_files_and_retains_provider_progress(
    tmp_path, fail_upload
):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from overbae.services.training_transfer import observe, stage_data

    project = Project.objects.create(name="Transfer journey", slug="transfer-journey")
    dataset = Dataset.objects.create(project=project)
    job = FinetuningJob.objects.create(
        project=project, dataset=dataset, base_model="fixture", status="preparing"
    )
    training_submission.claim(job)
    source = tmp_path / "data.jsonl"
    source.write_text(
        json.dumps({"messages": [{"role": "assistant", "content": "example"}]}) + "\n"
    )
    snapshots = []

    class Volume:
        @contextmanager
        def batch_upload(self):
            yield self
            job.refresh_from_db()
            snapshots.append(dict(job.progress["diagnostics"]))
            if fail_upload:
                raise OSError("upload interrupted")

        def put_file(self, source, destination):
            assert source.stat().st_size == 32

        def read_file(self, path):
            assert path == "/runs/run-fixture/transfer-progress.json"
            yield json.dumps(
                {
                    "stage": "selecting_prepared_rows",
                    "completed": 1,
                    "total": 1,
                    "unit": "rows",
                    "source_at": 2000000000,
                }
            ).encode()

    remote = MagicMock()
    preparation = SimpleNamespace(id="prep", report={"artifact_sha256": "digest"})
    if fail_upload:
        with pytest.raises(OSError):
            stage_data(
                job, {"data": source}, "run-fixture", preparation, Volume(), remote, num_examples=1
            )
        assert snapshots[-1]["completed"] == 0
        assert snapshots[-1]["files_completed"] == 0
        remote.remote.assert_not_called()
        return
    stage_data(job, {"data": source}, "run-fixture", preparation, Volume(), remote, num_examples=1)
    assert snapshots[-1]["completed"] == 0
    remote.remote.assert_called_once()
    job.refresh_from_db()
    assert job.progress["diagnostics"]["files_completed"] == 1
    assert job.progress["diagnostics"]["acknowledged_bytes"] == 32
    observe(job, volume=Volume())
    job.refresh_from_db()
    assert job.progress["diagnostics"]["completed"] == 1
    assert job.progress["diagnostics"]["stage"] == "selecting_prepared_rows"
    training_submission.record_pre_dispatch_stage(
        job, "preparing_base_model", "Checking weights", diagnostics={"source_at": 2000000001}
    )
    observe(job, volume=Volume())
    job.refresh_from_db()
    assert job.progress["stage"] == "preparing_base_model"
