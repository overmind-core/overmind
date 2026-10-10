import importlib
import json
import os
import shutil
from pathlib import Path
from unittest.mock import Mock

import pytest

from modal_shared.stacks import WORKER_ALLOWED, worker_cls_name
from overbae.modal import modal_vllm_worker as serving


def test_shared_worker_resolves_family_from_sealed_base(monkeypatch, tmp_path):
    obj = serving._BaseVLLMWorker()
    obj.model_path = "base"
    obj.model_name = "opaque-deployment-id"
    obj.max_model_len = 512
    obj.enable_lora = True
    obj.max_lora_rank = 16
    (tmp_path / "base").mkdir()
    monkeypatch.setattr(serving, "WEIGHTS_MOUNT", str(tmp_path))
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr(serving, "vllm_cache_vol", Mock())
    monkeypatch.setattr(
        serving,
        "read_base_manifest",
        Mock(return_value={"repo": "Qwen/Qwen3-Coder-30B-A3B-Instruct"}),
    )
    monkeypatch.setattr(serving.subprocess, "Popen", Mock())
    monkeypatch.setattr(serving, "_wait_for_vllm", Mock())
    monkeypatch.setenv("VLLM_ALLOW_RUNTIME_LORA_UPDATING", "0")
    monkeypatch.setenv("VLLM_SERVER_DEV_MODE", "0")
    obj._startup_inner(_json=json, _os=os)
    assert "--enable-mixed-moe-lora-format" in obj._serve_command
    assert obj._serve_command[obj._serve_command.index("--tool-call-parser") + 1] == "qwen3_coder"


@pytest.mark.parametrize("gpu,image", sorted(WORKER_ALLOWED))
def test_every_gpu_image_has_a_separate_shared_base_worker(gpu, image):
    regular = getattr(serving, worker_cls_name(gpu, image))._get_user_cls()
    shared = getattr(serving, worker_cls_name(gpu, image, enable_lora=True))._get_user_cls()
    assert issubclass(shared, serving._SharedBaseVLLMWorker)
    assert not regular.snapshot_weights
    assert shared.snapshot_weights
    assert shared.__annotations__["base_identity"] is str
    assert set(regular.__annotations__) < set(shared.__annotations__)


@pytest.mark.parametrize("gpu,image", sorted(WORKER_ALLOWED))
def test_modal_discovers_only_the_intended_lifecycle_hooks(gpu, image):
    discovery = importlib.import_module("modal._partial_function")
    flags = discovery._PartialFunctionFlags
    regular = getattr(serving, worker_cls_name(gpu, image))._get_user_cls()
    shared = getattr(serving, worker_cls_name(gpu, image, enable_lora=True))._get_user_cls()
    find = discovery._find_partial_methods_for_user_cls
    assert set(find(regular, flags.ENTER_POST_SNAPSHOT)) == {"startup"}
    assert find(regular, flags.ENTER_PRE_SNAPSHOT) == {}
    assert set(find(shared, flags.ENTER_PRE_SNAPSHOT)) == {"startup"}
    assert set(find(shared, flags.ENTER_POST_SNAPSHOT)) == {"restore"}


def worker():
    obj = serving._SharedBaseVLLMWorker()
    obj.model_name = "base--test"
    obj.model_path = ".base_models/org--base"
    obj.base_identity = "base-identity"
    obj._startup_error = None
    obj._verify_base_identity = Mock()
    obj._start = Mock()
    obj._serve_command = ["vllm", "serve", "base"]
    obj._proc = Mock(poll=Mock(return_value=None))
    return obj


def test_capture_prepares_only_the_base_before_level_two_sleep(monkeypatch):
    obj = worker()
    calls = []
    obj._base_rpc = Mock(side_effect=lambda *args: calls.append(args) or {"reused": True})
    obj._control = Mock(side_effect=lambda path: calls.append(path))
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr(serving, "artifacts_vol", Mock(commit=lambda: calls.append("commit")))
    serving._SharedBaseVLLMWorker.startup._get_raw_f()(obj)
    assert obj._startup_error is None
    assert calls == [
        ("prepare_shared_base", serving.ARTIFACTS_MOUNT, "base-identity", obj._serve_command),
        "commit",
        "/sleep?level=2",
    ]
    assert obj._snapshot_origin


def test_failed_capture_records_error_without_enter_retry(monkeypatch):
    obj = worker()
    obj._verify_base_identity.side_effect = ValueError("base changed")
    monkeypatch.setattr(serving, "weights_vol", Mock())
    serving._SharedBaseVLLMWorker.startup._get_raw_f()(obj)
    assert "base changed" in obj._startup_error
    obj._start.assert_not_called()
    obj._proc.kill.assert_called_once()


def test_restore_reloads_before_kv_wake_and_clears_tenant_state(monkeypatch):
    obj = worker()
    obj._snapshot_origin = "original"
    obj._loaded_adapters = {"must-not-survive"}
    calls = []
    obj._control = Mock(side_effect=lambda path: calls.append(path))
    obj._base_rpc = Mock(side_effect=lambda method: calls.append(method) or {"reload_s": 1})
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr(serving, "artifacts_vol", Mock(reload=lambda: calls.append("refresh")))
    monkeypatch.setattr(
        serving, "_wait_for_vllm", Mock(side_effect=lambda **kw: calls.append("health"))
    )
    serving._SharedBaseVLLMWorker.restore._get_raw_f()(obj)
    assert calls == [
        "refresh",
        "/wake_up?tags=weights",
        "restore_shared_base",
        "/wake_up?tags=kv_cache",
        "health",
    ]
    assert obj._loaded_adapters == set()
    assert obj._adapter_lock is None
    assert obj._runtime != obj._snapshot_origin
    assert obj._restore_metrics["ready_at"] > 0


def test_restore_failure_never_reports_ready(monkeypatch):
    obj = worker()
    obj._base_rpc = Mock(side_effect=RuntimeError("checksum mismatch"))
    obj._control = Mock()
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr(serving, "artifacts_vol", Mock())
    health = Mock()
    monkeypatch.setattr(serving, "_wait_for_vllm", health)
    serving._SharedBaseVLLMWorker.restore._get_raw_f()(obj)
    assert "checksum mismatch" in obj._startup_error
    assert "ready_at" not in obj._restore_metrics
    health.assert_not_called()
    obj._control.assert_called_once_with("/wake_up?tags=weights")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/collective_rpc", "/sleep", "/v1/../collective_rpc", "/v1/load_lora_adapter"]
)
async def test_control_endpoints_are_not_proxied(path):
    obj = worker()
    with pytest.raises(ValueError, match="endpoints"):
        await serving._BaseVLLMWorker.infer._get_raw_f()(
            obj, method="POST", path=path, body=b"{}", headers={}
        )


def test_shared_pool_uses_base_identity_not_adapter_identity(monkeypatch):
    cls = Mock()
    lookup = Mock(return_value=cls)
    vol = Mock()
    vol.read_file.return_value = [json.dumps({"identity": "immutable"}).encode()]
    monkeypatch.setattr(serving.modal.Cls, "from_name", lookup)
    monkeypatch.setattr(serving, "weights_vol", vol)
    serving._base_identities.pop(".base_models/base", None)
    serving._make_worker(
        gpu_type="H200",
        model_path=".base_models/base",
        model_name="base",
        max_model_len=32768,
        enable_lora=True,
    )
    vol.reload.assert_not_called()
    vol.read_file.assert_called_once_with(".base_models/base/.base-manifest.json")
    assert lookup.call_args.args == (serving.APP_NAME, "H200_vllm_lora")
    assert cls.call_args.kwargs["base_identity"] == "immutable"
    assert "adapter" not in cls.call_args.kwargs
    serving._make_worker(
        gpu_type="H200",
        model_path=".base_models/base",
        model_name="base",
        max_model_len=32768,
        enable_lora=True,
    )
    vol.read_file.assert_called_once()


def test_gateway_picks_serve_image_without_reloading(monkeypatch):
    vol = Mock()
    monkeypatch.setattr(serving, "weights_vol", vol)
    assert serving._serve_image_for(hinted="") == "vllm"
    assert serving._serve_image_for(hinted="vllm") == "vllm"
    vol.reload.assert_not_called()
    vol.read_file.assert_not_called()


@pytest.mark.asyncio
async def test_gateway_reads_base_identity_without_reloading(monkeypatch):
    async def chunks(_path):
        yield json.dumps({"identity": "immutable"}).encode()

    vol = Mock()
    vol.read_file.aio = chunks
    monkeypatch.setattr(serving, "weights_vol", vol)
    serving._base_identities.pop(".base_models/fresh", None)
    assert await serving._base_identity_aio(".base_models/fresh") == "immutable"
    vol.reload.assert_not_called()
    assert await serving._base_identity_aio(".base_models/fresh") == "immutable"


class _AdapterClient:
    def __init__(self, timeout):
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def post(self, url, json):
        _AdapterClient.posts.append((url, json))
        return Mock(status_code=200, text="")


@pytest.mark.asyncio
async def test_adapter_load_leaves_the_weights_mount_closed(monkeypatch, tmp_path):
    mount = tmp_path / "weights"
    posts = []
    _AdapterClient.posts = posts

    def write_adapter(cache_key: str, payload: bytes) -> str:
        adapter = mount / ".adapters" / cache_key
        adapter.mkdir(parents=True)
        (adapter / "adapter_config.json").write_text(json.dumps({"r": 8}))
        (adapter / "adapter_model.safetensors").write_bytes(payload)
        return f".adapters/{cache_key}"

    first = write_adapter("ft-first", b"first-weights")
    second = write_adapter("ft-second", b"second-weights")
    monkeypatch.setattr(serving, "WEIGHTS_MOUNT", str(mount))
    monkeypatch.setattr(serving, "weights_vol", Mock())
    monkeypatch.setattr("httpx.AsyncClient", _AdapterClient)
    obj = serving._BaseVLLMWorker()
    obj._loaded_adapters = set()
    obj._adapter_lock = None

    try:
        await obj._ensure_adapter("first", first)
        await obj._ensure_adapter("second", second)

        assert serving.weights_vol.reload.call_count == 2
        loaded = [body["lora_path"] for _url, body in posts]
        assert len(loaded) == 2
        for path, payload in zip(loaded, (b"first-weights", b"second-weights"), strict=True):
            staged = Path(path)
            assert not staged.is_relative_to(mount)
            assert (staged / "adapter_model.safetensors").read_bytes() == payload
    finally:
        shutil.rmtree("/tmp/overmind-adapters", ignore_errors=True)
