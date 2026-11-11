from __future__ import annotations

import json
import math
import shutil
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

from django.conf import settings
from django.utils import timezone

from modal_shared.preparation import preparation_failure, validate_preparation_report
from modal_shared.training_data import materialize_files
from modal_shared.training_monitoring_runtime import freeze_run_files
from overbae.core.aws import boto3_client
from overbae.models import TrainingPreparation
from overbae.sagemaker.s3_store import (
    get_json,
    put_json,
    sft_bucket,
    sync_directory_to_s3,
    sync_s3_to_directory,
    upload_file,
)
from overbae.services import operational_progress


def processing_job_name(prep_id: str) -> str:
    clean = str(prep_id).replace("-", "")[:24]
    return f"om-prep-{clean}-{int(time.time())}"[:63]


def training_image_uri(stack: str, runtime: dict[str, Any] | None = None) -> str:
    runtime = runtime or {}
    if runtime.get("image_uri"):
        return str(runtime["image_uri"])
    region = runtime.get("region") or getattr(settings, "AWS_REGION", "eu-west-1")
    prefix = getattr(settings, "SAGEMAKER_ECR_REPOSITORY_PREFIX", "overmind")
    tag = runtime.get("image_tag") or getattr(settings, "SAGEMAKER_IMAGE_TAG", "latest")
    return f"{prefix}-train-{stack}:{tag}@{region}"


def start_preparation(prep: TrainingPreparation, source_path: Path, digest: str) -> str:
    bucket = sft_bucket()
    remote_key = f"preparations/{prep.id}/rows.jsonl.gz"
    upload_file(bucket, remote_key, source_path)
    request_payload = {
        **prep.config,
        "rows_path": f"/data/{remote_key}",
        "rows_sha256": digest,
    }
    put_json(bucket, f"preparations/{prep.id}/request.json", request_payload)

    job_name = processing_job_name(str(prep.id))
    runtime = prep.config.get("runtime") or {}
    role_arn = getattr(settings, "SAGEMAKER_EXECUTION_ROLE_ARN", "")
    boto3_client("sagemaker").create_processing_job(
        ProcessingJobName=job_name,
        RoleArn=role_arn,
        AppSpecification={
            "ImageUri": training_image_uri(prep.config["stack"], runtime),
            "ContainerEntrypoint": ["python", "-m", "overbae.sagemaker.sft_entrypoint"],
        },
        ProcessingResources={
            "ClusterConfig": {
                "InstanceCount": 1,
                "InstanceType": "ml.m5.2xlarge",
                "VolumeSizeInGB": 100,
            }
        },
        Environment={
            "OVERMIND_SAGEMAKER_MODE": "prepare",
            "OVERMIND_PREPARATION_ID": str(prep.id),
            "AWS_SFT_BUCKET_NAME": bucket,
            "OVERMIND_PREPARATION_REQUEST": json.dumps(request_payload),
        },
        StoppingCondition={"MaxRuntimeInSeconds": 23 * 60 * 60},
    )
    return job_name


def poll_preparation(prep: TrainingPreparation) -> dict[str, Any]:
    bucket = sft_bucket()
    resp = boto3_client("sagemaker").describe_processing_job(ProcessingJobName=prep.remote_id)
    status = str(resp.get("ProcessingJobStatus") or "")
    if status in ("InProgress", "Stopping"):
        raise TimeoutError("Processing job still running")
    report = get_json(bucket, f"preparations/{prep.id}/report.json")
    if report is not None:
        return validate_preparation_report(report)
    if status == "Completed":
        return validate_preparation_report({"ready": False, "error_code": "invalid_report"})
    return preparation_failure("process_failed")


def observe_progress(prep: TrainingPreparation) -> None:
    bucket = sft_bucket()
    raw = get_json(bucket, f"preparations/{prep.id}/progress.json")
    if not isinstance(raw, dict):
        return
    progress = {
        key: raw[key]
        for key in ("completed_rows", "reused_rows", "committed_shards", "updated_at")
        if isinstance(raw.get(key), (int, float)) and math.isfinite(raw[key]) and raw[key] >= 0
    }
    progress["stage"] = str(raw.get("stage", "tokenizing"))[:80]
    progress["total_rows"] = prep.cell.rows + (
        prep.validation_cell.rows if prep.validation_cell_id else 0
    )
    TrainingPreparation.objects.filter(
        pk=prep.pk, state="running", remote_id=prep.remote_id
    ).update(report={"progress": progress}, touched_at=timezone.now())
    operational_progress.preparation(prep, progress)


def cancel_preparation(remote_id: str) -> None:
    if not remote_id:
        return
    with suppress(Exception):
        boto3_client("sagemaker").stop_processing_job(ProcessingJobName=remote_id)


def stage_run_dataset_s3(
    *,
    run_id: str,
    preparation_id: str,
    artifact_sha256: str,
    selections: dict[str, dict[str, Any]],
    selection_dir: Path,
    monitoring: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket = sft_bucket()
    if not preparation_id:
        raise ValueError("A validated preprocessing artifact is required.")
    report = get_json(bucket, f"preparations/{preparation_id}/report.json")
    if not isinstance(report, dict):
        raise ValueError("Preprocessing report is missing from S3.")
    if not report.get("ready"):
        raise ValueError("This preprocessing artifact has incompatible rows.")
    if report.get("artifact_sha256") != artifact_sha256:
        raise ValueError("The preprocessing artifact differs from the approved preparation.")
    if "data" not in selections or set(selections) - {"data", "val"}:
        raise ValueError("Training selections must contain training and optional validation rows.")
    if not selections["data"].get("rows"):
        raise ValueError("The training selection is empty.")

    with tempfile.TemporaryDirectory(prefix="sm-stage-run-") as tmp:
        workspace = Path(tmp)
        prep_dir = workspace / "prep"
        run_dir = workspace / "run"
        prep_dir.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)

        sync_s3_to_directory(bucket, f"preparations/{preparation_id}", prep_dir)
        for name in selections:
            src_keys = selection_dir / f"{name}.keys"
            dst_keys = run_dir / f"selected-{name}.keys"
            dst_keys.write_bytes(src_keys.read_bytes())

        files = [
            (run_dir / f"selected-{name}.keys", run_dir / f"{name}.jsonl", selection)
            for name, selection in selections.items()
        ]

        def report_progress(values):
            put_json(
                bucket,
                f"runs/{run_id}/transfer-progress.json",
                {"source_at": time.time(), **values},
            )

        materialize_files(
            prep_dir / "tokens.jsonl",
            artifact_sha256,
            files,
            progress=report_progress,
        )
        report_progress({"stage": "committing_training_data"})
        if (prep_dir / "tokenizer").is_dir():
            shutil.copytree(prep_dir / "tokenizer", run_dir / "tokenizer", dirs_exist_ok=True)
        (run_dir / "preparation.json").write_text(json.dumps(report))
        plan = (
            freeze_run_files(run_dir, monitoring)
            if monitoring and monitoring.get("mode") != "off"
            else None
        )
        sync_directory_to_s3(run_dir, bucket, f"runs/{run_id}")
        report_progress({"stage": "training_data_ready"})
        return {
            "run_id": run_id,
            "run_dir": f"s3://{bucket}/runs/{run_id}",
            "monitoring_plan": plan,
        }
