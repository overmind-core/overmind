from unittest.mock import Mock

import pytest

modal = pytest.importorskip("modal")

from overbae.modal import modal_vllm_worker as workers  # noqa: E402


def make_worker():
    worker = workers._BaseVLLMWorker()
    worker.enable_lora = True
    worker._loaded_adapters = set()
    worker._proc = Mock()
    worker._warmup_chat = Mock()
    return worker


def test_snapshot_preserves_weights_without_checkpoint_reload(monkeypatch):
    worker = make_worker()
    post = Mock()
    health = Mock()
    monkeypatch.setattr(workers, "_vllm_post", post)
    monkeypatch.setattr(workers, "_wait_for_vllm", health)
    worker._snapshot_sleep()
    origin = worker._snapshot_origin
    worker._snapshot_wake()
    assert [call.args[0] for call in post.call_args_list] == ["/sleep?level=1", "/wake_up"]
    worker._warmup_chat.assert_called_once()
    health.assert_called_once_with(timeout=60, proc=worker._proc)
    assert worker._snapshot_origin == origin
    assert not getattr(worker, "_startup_error", None)


@pytest.mark.parametrize("failure", ["adapter", "warmup", "sleep", "wake"])
def test_failed_snapshot_cannot_serve(monkeypatch, failure):
    worker = make_worker()
    post = Mock()
    monkeypatch.setattr(workers, "_vllm_post", post)
    monkeypatch.setattr(workers.modal, "experimental", Mock(), raising=False)
    if failure == "adapter":
        worker._loaded_adapters.add("tenant-adapter")
    elif failure == "warmup":
        worker._warmup_chat.side_effect = RuntimeError("warmup failed")
    else:
        post.side_effect = RuntimeError(f"{failure} failed")
    if failure == "wake":
        worker._snapshot_wake()
    else:
        worker._snapshot_sleep()
    with pytest.raises(RuntimeError, match="snapshot failed"):
        worker._ensure_ready()


@pytest.mark.parametrize("gpu,image", sorted(workers.WORKER_LORA_CLS))
def test_every_lora_worker_uses_snapshots_and_preserves_plain_workers(gpu, image):
    lora = getattr(workers, workers.WORKER_LORA_CLS[gpu, image])._get_user_cls()
    plain = getattr(workers, workers.WORKER_CLS[gpu, image])._get_user_cls()
    assert issubclass(lora, workers._SnapVLLMWorker)
    assert issubclass(plain, workers._PlainVLLMWorker)


@pytest.mark.parametrize("gpu,image", sorted(workers.WORKER_LORA_CLS))
@pytest.mark.parametrize("snapshot", [True, False])
def test_snapshot_owns_compiled_files_and_host_weight_capacity(monkeypatch, gpu, image, snapshot):
    register = Mock(return_value=lambda cls: cls)
    monkeypatch.setattr(workers.app, "cls", register)
    monkeypatch.setattr(workers, "_worker_concurrency", lambda cls: cls)
    monkeypatch.setattr(workers, "test_snapshot_worker", None, raising=False)
    workers._register_worker("test_snapshot_worker", gpu, image, snapshot=snapshot)
    options = register.call_args.kwargs
    assert workers.WEIGHTS_MOUNT in options["volumes"]
    assert (workers.VLLM_CACHE_MOUNT in options["volumes"]) is not snapshot
    assert options["enable_memory_snapshot"] is snapshot
    if snapshot:
        assert options["memory"] == workers._SNAPSHOT_HOST_MEMORY_MIB[gpu]
        assert options["experimental_options"] == {"enable_gpu_snapshot": True}
