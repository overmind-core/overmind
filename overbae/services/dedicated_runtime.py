"""Binds one dedicated deployment to its own Modal parameter set."""

from __future__ import annotations

import inspect
import json
import os
from decimal import Decimal

import modal
from asgiref.sync import async_to_sync

from modal_shared.serving.capacity import sequence_cap
from modal_shared.shared import rel_weights_path
from overbae.core.errors import InputValidationError
from overbae.modal.gpu_selector import gpu_vram_gb
from overbae.modal.model_registry import get_model_config_any_backend
from overbae.services.dedicated_offer import serving_shape

APP_NAME = "overmind-dedicated"
WORKER_UNAVAILABLE = "Dedicated worker is not deployed."
# with_options() builds a separate autoscaler. Keep one option set and change
# min, max, and idle through update_autoscaler, or a GPU change leaves the old set running.
_STABLE_MAX = 8
_STABLE_WINDOW = 3600


def environment() -> str:
    return os.environ.get("MODAL_ENVIRONMENT") or "overmind-dev"


def _drive(result):
    if inspect.iscoroutine(result):

        async def _wait():
            return await result

        return async_to_sync(_wait)()
    return result


def worker_class_name(_deployed, gpu_type: str) -> str:
    # Not `{gpu}_vllm`: that global is the serverless class, and Modal loads a class
    # with getattr(module, name).
    token = gpu_type.replace("-", "")
    return f"{token}_vllm_dedicated"


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
    params = {
        "model_path": path,
        "model_name": deployment.served_model_id,
        "max_model_len": deployed.max_model_len,
        "enable_lora": False,
        "max_lora_rank": deployed.lora_rank or 16,
    }
    if deployed.is_lora:
        params["adapter_path"] = rel_weights_path(deployed.adapter_path)
    return params


def _window(seconds: int) -> int:
    return min(max(int(seconds), 2), _STABLE_WINDOW)


def _chat_cls(deployed, gpu_type: str):
    return modal.Cls.from_name(
        APP_NAME, worker_class_name(deployed, gpu_type), environment_name=environment()
    )


def chat_sequence_cap(deployment) -> int | None:
    """Full-context sequences on this deployment's GPU. None when the shape is unknown."""
    deployed = deployment.deployed_model
    cfg = get_model_config_any_backend(deployed.base_model_id) or {}
    params = cfg.get("total_params_b")
    if not params:
        return None
    fp8 = serving_shape(deployed) == "merged_fp8" and bool(cfg.get("fp8_supported"))
    return sequence_cap(
        vram_gb=gpu_vram_gb(deployment.gpu_type),
        weight_gb=float(params) * (1 if fp8 else 2),
        num_attn_layers=cfg.get("num_attn_layers"),
        num_kv_heads=cfg.get("num_kv_heads"),
        head_dim=cfg.get("head_dim"),
        max_model_len=deployed.max_model_len,
    )


def _variant(cls, deployment, *, maximum: int, idle_seconds: int):
    if hasattr(cls, "with_options"):
        cls = cls.with_options(
            max_containers=max(int(maximum), 1), scaledown_window=_window(idle_seconds)
        )
    cap = chat_sequence_cap(deployment)
    # The class default is 32. A lower fit has to be its own input target or Modal
    # keeps filling a container that can only run a few sequences.
    if cap is not None and cap < 32 and hasattr(cls, "with_concurrency"):
        admitted = max(cap, 1)
        cls = cls.with_concurrency(max_inputs=admitted, target_inputs=max(1, int(admitted * 0.7)))
    return cls


def _scale(cls, deployment, *, minimum: int, maximum: int, idle_seconds: int):
    instance = cls(**parameters(deployment))
    method = getattr(instance, "update_autoscaler", None)
    if method is not None:
        _drive(
            method(
                min_containers=minimum,
                max_containers=max(int(maximum), 1),
                scaledown_window=_window(idle_seconds),
            )
        )
    return instance


def release(deployment, gpu_type: str) -> None:
    """Drop every autoscaler variant for one GPU class."""
    deployed = deployment.deployed_model
    if deployed.inference_contract == "decision":
        return
    try:
        cls = _chat_cls(deployed, gpu_type)
        option_sets = (
            (deployment.max_replicas, deployment.idle_shutdown_seconds),
            (_STABLE_MAX, _STABLE_WINDOW),
        )
        seen = set()
        for maximum, idle_seconds in option_sets:
            key = (max(int(maximum), 1), _window(idle_seconds))
            if key in seen:
                continue
            seen.add(key)
            _scale(
                _variant(cls, deployment, maximum=maximum, idle_seconds=idle_seconds),
                deployment,
                minimum=0,
                maximum=1,
                idle_seconds=2,
            )
        _scale(cls, deployment, minimum=0, maximum=1, idle_seconds=2)
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError(WORKER_UNAVAILABLE) from exc


def retire_previous_options(deployment) -> None:
    """Scale down an option set that is not the stable one for this GPU."""
    deployed = deployment.deployed_model
    if deployed.inference_contract == "decision":
        return
    key = (max(int(deployment.max_replicas), 1), _window(deployment.idle_shutdown_seconds))
    if key == (_STABLE_MAX, _STABLE_WINDOW):
        return
    cls = _chat_cls(deployed, deployment.gpu_type)
    _scale(
        _variant(
            cls,
            deployment,
            maximum=deployment.max_replicas,
            idle_seconds=deployment.idle_shutdown_seconds,
        ),
        deployment,
        minimum=0,
        maximum=1,
        idle_seconds=2,
    )


def connect(deployment):
    """The container advance() already scaled. A request must not call update_autoscaler:
    that control-plane round trip is seconds, and the GPU is already warm."""
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
                    max_containers=max(deployment.max_replicas, 1),
                    scaledown_window=_window(deployment.idle_shutdown_seconds),
                )
            return cls(**parameters(deployment))
        cls = _variant(
            _chat_cls(deployed, deployment.gpu_type),
            deployment,
            maximum=_STABLE_MAX,
            idle_seconds=_STABLE_WINDOW,
        )
        return cls(**parameters(deployment))
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError(WORKER_UNAVAILABLE) from exc


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
                    scaledown_window=_window(idle_seconds),
                )
            return _scale(
                cls, deployment, minimum=minimum, maximum=maximum, idle_seconds=idle_seconds
            )
        cls = _chat_cls(deployed, deployment.gpu_type)
        return _scale(
            _variant(cls, deployment, maximum=_STABLE_MAX, idle_seconds=_STABLE_WINDOW),
            deployment,
            minimum=minimum,
            maximum=maximum,
            idle_seconds=idle_seconds,
        )
    except InputValidationError:
        raise
    except Exception as exc:
        raise InputValidationError(WORKER_UNAVAILABLE) from exc


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
        call = instance.infer.spawn(
            method="POST",
            path="/v1/chat/completions",
            body=json.dumps(
                {
                    "model": deployment.served_model_id,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 1,
                }
            ).encode(),
            headers={"content-type": "application/json"},
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


class ChatStreamError(Exception):
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _served_body(deployment, body: bytes, *, stream: bool) -> bytes:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if not isinstance(payload, dict):
        return body
    payload["model"] = deployment.served_model_id
    payload["stream"] = stream
    if stream:
        # Usage and per-request timing ride the last SSE chunk. The view strips both
        # unless the caller asked to see usage.
        payload["include_metrics"] = True
        options = payload.get("stream_options")
        if not isinstance(options, dict):
            options = {}
        options["include_usage"] = True
        payload["stream_options"] = options
    return json.dumps(payload).encode()


def _chat_kwargs(instance, deployment, body: bytes, *, stream: bool) -> tuple[object, dict]:
    method = instance.infer_stream if stream else instance.infer
    return method, {
        "method": "POST",
        "path": "/v1/chat/completions",
        "body": _served_body(deployment, body, stream=stream),
        "headers": {"content-type": "application/json"},
    }


def chat(instance, deployment, body: bytes) -> tuple[int, bytes, str]:
    method, kwargs = _chat_kwargs(instance, deployment, body, stream=False)
    result = method.remote(**kwargs)
    status = int(result.get("status") or 502)
    payload = result.get("body") or b""
    if isinstance(payload, str):
        payload = payload.encode()
    content_type = (result.get("headers") or {}).get("content-type") or "application/json"
    return status, payload, content_type


def iter_chat_stream(instance, deployment, body: bytes):
    method, kwargs = _chat_kwargs(instance, deployment, body, stream=True)
    frames = method.remote_gen(**kwargs)
    try:
        try:
            first = next(frames)
        except StopIteration as exc:
            raise InputValidationError(WORKER_UNAVAILABLE) from exc
        if not isinstance(first, dict):
            raise InputValidationError(WORKER_UNAVAILABLE)
        status = int(first.get("status") or 502)
        if status >= 400:
            rejected = bytearray()
            for chunk in frames:
                if isinstance(chunk, str):
                    chunk = chunk.encode()
                rejected.extend(chunk)
                if len(rejected) > 65536:
                    break
            raise ChatStreamError(status, bytes(rejected))
        for chunk in frames:
            if isinstance(chunk, str):
                chunk = chunk.encode()
            if chunk:
                yield chunk
    finally:
        close = getattr(frames, "close", None)
        if close is not None:
            close()


def predict(instance, decisions: list) -> dict:
    result = instance.predict.remote(decisions)
    if not isinstance(result, dict) or result.get("error_code"):
        raise InputValidationError(WORKER_UNAVAILABLE)
    return result


def minute_usd(rate: Decimal, replicas: int) -> Decimal:
    return (rate * Decimal(replicas)) / Decimal(60)
