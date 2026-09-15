from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overbae.services.finetuning_runner import (
    MAX_ACTIVITY_LINES,
    BasetenRunner,
    filter_activity_logs,
    get_runner,
    progress_from_snapshot,
)

_REMOTE_ID = "proj-1:job-9"


def _bt_logs():
    """Log entries exactly as baseten_callback.py prints them."""
    lines = [
        "BT_PROGRESS "
        + json.dumps(
            {
                "step": 5,
                "total_steps": 30,
                "epoch": 0.5,
                "epochs": 3,
                "loss": 2.51,
                "lr": 1e-5,
                "elapsed_s": 45.2,
                "eta_s": 225.8,
                "grad_norm": 1.9,
                "token_accuracy": 0.61,
                "num_tokens": 4096,
            }
        ),
        "BT_EVAL "
        + json.dumps(
            {
                "step": 10,
                "epoch": 1.0,
                "eval_loss": 3.12,
                "eval_runtime_s": 2.3,
                "eval_token_accuracy": 0.58,
            }
        ),
        "BT_CHECKPOINT " + json.dumps({"step": 10, "epoch": 1.0, "path": "checkpoint-10"}),
        "BT_PROGRESS "
        + json.dumps(
            {
                "step": 10,
                "total_steps": 30,
                "epoch": 1.0,
                "epochs": 3,
                "loss": 2.10,
                "lr": 9e-6,
                "elapsed_s": 90.0,
                "eta_s": 180.0,
                "grad_norm": 1.4,
                "token_accuracy": 0.65,
                "num_tokens": 8192,
            }
        ),
    ]
    return [{"message": m, "timestamp": 1_700_000_000_000 + i} for i, m in enumerate(lines)]


def _poll(state="TRAINING_JOB_RUNNING", logs=None):
    runner = BasetenRunner()
    with (
        patch.object(BasetenRunner, "_get_job", return_value={"current_status": state}),
        patch.object(BasetenRunner, "_fetch_logs", return_value=logs or _bt_logs()),
    ):
        return runner.poll(_REMOTE_ID)


def test_filter_activity_logs_keeps_stage_lines_drops_noise():
    """Fixture lines are verbatim raw output from Baseten job wnr4pyq."""
    logs = [
        # ns timestamp, as the Baseten API returns it
        {"message": "Creating the training job.", "timestamp": "1784323432231601977"},
        {"message": "Image pulled successfully for g00r0", "timestamp": "2"},
        {"message": "Collecting trl>=1.4.0", "timestamp": "3"},
        {"message": "  Downloading trl-1.8.0-py3-none-any.whl.metadata (12 kB)", "timestamp": "4"},
        {
            "message": "Requirement already satisfied: jinja2 in /opt/conda/lib/python3.11/site-packages (from trl>=1.4.0) (3.1.6)",
            "timestamp": "5",
        },
        {
            "message": "   \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501 863.2/863.2 kB 45.1 MB/s eta 0:00:00",
            "timestamp": "6",
        },
        {"message": "Successfully installed accelerate-1.14.0 aiohttp-3.14.1", "timestamp": "7"},
        {
            "message": "Warning: You are sending unauthenticated requests to the HF Hub.",
            "timestamp": "8",
        },
        {
            "message": "[transformers] `torch_dtype` is deprecated! Use `dtype` instead!",
            "timestamp": "9",
        },
        {
            "message": "\rFetching 5 files:   0%|          | 0/5 [00:00<?, ?it/s]\rFetching 5 files: 100%|\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588| 5/5 [02:18<00:00, 27.69s/it] ",
            "timestamp": "10",
        },
        {
            "message": "\r  0%|          | 0/1 [00:00<?, ?it/s]\x1b[A\r                                              ",
            "timestamp": "11",
        },
        {
            "message": "\rMap:   0%|          | 0/22 [00:00<?, ? examples/s]\rMap: 100%|\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588| 22/22 [00:00<00:00, 1267.49 examples/s]",
            "timestamp": "12",
        },
        {"message": "Model=Qwen/Qwen3-8B  type=tool  lr=0.0001  seed=42", "timestamp": "13"},
        {"message": "Loaded: 22 train / 6 val  (type=tool)", "timestamp": "14"},
        {
            "message": 'BT_PROGRESS {"step": 1, "total_steps": 21, "loss": 0.1959, "lr": 0.0}',
            "timestamp": "15",
        },
        {
            "message": "{'loss': '0.1959', 'grad_norm': '0.3122', 'learning_rate': '0', 'mean_token_accuracy': '0.9617', 'epoch': '0.3636'}",
            "timestamp": "16",
        },
        {
            "message": "{'eval_loss': '0.09065', 'eval_runtime': '5.037', 'eval_samples_per_second': '1.191'}",
            "timestamp": "17",
        },
        {
            "message": 'BT_EVAL {"step": 21, "epoch": 7.0, "eval_loss": 0.09065335988998413, "eval_runtime_s": 5.04, "eval_token_accuracy": 0.9747}',
            "timestamp": "18",
        },
        {
            "message": 'BT_CHECKPOINT {"step": 21, "epoch": 7.0, "path": "checkpoint-21"}',
            "timestamp": "19",
        },
        {
            "message": "{'train_runtime': '272.8', 'train_samples_per_second': '0.565', 'train_steps_per_second': '0.077', 'train_loss': '0.1258', 'epoch': '7'}",
            "timestamp": "20",
        },
        {"message": "Adapter saved \u2192 /mnt/ckpts", "timestamp": "21"},
        {"message": "Job already completed successfully, terminating job...", "timestamp": "22"},
    ]
    out = filter_activity_logs(logs)
    messages = [line["message"] for line in out]
    assert messages == [
        "Creating the training job.",
        "Image pulled successfully for g00r0",
        "Model=Qwen/Qwen3-8B  type=tool  lr=0.0001  seed=42",
        "Loaded: 22 train / 6 val  (type=tool)",
        "Eval @ step 21 — loss 0.09065, token acc 97.5%",
        "Checkpoint saved: checkpoint-21 (step 21)",
        "Training finished — train loss 0.1258, 273s",
        "Adapter saved \u2192 /mnt/ckpts",
        "Job already completed successfully, terminating job...",
    ]
    assert out[0]["ts"] == 1784323432231
    assert all("\x1b" not in m and "\r" not in m and "\u2588" not in m for m in messages)

    many = [{"message": f"line {i}", "timestamp": i} for i in range(MAX_ACTIVITY_LINES + 10)]
    capped = filter_activity_logs(many)
    assert len(capped) == MAX_ACTIVITY_LINES
    assert capped[-1]["message"] == f"line {MAX_ACTIVITY_LINES + 9}"


def test_poll_exposes_activity_feed():
    """BT_EVAL / BT_CHECKPOINT surface as readable stage events; BT_PROGRESS stays chart-only."""
    logs = [
        {"message": "Training job status updated to TRAINING_JOB_RUNNING.", "timestamp": 1},
        *_bt_logs(),
        {"message": "Loading checkpoint shards: done", "timestamp": 2_000_000_000_000},
    ]
    snap = _poll(logs=logs)
    assert [a["message"] for a in snap.activity] == [
        "Training job status updated to TRAINING_JOB_RUNNING.",
        "Eval @ step 10 — loss 3.12, token acc 58.0%",
        "Checkpoint saved: checkpoint-10 (step 10)",
        "Loading checkpoint shards: done",
    ]
    progress = progress_from_snapshot(snap)
    assert progress["activity"] == snap.activity


def test_poll_maps_histories_into_monitor_series():
    snap = _poll()
    assert snap.state == "running"
    assert snap.step == snap.trained_steps == 10
    assert snap.total_steps == 30
    assert snap.loss_series == [
        {"step": 5, "train_loss": 2.51},
        {"step": 10, "train_loss": 2.1, "eval_loss": 3.12},
    ]
    assert snap.learning_rate_series == [
        {"step": 5, "value": 1e-5},
        {"step": 10, "value": 9e-6},
    ]
    assert snap.grad_norm_series == [
        {"step": 5, "value": 1.9},
        {"step": 10, "value": 1.4},
    ]
    assert snap.token_accuracy_series == [
        {"step": 5, "train": 0.61},
        {"step": 10, "train": 0.65, "eval": 0.58},
    ]
    assert snap.tokens_processed == 8192
    assert snap.estimated_finish is not None
    assert snap.output_model_name == ""


def test_poll_maps_checkpoint_rows_with_losses():
    snap = _poll()
    assert len(snap.checkpoints) == 1
    row = snap.checkpoints[0]
    assert row["step"] == 10
    assert row["path"] == "checkpoint-10"
    assert row["train_loss"] == 2.1
    assert row["valid_loss"] == 3.12
    assert row["valid_mean_token_accuracy"] == 0.58
    assert row["has_eval"] is True


def test_poll_success_sets_output_model_name():
    snap = _poll(state="TRAINING_JOB_COMPLETED")
    assert snap.state == "succeeded"
    assert snap.output_model_name == "baseten/job-9/final"


def test_progress_from_snapshot_feeds_monitor_charts():
    progress = progress_from_snapshot(_poll())
    assert progress["trained_steps"] == 10
    assert progress["total_steps"] == 30
    assert [p["step"] for p in progress["metrics"]["loss"]] == [5, 10]
    assert [p["value"] for p in progress["metrics"]["learning_rate"]] == [1e-5, 9e-6]
    assert [p["value"] for p in progress["metrics"]["grad_norm"]] == [1.9, 1.4]
    assert progress["metrics"]["token_accuracy"] == [
        {"step": 5, "train": 0.61},
        {"step": 10, "train": 0.65, "eval": 0.58},
    ]
    assert progress["latest_train_loss"] == 2.1
    assert progress["latest_eval_loss"] == 3.12


def test_progress_from_snapshot_keeps_pr367_live_metric_contract():
    """Flat keys + raw histories the FE reads (finetuning-progress.ts, LiveMetricsChart)."""
    progress = progress_from_snapshot(_poll())
    assert progress["train_loss"] == 2.1
    assert progress["eval_loss"] == 3.12
    assert progress["learning_rate"] == 9e-6
    assert progress["token_accuracy"] == 0.65
    assert progress["eval_token_accuracy"] == 0.58
    assert progress["current_epoch"] == 1.0
    assert progress["eta_s"] == 180.0
    assert [p["step"] for p in progress["metrics_history"]] == [5, 10]
    assert progress["metrics_history"][1]["grad_norm"] == 1.4
    assert [p["step"] for p in progress["eval_history"]] == [10]
    assert progress["eval_history"][0]["eval_token_accuracy"] == 0.58


def test_cancel_posts_stop_endpoint(settings):
    settings.BASETEN_API_KEY = "test-key"
    with patch("requests.post") as post:
        post.return_value.raise_for_status.return_value = None
        BasetenRunner().cancel(_REMOTE_ID)
    url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs["url"]
    assert url.endswith("/v1/training_projects/proj-1/jobs/job-9/stop")


def test_get_runner_registers_baseten(settings):
    settings.FINETUNING_BACKEND = "baseten"
    assert isinstance(get_runner(), BasetenRunner)


def _fake_plan():
    from overbae.services.finetuning_policy import BasetenTrainingPlan

    return BasetenTrainingPlan(
        context_length=4096,
        n_epochs=3,
        batch_size=8,
        per_device_batch=1,
        grad_accum=8,
        learning_rate=1e-4,
        warmup_ratio=0.05,
        weight_decay=0.01,
        lora_r=16,
        lora_alpha=32,
        lora_dropout=0.05,
        lora_target_modules="all-linear",
        packing=False,
    )


def test_config_source_validates_against_installed_sdk(settings):
    """TrainingProject is strict (extra='forbid'); the project value must land on ``name``,
    because push upserts projects by name."""
    pytest.importorskip("truss_train")
    settings.BASETEN_PROJECT = "overmind-dev"

    job = SimpleNamespace(id="j1", name="my ft job!", base_model="Qwen/Qwen3-8B")
    src = BasetenRunner()._build_config_source(
        job=job,
        gpu_type="H100",
        gpu_count=1,
        dataset_type="chat",
        plan=_fake_plan(),
        project_name="overmind-dev",
    )

    ns: dict = {}
    exec(src, ns)  # noqa: S102 — pydantic validation IS the assertion
    tp = ns["training_project"]
    assert tp.name == "overmind-dev"
    assert tp.job.name == "my-ft-job-"  # sanitised per-job label
    assert tp.job.runtime.environment_variables["MAX_LENGTH"] == "4096"
    assert tp.job.compute.accelerator.count == 1
    # Gated HF models need the workspace secret wired explicitly.
    from truss_train import SecretReference

    assert tp.job.runtime.environment_variables["HF_TOKEN"] == SecretReference(
        name="HF_ACCESS_TOKEN"
    )
    assert tp.job.runtime.environment_variables["HUGGING_FACE_HUB_TOKEN"] == SecretReference(
        name="HF_ACCESS_TOKEN"
    )


def test_ensure_trussrc_rewrites_from_settings(settings, tmp_path, monkeypatch):
    """HOME == TMPDIR in workers, so a swept /data/tmp must not break the next push."""
    remote_factory = pytest.importorskip("truss.remote.remote_factory")
    settings.BASETEN_API_KEY = "sk-test-123"
    trussrc = tmp_path / ".trussrc"
    monkeypatch.setattr(remote_factory, "USER_TRUSSRC_PATH", trussrc)

    BasetenRunner()._ensure_trussrc()

    content = trussrc.read_text()
    assert "[baseten]" in content
    assert "api_key = sk-test-123" in content
    assert "remote_provider = baseten" in content
    assert remote_factory.RemoteFactory.load_remote_config("baseten").configs["api_key"] == (
        "sk-test-123"
    )


def test_cleanup_tmp_never_sweeps_dotfiles(settings, tmp_path):
    """HOME == TMPDIR in the containers, so dotfiles (.trussrc, .aws) are config, not scratch."""
    import os

    from overbae.tasks.cleanup_tmp import cleanup_data_tmp

    settings.TMPDIR = str(tmp_path)
    old = ("stale.txt", ".trussrc")
    for name in old:
        p = tmp_path / name
        p.write_text("x")
        os.utime(p, (0, 0))  # ancient mtime — eligible for sweep

    cleanup_data_tmp()

    assert not (tmp_path / "stale.txt").exists()
    assert (tmp_path / ".trussrc").exists()


def test_config_source_has_no_id_kwarg():
    job = SimpleNamespace(id="j1", name="ft", base_model="Qwen/Qwen3-8B")
    src = BasetenRunner()._build_config_source(
        job=job,
        gpu_type="H100",
        gpu_count=1,
        dataset_type="chat",
        plan=_fake_plan(),
        project_name="overmind-dev",
    )
    assert 'name="overmind-dev"' in src
    assert "id=" not in src
