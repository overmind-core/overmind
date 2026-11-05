"""A dedicated LoRA boots on its own worker and folds one adapter at startup."""

import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from overbae.modal import modal_vllm_worker as serving
from overbae.services import dedicated_runtime
from overbae.services.dedicated_runtime import (
    parameters,
    repair_forced_calls,
    shape_chat_payload,
    worker_class_name,
)

pytestmark = pytest.mark.covers("model-runtime")


def test_dedicated_lora_uses_the_plain_worker_and_no_runtime_adapter():
    deployed = SimpleNamespace(
        inference_contract="chat",
        weights_path="/weights/.base_models/Qwen--Qwen2.5-0.5B-Instruct",
        adapter_path="/weights/.adapters/ft-9be76bc2",
        is_lora=True,
        max_model_len=16384,
        lora_rank=16,
    )
    deployment = SimpleNamespace(
        id="dep",
        deployed_model=deployed,
        served_model_id="dedicated/ft-9be76bc2",
    )

    assert worker_class_name(deployed, "A100-80GB") == "A10080GB_vllm_dedicated"
    qwen = SimpleNamespace(
        inference_contract="chat",
        base_model_id="Qwen/Qwen3.5-4B",
        quantization="bf16",
    )
    assert worker_class_name(qwen, "H100") == "H100_sglang_dedicated"
    fp8 = SimpleNamespace(
        inference_contract="chat",
        base_model_id="Qwen/Qwen3.5-4B",
        quantization="fp8",
    )
    assert worker_class_name(fp8, "H100") == "H100_vllm_dedicated"
    assert parameters(deployment) == {
        "model_path": ".base_models/Qwen--Qwen2.5-0.5B-Instruct",
        "model_name": "dedicated/ft-9be76bc2",
        "max_model_len": 16384,
        "enable_lora": False,
        "max_lora_rank": 16,
        "adapter_path": ".adapters/ft-9be76bc2",
    }


def test_prod_snapshot_engine_enables_the_sleep_route(monkeypatch, tmp_path):
    obj = serving._BaseVLLMWorker()
    obj.model_path = "base"
    obj._journal = Mock()
    obj.model_name = "dedicated/ft"
    obj.max_model_len = 512
    obj.enable_lora = False
    obj.max_lora_rank = 16
    obj.gpu_type = "A100-80GB"
    obj.snapshot_engine = True
    (tmp_path / "base").mkdir()
    monkeypatch.setattr(serving, "WEIGHTS_MOUNT", str(tmp_path))
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr(serving, "IS_DEV", False)
    monkeypatch.setattr(serving.subprocess, "Popen", Mock())
    monkeypatch.setattr(serving, "_wait_for_vllm", Mock())
    slept = Mock()
    monkeypatch.setattr(serving, "_vllm_control", slept)
    monkeypatch.setenv("VLLM_SERVER_DEV_MODE", "0")

    obj._startup_inner(_json=json, _os=os)

    assert os.environ["VLLM_SERVER_DEV_MODE"] == "1"
    assert "--enable-sleep-mode" in obj._serve_command
    slept.assert_called_once_with("/sleep?level=1")


def test_dedicated_worker_is_not_the_serverless_class():
    discovery = importlib.import_module("modal._partial_function")
    flags = discovery._PartialFunctionFlags
    dedicated = serving.B200_vllm_dedicated._get_user_cls()
    serverless = serving.B200_vllm._get_user_cls()
    assert dedicated is not serverless
    assert "snapshot_engine" not in serverless.__annotations__
    assert "adapter_path" not in serverless.__annotations__
    assert dedicated.__annotations__["adapter_path"] is str
    assert dedicated.__annotations__["snapshot_engine"] is bool
    find = discovery._find_partial_methods_for_user_cls
    assert set(find(dedicated, flags.ENTER_PRE_SNAPSHOT)) == {"startup"}
    assert set(find(dedicated, flags.ENTER_POST_SNAPSHOT)) == {"wake"}
    sglang = serving.H100_sglang_dedicated._get_user_cls()
    assert sglang.engine == "sglang"
    assert "snapshot_engine" not in sglang.__annotations__
    assert "adapter_path" in sglang.__annotations__


def _decision():
    return SimpleNamespace(
        id="dep-1",
        gpu_type="A100-80GB",
        max_replicas=2,
        idle_shutdown_seconds=900,
        served_model_id="dedicated/dep-1",
        deployed_model=SimpleNamespace(
            inference_contract="decision",
            base_model_id="Cloudflare/clef",
            lora_rank=0,
            serving_runtime={"app": "overmind-decision-serve-old", "environment": "overmind-prod"},
            weights_path="/weights/.decisions/abc",
            checkpoint_hash="abc",
            max_model_len=4096,
        ),
    )


def test_dedicated_decision_binds_its_own_class(monkeypatch):
    seen = {}

    class Endpoint:
        def __init__(self, **kwargs):
            seen["params"] = kwargs

        def update_autoscaler(self, **kwargs):
            seen["scale"] = kwargs

    class Remote:
        def with_options(self, **kwargs):
            seen["options"] = kwargs
            return Endpoint

    def from_name(app_name, cls_name, *, environment_name):
        seen["lookup"] = (app_name, cls_name, environment_name)
        return Remote()

    monkeypatch.setenv("MODAL_ENVIRONMENT", "overmind-dev")
    monkeypatch.setattr(dedicated_runtime.modal.Cls, "from_name", from_name)
    monkeypatch.setattr(dedicated_runtime, "chat_sequence_cap", lambda deployment: None)
    dedicated_runtime.bind(_decision(), minimum=1, maximum=2, idle_seconds=900)
    assert seen["lookup"] == ("overmind-dedicated", "A10080GB_vllm_dedicated", "overmind-dev")
    assert seen["params"] == {
        "model_path": ".base_models/Cloudflare--clef",
        "model_name": "dedicated/dep-1",
        "max_model_len": 4096,
        "enable_lora": True,
        "max_lora_rank": 0,
        "adapter_path": ".decisions/abc",
        "snapshot_engine": False,
    }
    assert "gpu" not in seen["options"]
    assert seen["scale"]["min_containers"] == 1


def test_dedicated_chat_disables_thinking_and_keeps_parallel_tools():
    payload = {
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "chat_template_kwargs": {"enable_thinking": True},
        "max_tokens": 16,
    }
    shape_chat_payload(payload, base_model_id="unsloth/Muse-Glimmer-30B")
    assert payload["chat_template_kwargs"]["enable_thinking"] is False
    assert payload["chat_template_kwargs"]["thinking"] is False
    assert payload["chat_template_kwargs"]["reasoning_strength"] == "low"
    assert payload["include_reasoning"] is False
    assert payload["parallel_tool_calls"] is True
    assert payload["max_tokens"] == 48

    held = {
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "parallel_tool_calls": False,
        "tool_choice": "none",
    }
    shape_chat_payload(held, base_model_id="Qwen/Qwen3-4B")
    assert "tools" not in held
    assert "tool_choice" not in held
    assert held["chat_template_kwargs"] == {"enable_thinking": False, "thinking": False}

    oss = {"max_tokens": 32}
    shape_chat_payload(oss, base_model_id="openai/gpt-oss-20b")
    assert oss["include_reasoning"] is False
    assert oss["reasoning_effort"] == "low"
    assert "parallel_tool_calls" not in oss
    assert payload["messages"][0] == {
        "role": "system",
        "content": (
            "Put one atem:invoke for each item the user names, and separate assistant "
            "tool messages with <|eom|> rather than ending the turn after the first."
        ),
    }

    named = {
        "messages": [{"role": "user", "content": "What is the weather in Paris?"}],
        "tools": [
            {"type": "function", "function": {"name": "get_weather"}},
            {"type": "function", "function": {"name": "get_time"}},
        ],
        "tool_choice": {"type": "function", "function": {"name": "get_time"}},
    }
    shape_chat_payload(named, base_model_id="google/gemma-4-26B-A4B-it")
    assert [tool["function"]["name"] for tool in named["tools"]] == ["get_time"]
    assert named["parallel_tool_calls"] is False
    assert named["messages"][0]["content"] == "Call get_time."
    assert named["tool_choice"] == "required"
    assert "get_time" in named["structured_outputs"]["regex"]
    assert "get_weather" not in named["structured_outputs"]["regex"]

    required = {
        "messages": [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "tool_choice": "required",
    }
    shape_chat_payload(required, base_model_id="openai/gpt-oss-20b")
    assert required["parallel_tool_calls"] is False
    assert "structured_outputs" not in required
    assert required["messages"][0]["content"] == "Be brief.\nCall one of the available functions."

    kept = {
        "tools": [{"type": "function", "function": {"name": "get_weather"}}],
        "tool_choice": "required",
        "parallel_tool_calls": True,
    }
    shape_chat_payload(kept, base_model_id="openai/gpt-oss-20b")
    assert kept["parallel_tool_calls"] is True

    shaped = json.dumps(
        {
            "tool_choice": "required",
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_time",
                        "parameters": {
                            "type": "object",
                            "properties": {"timezone": {"type": "string"}},
                            "required": ["timezone"],
                        },
                    },
                }
            ],
        }
    ).encode()
    raw = json.dumps(
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": (
                            "<|channel|>commentary to=functions.get_time<|constrain|>json"
                            '<|message|>{"timezone":"UTC"}<|call|>'
                            "<|channel|>commentary to=functions.get_time<|constrain|>json"
                            '<|message|>{"result":"later"}<|call|>'
                        ),
                    },
                }
            ]
        }
    ).encode()
    repaired = json.loads(repair_forced_calls(raw, shaped))
    calls = repaired["choices"][0]["message"]["tool_calls"]
    assert [call["function"]["name"] for call in calls] == ["get_time"]
    assert json.loads(calls[0]["function"]["arguments"]) == {"timezone": "UTC"}

    quoted = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_0",
                                "type": "function",
                                "function": {
                                    "name": "get_time",
                                    "arguments": json.dumps({"timezone": '"Europe/Paris"'}),
                                },
                            }
                        ]
                    }
                }
            ]
        }
    ).encode()
    repaired = json.loads(repair_forced_calls(quoted, shaped))
    assert json.loads(
        repaired["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    ) == {"timezone": "Europe/Paris"}
