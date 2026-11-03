"""A dedicated LoRA boots on its own worker and folds one adapter at startup."""

import importlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from overbae.modal import modal_vllm_worker as serving
from overbae.services import dedicated_runtime
from overbae.services.dedicated_runtime import chat, parameters, worker_class_name

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
    assert parameters(deployment) == {
        "model_path": ".base_models/Qwen--Qwen2.5-0.5B-Instruct",
        "model_name": "dedicated/ft-9be76bc2",
        "max_model_len": 16384,
        "enable_lora": False,
        "max_lora_rank": 16,
        "adapter_path": ".adapters/ft-9be76bc2",
    }
    sent = {}

    def remote(**kwargs):
        sent.update(kwargs)
        return {"status": 200, "body": b"{}", "headers": {}}

    chat(SimpleNamespace(infer=SimpleNamespace(remote=remote)), deployment, b"{}")
    assert "adapter" not in sent


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
    monkeypatch.setattr(serving, "IS_PROD", True)
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


def _decision(app="overmind-decision-serve-old"):
    return SimpleNamespace(
        id="dep-1",
        gpu_type="A100-80GB",
        max_replicas=1,
        idle_shutdown_seconds=900,
        deployed_model=SimpleNamespace(
            inference_contract="decision",
            serving_runtime={"app": app, "environment": "overmind-prod"},
            weights_path="/weights/.decisions/abc",
            checkpoint_hash="abc",
            max_model_len=4096,
        ),
    )


def test_older_decision_release_scales_without_pool(monkeypatch):
    dedicated_runtime._unpooled_apps.clear()
    seen = []

    class Endpoint:
        def __init__(self, **kwargs):
            seen.append(kwargs)
            self.kwargs = kwargs

        def update_autoscaler(self, **_kwargs):
            if "pool" in self.kwargs:
                raise RuntimeError(
                    "parameter names were provided but are not defined "
                    "class modal.parameters for the class: pool"
                )

    class Remote:
        def with_options(self, **_kwargs):
            return Endpoint

    monkeypatch.setattr(dedicated_runtime.modal.Cls, "from_name", lambda *_a, **_k: Remote())
    dedicated_runtime.bind(_decision(), minimum=1, maximum=1, idle_seconds=900)
    assert "pool" in seen[0]
    assert "pool" not in seen[1]
    dedicated_runtime._unpooled_apps.clear()


def test_decision_predict_retries_when_the_release_rejects_pool(monkeypatch):
    dedicated_runtime._unpooled_apps.clear()
    deployment = _decision("overmind-decision-serve-other")
    calls = []

    class Instance:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

            class Predict:
                def remote(self, _decisions):
                    calls.append(kwargs)
                    if "pool" in kwargs:
                        raise RuntimeError(
                            "parameter names were provided but are not defined "
                            "class modal.parameters for the class: pool"
                        )
                    return {"predictions": [{"key": "0"}]}

            self.predict = Predict()

    monkeypatch.setattr(
        dedicated_runtime,
        "connect",
        lambda row: Instance(**dedicated_runtime.parameters(row)),
    )
    result = dedicated_runtime.predict(Instance(**parameters(deployment)), [], deployment)
    assert result["predictions"] == [{"key": "0"}]
    assert "pool" in calls[0]
    assert "pool" not in calls[1]
    dedicated_runtime._unpooled_apps.clear()
