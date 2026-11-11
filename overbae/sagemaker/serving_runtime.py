from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass

from django.conf import settings

from overbae.core.aws import boto3_client
from overbae.core.errors import InputValidationError
from overbae.sagemaker.instance_map import sagemaker_instance_type


@dataclass
class SageMakerDedicatedHandle:
    endpoint_name: str
    component_name: str
    gpu_type: str
    min_replicas: int
    max_replicas: int
    idle_seconds: int


def endpoint_name_for(deployment) -> str:
    return f"om-ded-{str(deployment.id).replace('-', '')[:20]}"[:63]


def component_name_for(deployment, gpu_type: str | None = None) -> str:
    gpu = (gpu_type or deployment.gpu_type or "H100").replace("-", "").lower()
    return f"om-ic-{str(deployment.id).replace('-', '')[:16]}-{gpu}"[:63]


def bind(deployment, *, minimum: int, maximum: int, idle_seconds: int) -> SageMakerDedicatedHandle:
    sm = boto3_client("sagemaker")
    aas = boto3_client("application-autoscaling")
    endpoint_name = endpoint_name_for(deployment)
    component_name = component_name_for(deployment)
    instance_type = sagemaker_instance_type(deployment.gpu_type, 1)
    role_arn = getattr(settings, "SAGEMAKER_EXECUTION_ROLE_ARN", "")
    wbucket = getattr(settings, "AWS_WEIGHTS_BUCKET_NAME", "")
    prefix = getattr(settings, "SAGEMAKER_ECR_REPOSITORY_PREFIX", "overmind")
    tag = getattr(settings, "SAGEMAKER_IMAGE_TAG", "latest")

    deployed = deployment.deployed_model
    config_name = f"{endpoint_name}-cfg"[:63]

    try:
        sm.describe_endpoint(EndpointName=endpoint_name)
    except Exception:  # noqa: BLE001
        with contextlib.suppress(Exception):
            sm.create_endpoint_config(
                EndpointConfigName=config_name,
                ExecutionRoleArn=role_arn,
                ProductionVariants=[
                    {
                        "VariantName": "AllTraffic",
                        "InstanceType": instance_type,
                        "InitialInstanceCount": max(1, int(minimum) if int(minimum) > 0 else 1),
                        "RoutingConfig": {"RoutingStrategy": "LEAST_OUTSTANDING_REQUESTS"},
                    }
                ],
            )
        with contextlib.suppress(Exception):
            sm.create_endpoint(
                EndpointName=endpoint_name,
                EndpointConfigName=config_name,
            )

    desired = max(0, int(minimum))
    spec = {
        "Container": {
            "Image": f"{prefix}-serve-vllm:{tag}",
            "ArtifactUrl": f"s3://{wbucket}/{deployed.model_id}/",
            "Environment": {
                "SERVED_MODEL_ID": deployment.served_model_id,
                "MAX_MODEL_LEN": str(deployed.max_model_len or 8192),
            },
        },
        "StartupParameters": {
            "ModelDataDownloadTimeoutInSeconds": 1800,
            "ContainerStartupHealthCheckTimeoutInSeconds": 1800,
        },
        "ComputeResourceRequirements": {
            "NumberOfAcceleratorDevicesRequired": 1.0,
            "NumberOfCpuCoresRequired": 4.0,
            "MinMemoryRequiredInMb": 16384,
        },
    }
    runtime_cfg = {"CopyCount": max(desired, 1 if int(maximum) > 0 else 0)}
    if int(minimum) == 0 and int(idle_seconds) <= 2:
        runtime_cfg = {"CopyCount": 0}

    try:
        sm.describe_inference_component(InferenceComponentName=component_name)
        sm.update_inference_component(
            InferenceComponentName=component_name,
            Specification=spec,
            RuntimeConfig=runtime_cfg,
        )
    except Exception:  # noqa: BLE001
        sm.create_inference_component(
            InferenceComponentName=component_name,
            EndpointName=endpoint_name,
            VariantName="AllTraffic",
            Specification=spec,
            RuntimeConfig=runtime_cfg,
        )

    resource_id = f"inference-component/{component_name}"
    aas.register_scalable_target(
        ServiceNamespace="sagemaker",
        ResourceId=resource_id,
        ScalableDimension="sagemaker:inference-component:DesiredCopyCount",
        MinCapacity=max(0, int(minimum)),
        MaxCapacity=max(1, int(maximum)),
    )
    aas.put_scaling_policy(
        PolicyName=f"{component_name}-scaling",
        ServiceNamespace="sagemaker",
        ResourceId=resource_id,
        ScalableDimension="sagemaker:inference-component:DesiredCopyCount",
        PolicyType="TargetTrackingScaling",
        TargetTrackingScalingPolicyConfiguration={
            "TargetValue": 5.0,
            "PredefinedMetricSpecification": {
                "PredefinedMetricType": "SageMakerInferenceComponentInvocationsPerCopy"
            },
            "ScaleInCooldown": max(2, int(idle_seconds)),
            "ScaleOutCooldown": 30,
        },
    )

    return SageMakerDedicatedHandle(
        endpoint_name=endpoint_name,
        component_name=component_name,
        gpu_type=deployment.gpu_type,
        min_replicas=int(minimum),
        max_replicas=int(maximum),
        idle_seconds=int(idle_seconds),
    )


def release(deployment, gpu_type: str) -> None:
    sm = boto3_client("sagemaker")
    comp = component_name_for(deployment, gpu_type)
    with contextlib.suppress(Exception):
        sm.delete_inference_component(InferenceComponentName=comp)


def retire_previous_options(deployment) -> None:
    _ = deployment


def spawn_probe(instance: SageMakerDedicatedHandle, deployment) -> str:
    from overbae.sagemaker.s3_store import put_json, weights_bucket

    deployed = deployment.deployed_model
    kind = "decision" if deployed.inference_contract == "decision" else "chat"
    call_id = f"sm-probe:{deployment.id}"
    put_json(
        weights_bucket(),
        f".ops/{call_id}.json",
        {
            "endpoint_name": instance.endpoint_name,
            "component_name": instance.component_name,
            "kind": kind,
            "served_model_id": deployment.served_model_id,
        },
    )
    return call_id


def probe_state(call_id: str) -> str:
    from overbae.sagemaker.s3_store import get_json, weights_bucket

    if not call_id.startswith("sm-probe:"):
        return "failed"
    record = get_json(weights_bucket(), f".ops/{call_id}.json")
    if not isinstance(record, dict):
        return "failed"
    endpoint_name = record["endpoint_name"]
    component_name = record["component_name"]
    kind = record["kind"]
    served_model_id = record["served_model_id"]
    sm = boto3_client("sagemaker")
    try:
        ic = sm.describe_inference_component(InferenceComponentName=component_name)
        status = ic.get("InferenceComponentStatus", "InService")
        if status in ("Creating", "Updating"):
            return "pending"
        if status == "Failed":
            return "failed"
    except Exception:  # noqa: BLE001
        return "failed"

    body = (
        {
            "model": served_model_id,
            "state": "Serving readiness check.",
            "questions": {
                "ready": {
                    "type": "choice",
                    "instructions": "Select an option.",
                    "criteria": {"A": "A", "B": "B"},
                }
            },
        }
        if kind == "decision"
        else {
            "model": served_model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    )
    try:
        boto3_client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            InferenceComponentName=component_name,
            ContentType="application/json",
            Body=json.dumps(body).encode("utf-8"),
        )
    except Exception:  # noqa: BLE001
        return "failed"
    return "ok"


def runner_count(instance: SageMakerDedicatedHandle, *, decision: bool) -> int | None:
    del decision
    try:
        ic = boto3_client("sagemaker").describe_inference_component(
            InferenceComponentName=instance.component_name
        )
    except Exception:  # noqa: BLE001
        return None
    rt = ic.get("RuntimeConfig") or {}
    if "CurrentCopyCount" in rt:
        return int(rt["CurrentCopyCount"])
    if "DesiredCopyCount" in rt:
        return int(rt["DesiredCopyCount"])
    if "CopyCount" in rt:
        return int(rt["CopyCount"])
    return None


def invoke_chat(deployment, shaped_payload: bytes) -> tuple[int, bytes, str]:
    endpoint_name = endpoint_name_for(deployment)
    component_name = component_name_for(deployment)
    try:
        resp = boto3_client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            InferenceComponentName=component_name,
            ContentType="application/json",
            Accept="application/json",
            Body=shaped_payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise InputValidationError("Dedicated worker is not deployed.") from exc
    body_stream = resp.get("Body")
    raw = body_stream.read() if hasattr(body_stream, "read") else (body_stream or b"{}")
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return 200, raw, str(resp.get("ContentType") or "application/json")


def iter_chat_stream(deployment, shaped_payload: bytes):
    endpoint_name = endpoint_name_for(deployment)
    component_name = component_name_for(deployment)
    try:
        resp = boto3_client("sagemaker-runtime").invoke_endpoint_with_response_stream(
            EndpointName=endpoint_name,
            InferenceComponentName=component_name,
            ContentType="application/json",
            Accept="text/event-stream",
            Body=shaped_payload,
        )
    except Exception as exc:  # noqa: BLE001
        raise InputValidationError("Dedicated worker is not deployed.") from exc
    stream = resp.get("Body") or []
    for event in stream:
        chunk = (event.get("PayloadPart") or {}).get("Bytes") if isinstance(event, dict) else None
        if chunk:
            yield chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8")


def post_systemone(deployment, body: dict) -> dict:
    endpoint_name = endpoint_name_for(deployment)
    component_name = component_name_for(deployment)
    try:
        resp = boto3_client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            InferenceComponentName=component_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(body).encode("utf-8"),
        )
    except Exception as exc:  # noqa: BLE001
        raise InputValidationError("Dedicated worker is not deployed.") from exc
    body_stream = resp.get("Body")
    raw = body_stream.read() if hasattr(body_stream, "read") else (body_stream or b"{}")
    return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)


def spawn_inference_request(request) -> str:
    from overbae.sagemaker.s3_store import put_json, weights_bucket

    payload = request.payload
    model_id = payload["model_id"]
    endpoint_name = f"om-ep-{model_id}"[:63]
    started = time.monotonic()
    if payload.get("inference_contract") == "decision":
        decisions = payload.get("decisions") or []
        questions = {
            str(idx): {
                "type": "choice",
                "instructions": item["question"],
                "criteria": {opt: opt for opt in item["options"]},
            }
            for idx, item in enumerate(decisions)
        }
        req_body = {
            "model": model_id,
            "state": decisions[0]["state"] if decisions else "",
            "questions": questions,
        }
        resp = boto3_client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(req_body).encode("utf-8"),
        )
        body_stream = resp.get("Body")
        raw = body_stream.read() if hasattr(body_stream, "read") else (body_stream or b"{}")
        parsed = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        answers = parsed.get("answers") or {}
        predictions = [
            {
                "options": item["options"],
                "probabilities": [
                    float((answers.get(str(idx)) or {}).get("probabilities", {}).get(opt) or 0.0)
                    for opt in item["options"]
                ],
            }
            for idx, item in enumerate(decisions)
        ]
        result = {
            "predictions": predictions,
            "usage": parsed.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0},
            "finish_reason": "stop",
            "latency_ms": (time.monotonic() - started) * 1000,
            "model_identity": payload.get("artifact_identity") or model_id,
        }
    else:
        req_body = {
            "model": model_id,
            "messages": payload["messages"],
            "temperature": payload.get("temperature", 1.0),
            "max_tokens": payload.get("max_tokens", 1024),
        }
        resp = boto3_client("sagemaker-runtime").invoke_endpoint(
            EndpointName=endpoint_name,
            ContentType="application/json",
            Accept="application/json",
            Body=json.dumps(req_body).encode("utf-8"),
        )
        body_stream = resp.get("Body")
        raw = body_stream.read() if hasattr(body_stream, "read") else (body_stream or b"{}")
        parsed = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
        choice = (parsed.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        result = {
            "content": msg.get("content") or "",
            "usage": parsed.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0},
            "finish_reason": choice.get("finish_reason") or "stop",
            "latency_ms": (time.monotonic() - started) * 1000,
            "model_identity": model_id,
        }

    call_id = f"sm-infer:{request.pk}"
    put_json(weights_bucket(), f".ops/{call_id}.json", {"state": "complete", "result": result})
    return call_id
