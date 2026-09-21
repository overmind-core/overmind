from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

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
from overbae.models import Project
from overbae.models.inference import DeployedModel
from overbae.services.inference_client import InferenceClient

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


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


def test_inference_client_posts_routing_headers():
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {"id": "cmpl"}
    with patch("overbae.services.inference_client.requests.post", return_value=fake) as post:
        client.chat_completions(
            model_id="ft-abc",
            messages=[{"role": "user", "content": "hi"}],
            gpu_type="H200",
            weights_path="/weights/ft-abc",
            max_model_len=32768,
        )
    headers = post.call_args.kwargs["headers"]
    assert headers[GPU_TYPE_HEADER] == "H200"
    assert headers[WEIGHTS_PATH_HEADER] == "/weights/ft-abc"
    assert headers[MAX_MODEL_LEN_HEADER] == "32768"


def test_inference_client_posts_routing_from_deployed():
    deployed = DeployedModel.objects.create(
        project=_project(),
        model_id=f"ft-from-row-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        gpu_type="L4",
        weights_path="/weights/ft-from-row",
        max_model_len=4096,
    )
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {"id": "cmpl"}
    with patch("overbae.services.inference_client.requests.post", return_value=fake) as post:
        client.chat_completions(
            model_id=deployed.model_id,
            messages=[{"role": "user", "content": "hi"}],
            deployed=deployed,
        )
    headers = post.call_args.kwargs["headers"]
    assert headers[GPU_TYPE_HEADER] == "L4"
    assert headers[WEIGHTS_PATH_HEADER] == "/weights/ft-from-row"
    assert headers[MAX_MODEL_LEN_HEADER] == "4096"


def test_inference_client_delete_sends_weights_path():
    mid = f"ft-del-{uuid.uuid4().hex[:8]}"
    DeployedModel.objects.create(
        project=_project(),
        model_id=mid,
        status=DeployedModel.Status.READY,
        weights_path="/weights/ft-del",
    )
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    fake = MagicMock()
    fake.ok = True
    fake.status_code = 200
    with patch("overbae.services.inference_client.requests.delete", return_value=fake) as delete:
        client.delete_model(mid)
    assert delete.call_args.kwargs["headers"][WEIGHTS_PATH_HEADER] == "/weights/ft-del"
    row = DeployedModel.objects.get(model_id=mid)
    assert row.status == DeployedModel.Status.DELETED


def test_deleting_an_adapter_leaves_the_shared_base_alone():
    """weights_path is a base shared with every other adapter on that model."""
    mid = f"ft-ad-{uuid.uuid4().hex[:8]}"
    DeployedModel.objects.create(
        project=_project(),
        model_id=mid,
        status=DeployedModel.Status.READY,
        weights_path="/weights/.base_models/Qwen--Qwen3.5-9B",
        adapter_path=f"/weights/.adapters/{mid}",
        is_lora=True,
    )
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    fake = MagicMock()
    fake.ok = True
    fake.status_code = 200
    with patch("overbae.services.inference_client.requests.delete", return_value=fake) as delete:
        client.delete_model(mid)
    assert delete.call_args.kwargs["headers"][WEIGHTS_PATH_HEADER] == f"/weights/.adapters/{mid}"


def test_adapter_deployment_sends_adapter_routing_headers():
    from modal_shared.shared import ADAPTER_PATH_HEADER, LORA_RANK_HEADER

    mid = f"ft-ad2-{uuid.uuid4().hex[:8]}"
    deployed = DeployedModel.objects.create(
        project=_project(),
        model_id=mid,
        status=DeployedModel.Status.READY,
        gpu_type="L40S",
        weights_path="/weights/.base_models/Qwen--Qwen3.5-9B",
        adapter_path=f"/weights/.adapters/{mid}",
        max_model_len=4096,
        is_lora=True,
        lora_rank=16,
    )
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    headers = client._headers_for(deployed)
    assert headers[ADAPTER_PATH_HEADER] == f"/weights/.adapters/{mid}"
    assert headers[LORA_RANK_HEADER] == "16"


def test_call_llm_attaches_routing_headers_for_inference_gateway():
    from overbae.core.llms import ModelSpec, _model_spec_client_and_name

    deployed = DeployedModel.objects.create(
        project=_project(),
        model_id=f"ft-eval-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        gpu_type="L40S",
        weights_path="/weights/ft-eval",
        max_model_len=16384,
    )
    spec = ModelSpec(
        provider="custom",
        model_id=deployed.model_id,
        base_url="https://gateway.example/v1",
        api_key_env="INFERENCE_API_KEY",
    )
    client = MagicMock()
    with (
        patch.dict("os.environ", {"INFERENCE_API_KEY": "k"}),
        patch("overbae.core.llms._openai_compatible_client", return_value=client) as factory,
    ):
        _model_spec_client_and_name(spec)
    header_items = dict(factory.call_args.args[3])
    assert header_items[GPU_TYPE_HEADER] == "L40S"
    assert header_items[WEIGHTS_PATH_HEADER] == "/weights/ft-eval"
    assert header_items[MAX_MODEL_LEN_HEADER] == "16384"
    assert SERVE_IMAGE_HEADER not in header_items


def test_call_llm_attaches_adapter_routing_headers_for_lora_deploy():
    from modal_shared.shared import ADAPTER_PATH_HEADER, LORA_RANK_HEADER
    from overbae.core.llms import ModelSpec, _model_spec_client_and_name

    mid = f"ft-lora-{uuid.uuid4().hex[:8]}"
    deployed = DeployedModel.objects.create(
        project=_project(),
        model_id=mid,
        status=DeployedModel.Status.READY,
        gpu_type="L40S",
        weights_path="/weights/.base_models/Qwen--Qwen3.5-9B",
        adapter_path=f"/weights/.adapters/{mid}",
        max_model_len=16384,
        is_lora=True,
        lora_rank=16,
    )
    spec = ModelSpec(
        provider="custom",
        model_id=deployed.model_id,
        base_url="https://gateway.example/v1",
        api_key_env="INFERENCE_API_KEY",
    )
    client = MagicMock()
    with (
        patch.dict("os.environ", {"INFERENCE_API_KEY": "k"}),
        patch("overbae.core.llms._openai_compatible_client", return_value=client) as factory,
    ):
        _model_spec_client_and_name(spec)
    header_items = dict(factory.call_args.args[3])
    assert header_items[ADAPTER_PATH_HEADER] == f"/weights/.adapters/{mid}"
    assert header_items[LORA_RANK_HEADER] == "16"


def test_worker_cls_name_default_and_muse():
    assert worker_cls_name("A100-80GB") == "A10080GB_vllm"
    assert worker_cls_name("A100-80GB", "muse_glimmer") == "A10080GB_muse_glimmer"
    assert worker_cls_name("H200", "muse_glimmer") == "H200_muse_glimmer"
    with pytest.raises(ValueError, match="serve_image"):
        worker_cls_name("L4", "muse_glimmer")
    with pytest.raises(ValueError, match="serve_image"):
        worker_cls_name("B300", "muse_glimmer")


def test_resolve_inference_url_muse_class():
    url = resolve_inference_url(
        gpu_type="A100-80GB",
        model_path="/weights/ft-muse",
        model_name="ft-muse",
        max_model_len=8192,
        environment="overmind-dev",
        serve_image="muse_glimmer",
    )
    assert "a10080gb-muse-glimmer" in url


def test_inference_client_sends_muse_serve_image_header():
    deployed = DeployedModel.objects.create(
        project=_project(),
        model_id=f"ft-muse-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        gpu_type="A100-80GB",
        weights_path="/weights/ft-muse",
        max_model_len=8192,
        base_model_id="unsloth/Muse-Glimmer-30B",
    )
    client = InferenceClient(base_url="https://gateway.example", api_key="k")
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {"id": "cmpl"}
    with patch("overbae.services.inference_client.requests.post", return_value=fake) as post:
        client.chat_completions(
            model_id=deployed.model_id,
            messages=[{"role": "user", "content": "hi"}],
            deployed=deployed,
        )
    headers = post.call_args.kwargs["headers"]
    assert headers[SERVE_IMAGE_HEADER] == "muse_glimmer"


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
    from overbae.tasks.model_deployment import _serves_as_adapter

    job = MagicMock()
    job.provider = "modal"
    job.base_model = base_model
    job.hyperparameters = {"training_type": {"type": "Lora"}}
    assert _serves_as_adapter(job) is expected


def test_full_finetune_takes_the_merge_path():
    from overbae.tasks.model_deployment import _serves_as_adapter

    job = MagicMock()
    job.provider = "modal"
    job.base_model = "Qwen/Qwen3-1.7B"
    job.hyperparameters = {"training_type": {"type": "Full"}}
    assert _serves_as_adapter(job) is False


def test_non_modal_provider_takes_the_merge_path():
    """Baseten and Nebius checkpoints come back through S3, not the sft Volume that
    publish_adapter reads from."""
    from overbae.tasks.model_deployment import _serves_as_adapter

    job = MagicMock()
    job.provider = "baseten"
    job.base_model = "Qwen/Qwen3-1.7B"
    job.hyperparameters = {"training_type": {"type": "Lora"}}
    assert _serves_as_adapter(job) is False
