"""Binds one dedicated deployment to its own Modal parameter set."""

from __future__ import annotations

import inspect
import json
import logging
import os
import re
from decimal import Decimal

import httpx
import modal
from asgiref.sync import async_to_sync
from django.conf import settings

from modal_shared.serving.capacity import sequence_cap
from modal_shared.shared import rel_weights_path
from overbae.core.errors import InputValidationError
from overbae.modal.gpu_selector import gpu_vram_gb
from overbae.modal.model_registry import get_model_config_any_backend
from overbae.services.dedicated_offer import serving_shape

logger = logging.getLogger(__name__)

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


def worker_class_name(deployed, gpu_type: str) -> str:
    # Not `{gpu}_vllm`: that global is the serverless class, and Modal loads a class
    # with getattr(module, name).
    from modal_shared.serving.engine import engine_for

    token = gpu_type.replace("-", "")
    engine = "vllm"
    quant = getattr(deployed, "quantization", None) or "bf16"
    if getattr(deployed, "inference_contract", "") != "decision" and quant in ("bf16", "none", ""):
        engine = engine_for(getattr(deployed, "base_model_id", "") or "")
    return f"{token}_{engine}_dedicated"


def parameters(deployment) -> dict:
    deployed = deployment.deployed_model
    if deployed.inference_contract == "decision":
        base = (deployed.base_model_id or "").replace("/", "--")
        return {
            "model_path": f".base_models/{base}",
            "model_name": deployment.served_model_id,
            "max_model_len": deployed.max_model_len,
            "enable_lora": True,
            "max_lora_rank": int(deployed.lora_rank or 0),
            "adapter_path": rel_weights_path(deployed.weights_path),
            "snapshot_engine": False,
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
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        serving_runtime.release(deployment, gpu_type)
        return
    deployed = deployment.deployed_model
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
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        serving_runtime.retire_previous_options(deployment)
        return
    deployed = deployment.deployed_model
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
    try:
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
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        return serving_runtime.bind(
            deployment, minimum=minimum, maximum=maximum, idle_seconds=idle_seconds
        )
    deployed = deployment.deployed_model
    try:
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
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        return serving_runtime.spawn_probe(instance, deployment)
    deployed = deployment.deployed_model
    body = (
        {
            "model": deployment.served_model_id,
            "state": "Serving readiness check.",
            "questions": {
                "ready": {
                    "type": "choice",
                    "instructions": "Select an option.",
                    "criteria": {"A": "A", "B": "B"},
                }
            },
        }
        if deployed.inference_contract == "decision"
        else {
            "model": deployment.served_model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
    )
    call = instance.infer.spawn(
        method="POST",
        path="/v1/systemone"
        if deployed.inference_contract == "decision"
        else "/v1/chat/completions",
        body=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    return call.object_id


def probe_state(call_id: str) -> str:
    if str(call_id).startswith("sm-probe:"):
        from overbae.sagemaker import serving_runtime

        return serving_runtime.probe_state(call_id)
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
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        return serving_runtime.runner_count(instance, decision=decision)
    del decision
    try:
        stats_call = instance.infer.get_current_stats
        stats = _drive(stats_call.aio()) if hasattr(stats_call, "aio") else stats_call()
    except Exception:
        return None
    value = getattr(stats, "num_total_runners", None)
    return int(value) if value is not None else None


_HTTP: httpx.Client | None = None
_DIRECT: dict[tuple[str, str], str] = {}


def _http() -> httpx.Client:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(timeout=300)
    return _HTTP


def _tunnel(deployment) -> str:
    key = (str(deployment.id), deployment.gpu_type)
    cached = _DIRECT.get(key)
    if cached:
        return cached
    url = connect(deployment).direct_url.remote() or ""
    if url:
        _DIRECT[key] = url
    return url


def _post(deployment, body: bytes, *, stream: bool, payload: bytes | None = None) -> httpx.Response:
    base = _tunnel(deployment)
    if not base:
        raise InputValidationError(WORKER_UNAVAILABLE)
    if payload is None:
        payload = _served_body(deployment, body, stream=stream)
    return _http().send(
        _http().build_request(
            "POST",
            base.rstrip("/") + "/v1/chat/completions",
            content=payload,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {settings.INFERENCE_API_KEY}",
            },
        ),
        stream=stream,
    )


class ChatStreamError(Exception):
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _named_tool(choice) -> str:
    if not isinstance(choice, dict) or choice.get("type") != "function":
        return ""
    function = choice.get("function")
    name = function.get("name") if isinstance(function, dict) else ""
    return name if isinstance(name, str) else ""


def _pin_system(payload: dict, line: str) -> None:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        payload["messages"] = [{"role": "system", "content": line}]
        return
    if (
        messages
        and isinstance(messages[0], dict)
        and messages[0].get("role") in ("system", "developer")
    ):
        content = messages[0].get("content")
        if isinstance(content, str) and content:
            messages[0]["content"] = f"{content}\n{line}"
        elif isinstance(content, list):
            content.append({"type": "text", "text": line})
        else:
            messages[0]["content"] = line
        return
    messages.insert(0, {"role": "system", "content": line})


def _tool_names(tools, only: str) -> list[str]:
    if only:
        return [only]
    names = []
    for tool in tools:
        if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
            continue
        name = tool["function"].get("name")
        if isinstance(name, str) and name:
            names.append(name)
    return names


def _forced_tool_regex(spec_key: str, names: list[str]) -> str:
    alt = "|".join(re.escape(name) for name in names)
    if spec_key == "gemma4":
        # The gemma4 parser refuses JSON guided decoding, so the native call is the constraint.
        return rf"<\|tool_call>call:(?:{alt})\{{[^{{}}]*\}}<tool_call\|>"
    # Same skip in the Muse parser. The completion starts after `<|start|>assistant`.
    return (
        rf" to=(?:{alt})<\|message\|><atem:function_calls>\n"
        rf"<atem:invoke name=\"(?:{alt})\">\n"
        rf"(?:<atem:parameter name=\"[A-Za-z_][A-Za-z0-9_]*\">[^<]*</atem:parameter>\n)*"
        rf"</atem:invoke>\n</atem:function_calls><\|eot\|>"
    )


def shape_chat_payload(payload: dict, *, base_model_id: str) -> None:
    """Thinking stays off. A forced tool choice is a single call unless the request says otherwise."""
    from modal_shared.modelfam import resolve

    spec = resolve(base_model_id or "")
    kwargs = dict(payload.get("chat_template_kwargs") or {})
    for key, value in spec.default_chat_template_kwargs:
        kwargs.setdefault(key, value)
    kwargs["enable_thinking"] = False
    kwargs["thinking"] = False
    payload["chat_template_kwargs"] = kwargs
    if spec.suppress_reasoning_output and "include_reasoning" not in payload:
        payload["include_reasoning"] = False
    if spec.default_reasoning_effort and "reasoning_effort" not in payload:
        payload["reasoning_effort"] = spec.default_reasoning_effort
    max_tokens = payload.get("max_tokens")
    if (
        spec.min_max_tokens
        and isinstance(max_tokens, int)
        and not isinstance(max_tokens, bool)
        and max_tokens < spec.min_max_tokens
    ):
        payload["max_tokens"] = spec.min_max_tokens
    if payload.get("tool_choice") == "none":
        # The schemas stay in the prompt otherwise, and the model emits the call as text.
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload.pop("parallel_tool_calls", None)
        return
    tools = payload.get("tools")
    if not tools:
        return
    # Gemma 4 and Muse skip JSON guided decoding for these choices. gpt-oss emits
    # one call when parallel calls are off; the parser misses it, so the reply lifts it out.
    name = _named_tool(payload.get("tool_choice"))
    forced = payload.get("tool_choice") == "required" or bool(name)
    if name:
        chosen = [
            tool
            for tool in tools
            if isinstance(tool, dict)
            and isinstance(tool.get("function"), dict)
            and tool["function"].get("name") == name
        ]
        if chosen:
            payload["tools"] = chosen
            tools = chosen
            # A named choice plus a regex is rejected. required is allowed, and the
            # regex only matches this function.
            payload["tool_choice"] = "required"
    if "parallel_tool_calls" not in payload:
        payload["parallel_tool_calls"] = not forced
    if name:
        _pin_system(payload, f"Call {name}.")
    elif payload.get("tool_choice") == "required":
        _pin_system(payload, "Call one of the available functions.")
    elif spec.key == "muse_glimmer" and payload.get("parallel_tool_calls") is True:
        _pin_system(
            payload,
            "Put one atem:invoke for each item the user names, and separate assistant "
            "tool messages with <|eom|> rather than ending the turn after the first.",
        )
    if not forced or payload.get("structured_outputs"):
        return
    names = _tool_names(tools, name)
    if spec.key in ("gemma4", "muse_glimmer") and names:
        payload["structured_outputs"] = {"regex": _forced_tool_regex(spec.key, names)}


def _served_body(deployment, body: bytes, *, stream: bool) -> bytes:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    if not isinstance(payload, dict):
        return body
    payload["model"] = deployment.served_model_id
    payload["stream"] = stream
    shape_chat_payload(
        payload, base_model_id=getattr(deployment.deployed_model, "base_model_id", "") or ""
    )
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


_HARMONY_CALL = re.compile(
    r"<\|channel\|>commentary to=functions\.(?P<name>[A-Za-z0-9_.-]+)"
    r"<\|constrain\|>json<\|message\|>(?P<args>\{[^{}]*\})"
)


def _args_match(tool: dict, arguments: dict) -> bool:
    params = (tool.get("function") or {}).get("parameters") or {}
    if not isinstance(params, dict):
        return True
    required = params.get("required") or []
    properties = params.get("properties") or {}
    if any(not isinstance(key, str) or key not in arguments for key in required):
        return False
    return not properties or all(key in properties for key in arguments)


def repair_forced_calls(raw: bytes, shaped: bytes) -> bytes:
    """Keep the schema-valid tool-call prefix. gpt-oss leaves the call in content."""
    try:
        request = json.loads(shaped)
        body = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(request, dict) or not isinstance(body, dict):
        return raw
    if request.get("tool_choice") != "required":
        return raw
    tools = request.get("tools")
    choices = body.get("choices")
    if not isinstance(tools, list) or not choices or not isinstance(choices[0], dict):
        return raw
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return raw
    calls = list(message.get("tool_calls") or [])
    if not calls:
        content = message.get("content") or ""
        if isinstance(content, str):
            for index, match in enumerate(_HARMONY_CALL.finditer(content)):
                calls.append(
                    {
                        "id": f"call_{index}",
                        "type": "function",
                        "function": {"name": match.group("name"), "arguments": match.group("args")},
                    }
                )
            if calls:
                message["content"] = None
    kept = []
    by_name = {
        tool["function"]["name"]: tool
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            break
        tool = by_name.get(function.get("name"))
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            break
        if tool is None or not isinstance(arguments, dict) or not _args_match(tool, arguments):
            break
        function["arguments"] = json.dumps(
            {
                key: value[1:-1]
                if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"'
                else value
                for key, value in arguments.items()
            }
        )
        kept.append(call)
    if kept:
        message["tool_calls"] = kept
        choices[0]["finish_reason"] = "tool_calls"
    elif calls:
        message["tool_calls"] = None
    return json.dumps(body).encode()


def chat(deployment, body: bytes) -> tuple[int, bytes, str]:
    shaped = _served_body(deployment, body, stream=False)
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        status, raw, content_type = serving_runtime.invoke_chat(deployment, shaped)
        if status < 400 and "json" in content_type:
            raw = repair_forced_calls(raw, shaped)
        return status, raw, content_type
    response = _post(deployment, body, stream=False, payload=shaped)
    try:
        content_type = response.headers.get("content-type") or "application/json"
        raw = response.content
        if response.status_code < 400 and "json" in content_type:
            raw = repair_forced_calls(raw, shaped)
        return response.status_code, raw, content_type
    finally:
        response.close()


def iter_chat_stream(deployment, body: bytes):
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        shaped = _served_body(deployment, body, stream=True)
        yield from serving_runtime.iter_chat_stream(deployment, shaped)
        return
    response = _post(deployment, body, stream=True)
    try:
        if response.status_code >= 400:
            rejected = response.read()
            logger.warning("dedicated chat upstream %s %s", response.status_code, rejected[:300])
            raise ChatStreamError(response.status_code, rejected)
        for chunk in response.iter_bytes():
            if chunk:
                yield chunk
    finally:
        response.close()


def post_systemone(deployment, body: dict) -> dict:
    if getattr(settings, "FINETUNING_BACKEND", "modal") == "sagemaker":
        from overbae.sagemaker import serving_runtime

        return serving_runtime.post_systemone(deployment, body)
    base = _tunnel(deployment)
    if not base:
        raise InputValidationError(WORKER_UNAVAILABLE)
    response = _http().post(
        base.rstrip("/") + "/v1/systemone",
        json=body,
        headers={"authorization": f"Bearer {settings.INFERENCE_API_KEY}"},
        timeout=300,
    )
    if response.status_code >= 400:
        raise InputValidationError(response.text[:500] or WORKER_UNAVAILABLE)
    return response.json()


def minute_usd(rate: Decimal, replicas: int) -> Decimal:
    return (rate * Decimal(replicas)) / Decimal(60)
