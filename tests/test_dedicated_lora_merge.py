"""A dedicated LoRA boots on its own worker and folds one adapter at startup."""

import importlib
from types import SimpleNamespace

from overbae.modal import modal_vllm_worker as serving
from overbae.services.dedicated_runtime import chat, parameters, worker_class_name


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
