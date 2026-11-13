"""SageMaker container entrypoint for SFT training and dataset preparation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from modal_shared.chat_checkpoint import seal_checkpoint
from modal_shared.compute_usage import ComputeMeter
from modal_shared.decision_artifact import seal_artifact
from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.preparation import run_preparation_process
from modal_shared.serving.artifacts import atomic_json
from modal_shared.training_failure import failure_receipt
from modal_shared.training_telemetry import record_heartbeat, record_stage
from overbae.sagemaker.s3_store import (
    get_json,
    put_json,
    sync_directory_to_s3,
    sync_s3_to_directory,
    upload_file,
)

ASSETS_DIR = Path(os.environ.get("OVERMIND_SFT_ASSETS_DIR", "/root/sft_assets"))
DATA_MOUNT = Path(os.environ.get("OVERMIND_DATA_DIR", "/data"))
WEIGHTS_MOUNT = Path(os.environ.get("OVERMIND_WEIGHTS_DIR", "/weights"))


def _sync_run_telemetry(run_dir: Path, bucket: str, run_id: str) -> None:
    if not bucket:
        return
    for name in (
        "meta.json",
        "progress.json",
        "metrics.jsonl",
        "telemetry.json",
        "compute-usage.json",
        "monitoring.json",
        "decision-checkpoints.json",
    ):
        p = run_dir / name
        if p.is_file():
            upload_file(bucket, f"runs/{run_id}/{name}", p)


def run_prepare() -> int:
    prep_id = os.environ["OVERMIND_PREPARATION_ID"]
    sft_bucket = os.environ["AWS_SFT_BUCKET_NAME"]
    dest = DATA_MOUNT / "preparations" / prep_id
    dest.mkdir(parents=True, exist_ok=True)
    sync_s3_to_directory(sft_bucket, f"preparations/{prep_id}", dest)
    raw_req = os.environ.get("OVERMIND_PREPARATION_REQUEST")
    request = json.loads(raw_req) if raw_req else json.loads((dest / "request.json").read_text())

    meter = ComputeMeter(cpu=8, memory_gib=8)

    def _commit():
        if (dest / "progress.json").is_file():
            upload_file(sft_bucket, f"preparations/{prep_id}/progress.json", dest / "progress.json")

    with tempfile.TemporaryDirectory(prefix="sm-prepare-") as workspace:
        req_path = Path(workspace) / "request.json"
        req_path.write_text(json.dumps(request))
        report = run_preparation_process(ASSETS_DIR, req_path, dest, commit=_commit)
        report["compute_usage"] = meter.snapshot()
        (dest / "report.json").write_text(json.dumps(report))
    sync_directory_to_s3(dest, sft_bucket, f"preparations/{prep_id}")
    return 0 if report.get("ready") or not report.get("error") else 1


def run_train() -> int:
    run_id = os.environ["OVERMIND_RUN_ID"]
    sft_bucket = os.environ["AWS_SFT_BUCKET_NAME"]
    weights_bucket = os.environ.get("AWS_WEIGHTS_BUCKET_NAME", "")
    gpu_type = os.environ.get("OVERMIND_GPU_TYPE", "H100")
    gpu_count = int(os.environ.get("OVERMIND_GPU_COUNT", "1"))
    call_id = os.environ.get("OVERMIND_TRAINING_JOB_NAME", run_id)

    meter = ComputeMeter(gpu_type=gpu_type, gpu_count=gpu_count)
    run_dir = DATA_MOUNT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sync_s3_to_directory(sft_bucket, f"runs/{run_id}", run_dir)

    env_from_s3 = get_json(sft_bucket, f"runs/{run_id}/env.json")
    if isinstance(env_from_s3, dict):
        for k, v in env_from_s3.items():
            os.environ.setdefault(str(k), str(v))
    model_id = os.environ.get("MODEL_ID", "")
    if weights_bucket and model_id:
        slug = model_id.replace("/", "--")
        base_local = WEIGHTS_MOUNT / ".base_models" / slug
        sync_s3_to_directory(weights_bucket, f".base_models/{slug}", base_local)

    final_dir = run_dir / "final"
    candidate_dir = run_dir / "candidate"
    if candidate_dir.exists():
        shutil.rmtree(candidate_dir)
    native = os.environ.get("TRAINING_OBJECTIVE") in DECISION_OBJECTIVES

    record_heartbeat(run_dir, new_attempt=True)
    record_stage(run_dir, "staging_training_files")
    meta = {
        "run_id": run_id,
        "status": "starting",
        "started_at": time.time(),
        "call_id": call_id,
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    _sync_run_telemetry(run_dir, sft_bucket, run_id)

    work_dir = Path(f"/tmp/train_{run_id}")
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "data.jsonl", work_dir / "data.jsonl")
    if (run_dir / "val.jsonl").exists():
        shutil.copy2(run_dir / "val.jsonl", work_dir / "val.jsonl")
    if (run_dir / "preparation.json").exists():
        shutil.copy2(run_dir / "preparation.json", work_dir / "preparation.json")
    if (run_dir / "tokenizer").is_dir():
        shutil.copytree(run_dir / "tokenizer", work_dir / "tokenizer", dirs_exist_ok=True)

    stop_sync = threading.Event()

    def _background_sync():
        while not stop_sync.wait(5.0):
            _sync_run_telemetry(run_dir, sft_bucket, run_id)

    syncer = threading.Thread(target=_background_sync, daemon=True)
    syncer.start()

    child_env = os.environ.copy()
    child_env["BT_CHECKPOINT_DIR"] = str(candidate_dir)
    child_env["BT_RUN_DIR"] = str(run_dir)
    local_log = work_dir / "train_stdout.log"
    meta["status"] = "running"
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    _sync_run_telemetry(run_dir, sft_bucket, run_id)

    try:
        for verify in [False, True] if native else [False]:
            phase_env = {**child_env, "DECISION_VERIFY_CHECKPOINT": "1" if verify else "0"}
            with (
                open(local_log, "ab") as log_f,
                subprocess.Popen(  # noqa: S603
                    [sys.executable, str(ASSETS_DIR / "train.py")],
                    cwd=work_dir,
                    env=phase_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                ) as proc,
            ):
                assert proc.stdout is not None
                for line in proc.stdout:
                    sys.stdout.buffer.write(line)
                    sys.stdout.buffer.flush()
                    log_f.write(line)
                    log_f.flush()
                proc.wait()
            if local_log.exists():
                shutil.copy2(local_log, run_dir / "train_stdout.log")
            if proc.returncode != 0:
                raise RuntimeError(f"train.py exited with code {proc.returncode}")
        if native:
            report = json.loads((run_dir / "decision-reload-verification.json").read_text())
            seal_artifact(candidate_dir, report)
        else:
            seal_checkpoint(candidate_dir)
        candidate_dir.rename(final_dir)
        sync_directory_to_s3(final_dir, sft_bucket, f"runs/{run_id}/final")
        meta.update(status="succeeded", finished_at=time.time())
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        return 0
    except Exception:
        if local_log.exists():
            shutil.copy2(local_log, run_dir / "train_stdout.log")
        failure = failure_receipt(run_dir / "train_stdout.log")
        meta.update(status="failed", error=failure["message"], failure=failure)
        (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
        put_json(sft_bucket, f"runs/{run_id}/meta.json", meta)
        raise
    finally:
        stop_sync.set()
        syncer.join(timeout=2.0)
        usage_path = run_dir / "compute-usage.json"
        atomic_json(usage_path, [meter.snapshot()])
        _sync_run_telemetry(run_dir, sft_bucket, run_id)


def main() -> int:
    mode = os.environ.get("OVERMIND_SAGEMAKER_MODE", "train")
    if mode == "prepare":
        return run_prepare()
    return run_train()


if __name__ == "__main__":
    sys.exit(main())
