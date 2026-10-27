"""Binds one dedicated deployment to its own Modal parameter set."""

from __future__ import annotations

import inspect
import json
import os
from decimal import Decimal

import modal
from asgiref.sync import async_to_sync

from modal_shared.shared import rel_weights_path
from overbae.core.errors import InputValidationError

APP_NAME = "overmind-dedicated"
WORKER_UNAVAILABLE = "Dedicated worker is not deployed."


def environment() -> str:
    return os.environ.get("MODAL_ENVIRONMENT") or "overmind-dev"


def _drive(result):
    if inspect.iscoroutine(result):

        async def _wait():
            return await result

        return async_to_sync(_wait)()
    return result


def _class_name(deployed, gpu_type: str) -> str:
    token = gpu_type.replace("-", "")
    return f"{token}_vllm_lora" if deployed.is_lora else f"{token}_vllm"


def _base_identity(model_path: str) -> str:
    try:
        volume = modal.Volume.from_name("overmind-weights", environment_name=environment())
        raw = b"".join(volume.read_file(f"{model_path}/.base-manifest.json"))
        identity = json.loads(raw)["identity"]
    except Exception as exc:
        raise InputValidationError(WORKER_UNAVAILABLE) from exc
    if not isinstance(identity, str) or not identity:
        raise InputValidationError(WORKER_UNAVAILABLE)
    return identity


def parameters(deployment) -> dict:
    deployed = deployment.deployed_model
    if deployed.inference_contract == "decision":
        return {
            "weights_path": deployed.weights_path,
            "artifact_identity": deployed.checkpoint_hash,
            "max_model_len": deployed.max_model_len,
            "pool": str(deployment.id),
        }
    path = rel_weights_path(deployed.weights_path)
    # Unique model name: a shared base name would put every LoRA of that base
    # in one pool and one tenant's floor would warm the others.
    params = {
        "model_path": path,
        "model_name": deployment.public_model_id
        if not deployed.is_lora
        else f"dedicated-base-{deployment.id}",
        "max_model_len": deployed.max_model_len,
        "enable_lora": bool(deployed.is_lora),
        "max_lora_rank": deployed.lora_rank or 16,
    }
    if deployed.is_lora:
        params["base_identity"] = _base_identity(path)
    return params


def bind(deployment, *, minimum: int, maximum: int, idle_seconds: int):
    deployed = deployment.deployed_model
    env = environment()
    try:
        if deployed.inference_contract == "decision":
            runtime = deployed.serving_runtime or {}
            app_name = runtime.get("app")
            if not app_name:
                raise InputValidationError(WORKER_UNAVAILABLE)
            cls = modal.Cls.from_name(
                app_name, "DecisionEndpoint", environment_name=runtime.get("environment") or env
            )
            if hasattr(cls, "with_options"):
                cls = cls.with_options(
                    gpu=deployment.gpu_type,
                    max_containers=max(maximum, 1),
                    scaledown_window=idle_seconds,
                )
        else:
            cls = modal.Cls.from_name(
                APP_NAME, _class_name(deployed, deployment.gpu_type), environment_name=env
            )
            if hasattr(cls, "with_options"):
                cls = cls.with_options(
                    max_containers=max(maximum, 1),
                    scaledown_window=idle_seconds,
                )
        instance = cls(**parameters(deployment))
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError(WORKER_UNAVAILABLE) from exc
    method = getattr(instance, "update_autoscaler", None)
    if method is not None:
        _drive(
            method(
                min_containers=minimum,
                max_containers=max(maximum, 1),
                scaledown_window=idle_seconds,
            )
        )
    return instance


def spawn_probe(instance, deployment) -> str:
    deployed = deployment.deployed_model
    if deployed.inference_contract == "decision":
        call = instance.predict.spawn(
            [
                {
                    "state": "Serving readiness check.",
                    "question": "Select an option.",
                    "kind": "choice",
                    "options": ["A", "B"],
                }
            ]
        )
    else:
        adapter = None
        if deployed.is_lora:
            adapter = (
                deployment.public_model_id,
                rel_weights_path(deployed.adapter_path),
            )
        call = instance.infer.spawn(
            method="POST",
            path="/v1/chat/completions",
            body=json.dumps(
                {
                    "model": deployment.public_model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            ).encode(),
            headers={"content-type": "application/json"},
            adapter=adapter,
        )
    return call.object_id


def probe_state(call_id: str) -> str:
    try:
        result = modal.FunctionCall.from_id(call_id).get(timeout=0)
    except TimeoutError:
        return "pending"
    except Exception:
        return "failed"
    if not isinstance(result, dict):
        return "failed"
    if result.get("error_code"):
        return "failed"
    status = result.get("status")
    if isinstance(status, int) and status >= 400:
        return "failed"
    return "ok"


def runner_count(instance, *, decision: bool) -> int | None:
    method = instance.predict if decision else instance.infer
    try:
        stats_call = method.get_current_stats
        stats = _drive(stats_call.aio()) if hasattr(stats_call, "aio") else stats_call()
    except Exception:
        return None
    value = getattr(stats, "num_total_runners", None)
    return int(value) if value is not None else None


def chat(instance, deployment, body: bytes) -> tuple[int, bytes, str]:
    deployed = deployment.deployed_model
    adapter = None
    if deployed.is_lora:
        adapter = (deployment.public_model_id, rel_weights_path(deployed.adapter_path))
    result = instance.infer.remote(
        method="POST",
        path="/v1/chat/completions",
        body=body,
        headers={"content-type": "application/json"},
        adapter=adapter,
    )
    status = int(result.get("status") or 502)
    payload = result.get("body") or b""
    if isinstance(payload, str):
        payload = payload.encode()
    content_type = (result.get("headers") or {}).get("content-type") or "application/json"
    return status, payload, content_type


def predict(instance, decisions: list) -> dict:
    result = instance.predict.remote(decisions)
    if not isinstance(result, dict) or result.get("error_code"):
        raise InputValidationError(WORKER_UNAVAILABLE)
    return result


def minute_usd(rate: Decimal, replicas: int) -> Decimal:
    return (rate * Decimal(replicas)) / Decimal(60)
