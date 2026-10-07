from __future__ import annotations

import uuid

import pytest
from factories import make_project

from modal_shared.shared import (
    GPU_TYPE_HEADER,
    MAX_MODEL_LEN_HEADER,
    SERVE_IMAGE_HEADER,
    WEIGHTS_PATH_HEADER,
    parse_routing_headers,
    resolve_inference_url,
    routing_headers,
    worker_cls_name,
)
from overbae.models import FinetuningJob
from overbae.models.inference import DeployedModel
from overbae.services.inference_client import InferenceClient

pytestmark = pytest.mark.django_db


def test_routing_headers_roundtrip():
    built = routing_headers(gpu_type="L40S", weights_path="/weights/ft-abc", max_model_len=16384)
    parsed = parse_routing_headers(built)
    assert parsed == {
        "gpu_type": "L40S",
        "weights_path": "/weights/ft-abc",
        "max_model_len": 16384,
        "serve_image": "vllm",
        "adapter_path": "",
        "lora_rank": 16,
    }


def test_routing_headers_carry_adapter_for_shared_base():
    built = routing_headers(
        gpu_type="L40S",
        weights_path="/weights/.base_models/Qwen--Qwen3.5-9B",
        max_model_len=4096,
        adapter_path="/weights/.adapters/ft-abc",
        lora_rank=32,
    )
    parsed = parse_routing_headers(built)
    assert parsed["weights_path"] == "/weights/.base_models/Qwen--Qwen3.5-9B"
    assert parsed["adapter_path"] == "/weights/.adapters/ft-abc"
    assert parsed["lora_rank"] == 32


def test_parse_routing_headers_case_insensitive():
    parsed = parse_routing_headers(
        {
            "x-overmind-gpu-type": "L4",
            "X-OVERMIND-WEIGHTS-PATH": "/weights/x",
            "X-Overmind-Max-Model-Len": "8192",
        }
    )
    assert parsed["gpu_type"] == "L4"
    assert parsed["max_model_len"] == 8192


def test_parse_routing_headers_missing_returns_none():
    assert parse_routing_headers({GPU_TYPE_HEADER: "L4"}) is None
    assert parse_routing_headers({GPU_TYPE_HEADER: "L4", WEIGHTS_PATH_HEADER: "/w"}) is None
    assert (
        parse_routing_headers(
            {
                GPU_TYPE_HEADER: "L4",
                WEIGHTS_PATH_HEADER: "/w",
                MAX_MODEL_LEN_HEADER: "nope",
            }
        )
        is None
    )


def test_resolve_inference_url_relative_path_and_query():
    url = resolve_inference_url(
        gpu_type="L4",
        model_path="/weights/ft-job/merged",
        model_name="ft-job",
        max_model_len=8192,
        environment="overmind-dev",
    )
    assert url.startswith(
        "https://overmind-overmind-dev--overmind-inference-l4-vllm-api.modal.run?"
    )
    assert "model_path=ft-job%2Fmerged" in url
    assert "model_name=ft-job" in url
    assert "max_model_len=8192" in url
    assert "/weights/" not in url.split("?", 1)[1]


GATEWAY = "https://gateway.example"
_COMPLETION = {
    "id": "cmpl",
    "object": "chat.completion",
    "created": 0,
    "model": "m",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


@pytest.fixture
def gateway(scripted):
    return scripted(GATEWAY).reply(json_body=_COMPLETION)


def _sent(gateway) -> dict:
    return gateway.calls[-1].headers


def _deployed(prefix: str, **fields) -> DeployedModel:
    return DeployedModel.objects.create(
        project=make_project(),
        model_id=f"{prefix}-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        **fields,
    )


def _chat(deployed=None, **routing):
    InferenceClient(base_url=GATEWAY, api_key="k").chat_completions(
        model_id=getattr(deployed, "model_id", "ft-abc"),
        messages=[{"role": "user", "content": "hi"}],
        deployed=deployed,
        **routing,
    )


def test_inference_client_posts_routing_headers(gateway):
    _chat(gpu_type="H200", weights_path="/weights/ft-abc", max_model_len=32768)
    headers = _sent(gateway)
    assert headers[GPU_TYPE_HEADER.lower()] == "H200"
    assert headers[WEIGHTS_PATH_HEADER.lower()] == "/weights/ft-abc"
    assert headers[MAX_MODEL_LEN_HEADER.lower()] == "32768"


def test_a_stored_gemma_e2b_l4_is_raised_to_l40s(gateway):
    deployed = _deployed(
        "ft-e2b",
        gpu_type="L4",
        weights_path="/weights/.base_models/unsloth--gemma-4-E2B-it",
        max_model_len=16384,
        base_model_id="google/gemma-4-E2B-it",
        is_lora=True,
        lora_rank=32,
    )
    _chat(deployed)
    assert _sent(gateway)[GPU_TYPE_HEADER.lower()] == "L40S"
    deployed.refresh_from_db()
    assert deployed.gpu_type == "L40S"


def test_inference_client_posts_routing_from_deployed(gateway):
    _chat(_deployed("ft-row", gpu_type="L4", weights_path="/weights/ft-row", max_model_len=4096))
    headers = _sent(gateway)
    assert headers[GPU_TYPE_HEADER.lower()] == "L4"
    assert headers[WEIGHTS_PATH_HEADER.lower()] == "/weights/ft-row"
    assert headers[MAX_MODEL_LEN_HEADER.lower()] == "4096"


def test_an_adapter_deployment_routes_to_its_adapter_on_the_shared_base(gateway):
    from modal_shared.shared import ADAPTER_PATH_HEADER, LORA_RANK_HEADER

    deployed = _deployed(
        "ft-ad",
        gpu_type="L40S",
        weights_path="/weights/.base_models/Qwen--Qwen3.5-9B",
        max_model_len=4096,
        is_lora=True,
        lora_rank=16,
    )
    DeployedModel.objects.filter(pk=deployed.pk).update(
        adapter_path=f"/weights/.adapters/{deployed.model_id}"
    )
    deployed.refresh_from_db()
    _chat(deployed)
    headers = _sent(gateway)
    assert headers[ADAPTER_PATH_HEADER.lower()] == f"/weights/.adapters/{deployed.model_id}"
    assert headers[LORA_RANK_HEADER.lower()] == "16"


def test_inference_client_omits_serve_image_header_for_muse(gateway):
    _chat(
        _deployed(
            "ft-muse",
            gpu_type="A100-80GB",
            weights_path="/weights/ft-muse",
            max_model_len=8192,
            base_model_id="unsloth/Muse-Glimmer-30B",
        )
    )
    assert SERVE_IMAGE_HEADER.lower() not in _sent(gateway)


def test_inference_client_delete_sends_weights_path(scripted):
    gateway = scripted(GATEWAY).reply(json_body={})
    deployed = _deployed("ft-del", weights_path="/weights/ft-del")
    InferenceClient(base_url=GATEWAY, api_key="k").delete_model(deployed.model_id)
    assert _sent(gateway)[WEIGHTS_PATH_HEADER.lower()] == "/weights/ft-del"
    deployed.refresh_from_db()
    assert deployed.status == DeployedModel.Status.DELETED


def test_deleting_an_adapter_leaves_the_shared_base_alone(scripted):
    gateway = scripted(GATEWAY).reply(json_body={})
    deployed = _deployed(
        "ft-ad", weights_path="/weights/.base_models/Qwen--Qwen3.5-9B", is_lora=True
    )
    adapter = f"/weights/.adapters/{deployed.model_id}"
    DeployedModel.objects.filter(pk=deployed.pk).update(adapter_path=adapter)
    InferenceClient(base_url=GATEWAY, api_key="k").delete_model(deployed.model_id)
    assert _sent(gateway)[WEIGHTS_PATH_HEADER.lower()] == adapter


@pytest.mark.parametrize("adapter", [False, True])
def test_an_evaluation_call_to_a_deployment_carries_its_routing(gateway, monkeypatch, adapter):
    from modal_shared.shared import ADAPTER_PATH_HEADER, LORA_RANK_HEADER
    from overbae.core.llms import ModelSpec, call_llm

    monkeypatch.setenv("INFERENCE_API_KEY", "k")
    deployed = _deployed(
        "ft-eval",
        gpu_type="L40S",
        weights_path="/weights/ft-eval",
        max_model_len=16384,
        is_lora=adapter,
        lora_rank=16,
    )
    if adapter:
        DeployedModel.objects.filter(pk=deployed.pk).update(adapter_path="/weights/.adapters/x")
    spec = ModelSpec(
        provider="custom",
        model_id=deployed.model_id,
        base_url=f"{GATEWAY}/v1",
        api_key_env="INFERENCE_API_KEY",
    )
    call_llm("hi", model_spec=spec)

    headers = _sent(gateway)
    assert headers[GPU_TYPE_HEADER.lower()] == "L40S"
    assert headers[WEIGHTS_PATH_HEADER.lower()] == "/weights/ft-eval"
    assert headers[MAX_MODEL_LEN_HEADER.lower()] == "16384"
    assert SERVE_IMAGE_HEADER.lower() not in headers
    if adapter:
        assert headers[ADAPTER_PATH_HEADER.lower()] == "/weights/.adapters/x"
        assert headers[LORA_RANK_HEADER.lower()] == "16"


def test_worker_cls_name_default():
    assert worker_cls_name("A100-80GB") == "A10080GB_vllm"
    with pytest.raises(ValueError, match="serve_image"):
        worker_cls_name("A100-80GB", "muse_glimmer")


def test_resolve_inference_url_vllm_class():
    url = resolve_inference_url(
        gpu_type="A100-80GB",
        model_path="/weights/ft-muse",
        model_name="ft-muse",
        max_model_len=8192,
        environment="overmind-dev",
    )
    assert "a10080gb-vllm" in url


@pytest.mark.parametrize(
    ("base_model", "expected"),
    [
        ("Qwen/Qwen3-1.7B", True),
        ("Qwen/Qwen3-Coder-30B-A3B-Instruct", True),
        ("nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B", False),
        ("openai/gpt-oss-20b", False),
    ],
)
def test_lora_routing_uses_verified_expert_support(base_model, expected):
    from overbae.services.deployment import serves_as_adapter

    job = FinetuningJob(
        provider="modal", base_model=base_model, hyperparameters={"training_type": {"type": "Lora"}}
    )
    assert serves_as_adapter(job) is expected


def test_full_finetune_takes_the_merge_path():
    from overbae.services.deployment import serves_as_adapter

    job = FinetuningJob(
        provider="modal",
        base_model="Qwen/Qwen3-1.7B",
        hyperparameters={"training_type": {"type": "Full"}},
    )
    assert serves_as_adapter(job) is False


def test_non_modal_provider_takes_the_merge_path():
    """Baseten and Nebius checkpoints come back through S3, not the sft Volume that
    publish_adapter reads from."""
    from overbae.services.deployment import serves_as_adapter

    job = FinetuningJob(
        provider="baseten",
        base_model="Qwen/Qwen3-1.7B",
        hyperparameters={"training_type": {"type": "Lora"}},
    )
    assert serves_as_adapter(job) is False
