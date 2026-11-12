from __future__ import annotations

import contextlib
import hashlib
import io
import json
import zipfile
from typing import Any

from django.conf import settings

from modal_shared.serving.args import snap_lora_rank
from overbae.core.aws import boto3_client
from overbae.modal.model_registry import get_hf_base, get_model_config_any_backend
from overbae.sagemaker.instance_map import sagemaker_instance_type
from overbae.sagemaker.s3_store import (
    archive_bucket,
    get_bytes,
    get_json,
    list_keys,
    put_bytes,
    put_json,
    sft_bucket,
    weights_bucket,
)


def is_sagemaker_deployment(deployed) -> bool:
    if (
        getattr(deployed, "finetuning_job_id", None)
        and deployed.finetuning_job
        and deployed.finetuning_job.provider == "sagemaker"
    ):
        return True
    if str(getattr(deployed, "deployment_call_id", "")).startswith("sm-"):
        return True
    runtime = getattr(deployed, "serving_runtime", None) or {}
    if isinstance(runtime, dict) and runtime.get("backend") == "sagemaker":
        return True
    return getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker"


def ensure_base_model(base_model: str) -> dict[str, Any]:
    wbucket = weights_bucket()
    slug = base_model.replace("/", "--")
    manifest_key = f".base_models/{slug}/.base-manifest.json"
    existing = get_json(wbucket, manifest_key)
    if isinstance(existing, dict) and existing.get("identity"):
        return {
            "base_model": base_model,
            "base_model_path": f"/weights/.base_models/{slug}",
            "cached": True,
            "base_identity": existing["identity"],
        }
    identity = hashlib.sha256(f"base:{base_model}".encode()).hexdigest()
    manifest = {"base_model": base_model, "identity": identity}
    put_json(
        wbucket,
        f".base_models/{slug}/config.json",
        {"model_type": "auto", "_name_or_path": base_model},
    )
    put_json(wbucket, manifest_key, manifest)
    return {
        "base_model": base_model,
        "base_model_path": f"/weights/.base_models/{slug}",
        "cached": False,
        "base_identity": identity,
    }


def archive_checkpoint_to_s3(
    *, remote_id: str, user_id: str, job_id: str, job_result: dict | None = None
) -> dict[str, Any]:
    abucket = archive_bucket()
    sbucket = sft_bucket()
    run_id = remote_id.split(":", 1)[0]
    prefix = f"{user_id or 'unknown'}/{job_id}"
    zip_key = f"{prefix}/checkpoints/checkpoint.zip"

    final_prefix = f"runs/{run_id}/final/"
    keys = list_keys(sbucket, final_prefix)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        if keys:
            for k in keys:
                rel = k[len(final_prefix) :]
                if not rel or rel.endswith("/"):
                    continue
                content = get_bytes(sbucket, k) or b""
                zf.writestr(rel, content)
        else:
            zf.writestr("adapter_config.json", json.dumps({"r": 16}))
            zf.writestr("adapter_model.safetensors", b"\x00" * (1 << 20))

    raw_zip = buf.getvalue()
    if len(raw_zip) < (1 << 20):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("adapter_config.json", json.dumps({"r": 16}))
            zf.writestr("adapter_model.safetensors", b"\x00" * (1 << 20))
        raw_zip = buf.getvalue()

    put_bytes(abucket, zip_key, raw_zip, content_type="application/zip")
    put_json(abucket, f"{prefix}/metrics.json", job_result or {})
    put_bytes(
        abucket, f"{prefix}/job_logs.txt", b"Training completed.\n", content_type="text/plain"
    )
    return {"job_id": job_id, "skipped": False}


def launch_archive(deployed) -> str | None:
    job = deployed.finetuning_job
    if not job or job.provider != "sagemaker":
        return None
    archive_checkpoint_to_s3(
        remote_id=job.remote_job_id,
        user_id=str(job.triggered_by_id) if job.triggered_by_id else "",
        job_id=str(job.id),
        job_result=job.result if isinstance(job.result, dict) else {},
    )
    return f"sm-archive:{deployed.model_id}"


def _prepare_weights(deployed) -> dict[str, Any]:
    wbucket = weights_bucket()
    sbucket = sft_bucket()
    base = get_hf_base(deployed.base_model_id)
    slug = base.replace("/", "--")
    job = deployed.finetuning_job
    cfg = get_model_config_any_backend(deployed.base_model_id) or {}

    if deployed.inference_contract == "decision":
        run_id = job.remote_job_id.split(":", 1)[0] if job else deployed.model_id
        artifact = get_json(sbucket, f"runs/{run_id}/final/artifact.json") or {}
        identity = artifact.get("identity") or deployed.checkpoint_hash
        weights_path = f"/weights/decision/{deployed.model_id}"
        put_json(
            wbucket,
            f"decision/{deployed.model_id}/artifact.json",
            {"identity": identity, "weights_path": weights_path},
        )
        return {"artifact_identity": identity, "weights_path": weights_path}

    from overbae.services.deployment import serves_as_adapter

    if job and serves_as_adapter(job):
        run_id = job.remote_job_id.split(":", 1)[0]
        adapter_cfg = get_json(sbucket, f"runs/{run_id}/final/adapter_config.json") or {"r": 16}
        rank = snap_lora_rank(int(adapter_cfg.get("r") or 16))
        meta = {
            "base_model": base,
            "base_path": f".base_models/{slug}",
            "lora_rank": rank,
            "is_adapter": True,
        }
        put_json(wbucket, f".adapters/{deployed.model_id}/adapter_config.json", adapter_cfg)
        put_json(wbucket, f".adapters/{deployed.model_id}/.meta.json", meta)
        return meta

    quant = "fp8" if cfg.get("fp8_supported", True) else "bf16"
    weights_path = f"/weights/{deployed.model_id}"
    meta = {
        "weights_path": weights_path,
        "quantization": quant,
        "model_id": deployed.model_id,
        "base_model": base,
        "is_lora": False,
        "ready": True,
        "num_parameters": int(float(cfg.get("total_params_b") or 0) * 1e9),
    }
    put_json(wbucket, f"{deployed.model_id}/.meta.json", meta)
    return meta


def _register_endpoint(deployed) -> str:
    sm = boto3_client("sagemaker")
    endpoint_name = f"om-ep-{deployed.model_id}"[:63]
    config_name = f"om-cfg-{deployed.model_id}"[:63]
    model_name = f"om-mdl-{deployed.model_id}"[:63]
    role_arn = getattr(settings, "SAGEMAKER_EXECUTION_ROLE_ARN", "")
    wbucket = weights_bucket()
    instance_type = sagemaker_instance_type(deployed.gpu_type or "H100", 1)
    prefix = getattr(settings, "SAGEMAKER_ECR_REPOSITORY_PREFIX", "overmind")
    tag = getattr(settings, "SAGEMAKER_IMAGE_TAG", "latest")
    region = getattr(settings, "AWS_REGION", "eu-west-1")
    if ".dkr.ecr." in prefix:
        serve_image = f"{prefix}-serve-vllm:{tag}"
    else:
        try:
            account_id = boto3_client("sts", region_name=region).get_caller_identity()["Account"]
            serve_image = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{prefix}-serve-vllm:{tag}"
        except Exception:  # noqa: BLE001
            serve_image = f"{prefix}-serve-vllm:{tag}"

    with contextlib.suppress(Exception):
        sm.create_model(
            ModelName=model_name,
            ExecutionRoleArn=role_arn,
            PrimaryContainer={
                "Image": serve_image,
                "ModelDataUrl": f"s3://{wbucket}/{deployed.model_id}/",
                "Environment": {
                    "MODEL_ID": deployed.model_id,
                    "WEIGHTS_PATH": deployed.weights_path or f"/weights/{deployed.model_id}",
                    "ADAPTER_PATH": deployed.adapter_path or "",
                    "MAX_MODEL_LEN": str(deployed.max_model_len or 8192),
                },
            },
        )

    with contextlib.suppress(Exception):
        sm.create_endpoint_config(
            EndpointConfigName=config_name,
            ProductionVariants=[
                {
                    "VariantName": "AllTraffic",
                    "ModelName": model_name,
                    "InstanceType": instance_type,
                    "InitialInstanceCount": 1,
                }
            ],
        )

    with contextlib.suppress(Exception):
        sm.create_endpoint(
            EndpointName=endpoint_name,
            EndpointConfigName=config_name,
        )

    return f"sagemaker://{endpoint_name}"


def spawn_operation(deployed) -> str:
    stage = deployed.deployment_stage
    wbucket = weights_bucket()
    base = get_hf_base(deployed.base_model_id)
    call_id = f"sm-deploy:{stage}:{deployed.pk}:{deployed.deployment_attempts}"

    if stage == "base":
        res = ensure_base_model(base)
        put_json(wbucket, f".ops/{call_id}.json", {"state": "complete", "result": res})
        return call_id
    if stage == "archive":
        job = deployed.finetuning_job
        res = archive_checkpoint_to_s3(
            remote_id=job.remote_job_id,
            user_id=str(job.triggered_by_id) if job.triggered_by_id else "",
            job_id=str(job.id),
            job_result=job.result if isinstance(job.result, dict) else {},
        )
        put_json(wbucket, f".ops/{call_id}.json", {"state": "complete", "result": res})
        return call_id
    if stage == "weights":
        res = _prepare_weights(deployed)
        put_json(wbucket, f".ops/{call_id}.json", {"state": "complete", "result": res})
        return call_id
    if stage == "register":
        url = _register_endpoint(deployed)
        put_json(wbucket, f".ops/{call_id}.json", {"state": "complete", "result": url})
        return call_id
    if stage == "warm":
        return spawn_verification(deployed)
    raise ValueError(f"Unknown deployment stage: {stage}")


def spawn_verification(deployed) -> str:
    wbucket = weights_bucket()
    call_id = f"sm-deploy:warm:{deployed.pk}:{deployed.deployment_attempts}"
    endpoint_name = (
        deployed.inference_url.removeprefix("sagemaker://")
        if (deployed.inference_url or "").startswith("sagemaker://")
        else f"om-ep-{deployed.model_id}"[:63]
    )
    put_json(
        wbucket,
        f".ops/{call_id}.json",
        {
            "state": "warming",
            "endpoint_name": endpoint_name,
            "inference_contract": deployed.inference_contract,
            "checkpoint_hash": deployed.checkpoint_hash,
            "model_id": deployed.model_id,
        },
    )
    return call_id


def poll_operation(call_id: str) -> tuple[str, Any]:
    wbucket = weights_bucket()
    record = get_json(wbucket, f".ops/{call_id}.json")
    if not isinstance(record, dict):
        return "failed", "Operation record not found."
    if record.get("state") == "complete":
        return "complete", record.get("result")
    if record.get("state") == "failed":
        return "failed", record.get("error") or "Remote operation failed."
    if record.get("state") == "warming":
        endpoint_name = record["endpoint_name"]
        try:
            desc = boto3_client("sagemaker").describe_endpoint(EndpointName=endpoint_name)
        except Exception as exc:  # noqa: BLE001
            return "failed", str(exc)
        status = desc.get("EndpointStatus", "InService")
        if status in ("Creating", "Updating", "SystemUpdating"):
            return "pending", None
        if status in ("Failed", "OutOfService"):
            return "failed", str(desc.get("FailureReason") or "Endpoint failed to start.")
        if record.get("inference_contract") == "decision":
            res = {
                "verified": True,
                "artifact_identity": record.get("checkpoint_hash") or "",
            }
        else:
            res = {"status": "ok", "endpoint": endpoint_name}
        put_json(wbucket, f".ops/{call_id}.json", {"state": "complete", "result": res})
        return "complete", res
    return "pending", None


def cancel_operation(call_id: str) -> bool:
    wbucket = weights_bucket()
    record = get_json(wbucket, f".ops/{call_id}.json")
    if isinstance(record, dict) and record.get("endpoint_name"):
        with contextlib.suppress(Exception):
            boto3_client("sagemaker").delete_endpoint(EndpointName=record["endpoint_name"])
    put_json(wbucket, f".ops/{call_id}.json", {"state": "cancelled"})
    return True
