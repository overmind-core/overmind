from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from overbae.models import DeployedModel
from overbae.services import inference_live


@pytest.fixture
def worker_status(monkeypatch):
    store = {}
    cache = MagicMock()
    cache.get.side_effect = store.get
    cache.set.side_effect = lambda key, value, timeout: store.__setitem__(key, value)
    monkeypatch.setattr(inference_live, "cache", cache)
    monkeypatch.setattr(inference_live, "has_recent_inference", lambda *args: False)
    monkeypatch.setattr(inference_live, "modal_environment", lambda: "status-test")
    stats = SimpleNamespace(backlog=0, num_running_inputs=0, num_total_runners=0)
    read_stats = AsyncMock(return_value=stats)
    worker = SimpleNamespace(
        infer=SimpleNamespace(get_current_stats=SimpleNamespace(aio=read_stats))
    )
    worker_class = MagicMock(return_value=worker)
    monkeypatch.setattr(inference_live.modal.Cls, "from_name", MagicMock(return_value=worker_class))
    return store, cache, read_stats, worker_class


def deployment(**kwargs):
    return DeployedModel(
        model_id="ft-status-test",
        status="ready",
        base_model_id="Qwen/Qwen3.5-27B",
        gpu_type="A100-80GB",
        weights_path="/weights/full-checkpoint",
        max_model_len=16384,
        **kwargs,
    )


def test_worker_measurements_refresh_after_cache_expires(worker_status):
    store, cache, read_stats, _ = worker_status
    model = deployment()
    assert inference_live.live_worker_stats(model)["num_total_runners"] == 0
    read_stats.return_value.num_total_runners = 1
    assert inference_live.live_worker_stats(model)["num_total_runners"] == 0
    store.clear()
    assert inference_live.live_worker_stats(model)["num_total_runners"] == 1
    assert read_stats.await_count == 2
    assert all(call.kwargs["timeout"] <= 5 for call in cache.set.call_args_list)


def test_recent_activity_is_not_frozen_with_cached_worker_counts(worker_status, monkeypatch):
    model = deployment()
    assert inference_live.live_worker_stats(model)["recently_active"] is False
    monkeypatch.setattr(inference_live, "has_recent_inference", lambda *args: True)
    assert inference_live.live_worker_stats(model)["recently_active"] is True
    assert worker_status[2].await_count == 1


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("provider unavailable")])
def test_unavailable_worker_measurements_are_not_reported_as_zero(worker_status, error):
    worker_status[2].side_effect = error
    data = inference_live.live_worker_stats(deployment())
    assert data["available"] is False
    assert data["num_total_runners"] is None
    assert data["backlog"] is None


def test_full_checkpoint_stats_bind_the_deployment_pool(worker_status):
    result = inference_live.live_worker_stats(deployment(lora_rank=32))
    assert result["available"] is True
    worker_status[3].assert_called_once_with(
        model_path="full-checkpoint",
        model_name="ft-status-test",
        max_model_len=16384,
        enable_lora=False,
        max_lora_rank=16,
    )


def test_lora_stats_bind_the_sealed_base_pool(worker_status, monkeypatch):
    async def read_manifest(path):
        assert path == ".base_models/qwen/.base-manifest.json"
        yield b'{"identity":"sealed-base-digest"}'

    volume = SimpleNamespace(read_file=SimpleNamespace(aio=read_manifest))
    monkeypatch.setattr(inference_live.modal.Volume, "from_name", MagicMock(return_value=volume))
    model = deployment(adapter_path="/weights/.adapters/tenant-model", lora_rank=32)
    model.weights_path = "/weights/.base_models/qwen"
    result = inference_live.live_worker_stats(model)
    assert result["available"] is True
    worker_status[3].assert_called_once_with(
        model_path=".base_models/qwen",
        model_name="base--.base_models-qwen",
        max_model_len=16384,
        enable_lora=True,
        max_lora_rank=32,
        base_identity="sealed-base-digest",
    )


def test_worker_cache_separates_context_profiles(worker_status):
    model = deployment()
    inference_live.live_worker_stats(model)
    model.max_model_len = 4096
    inference_live.live_worker_stats(model)
    assert worker_status[2].await_count == 2
