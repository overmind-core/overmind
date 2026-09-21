import io
import json
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import sglang_snapshot_lab as lab


@pytest.mark.parametrize("base", ["dense-base", "moe-base"])
def test_command_uses_shared_bf16_base_and_cpu_backup_without_tenant_adapters(base):
    args = lab.command({"base_path": base, "max_model_len": 32768, "rank": 16})
    assert args[args.index("--model-path") + 1] == "/weights/" + base
    assert args[args.index("--dtype") + 1] == "bfloat16"
    assert args[args.index("--lora-target-modules") + 1] == "all"
    assert args[args.index("--load-format") + 1] == "safetensors"
    assert "--enable-memory-saver" in args
    assert "--enable-weights-cpu-backup" in args
    assert "--lora-strict-loading" in args
    assert "--lora-paths" not in args


@pytest.mark.parametrize("cls", [lab.H200Worker, lab.B200Worker])
def test_workers_declare_snapshot_parameters(cls):
    assert cls._get_user_cls().__annotations__ == {"config_json": str, "mode": str}


@pytest.mark.parametrize("tenant_config", [False, True])
def test_startup_captures_only_a_warmed_base(monkeypatch, tenant_config):
    worker = lab.SGLangWorker()
    cfg = {"base_path": "base", "max_model_len": 32768, "rank": 16}
    if tenant_config:
        cfg["adapters"] = ["private-adapter"]
    worker.config_json, worker.mode = json.dumps(cfg), "sglang-preserved"
    calls = []
    worker.fingerprint = Mock(side_effect=lambda: calls.append("fingerprint") or ["base"])
    monkeypatch.setattr(lab, "weights", Mock(reload=lambda: calls.append("refresh")))
    monkeypatch.setattr(lab.importlib.metadata, "version", lambda name: "test")
    monkeypatch.setattr(
        lab.subprocess, "Popen", lambda args: calls.append("launch") or Mock(poll=lambda: None)
    )
    monkeypatch.setattr(lab, "http", lambda path, **kwargs: calls.append(path) or io.BytesIO())
    lab.SGLangWorker.startup._get_raw_f()(worker)
    assert worker.loaded == set()
    if tenant_config:
        assert worker.error and calls == []
    else:
        assert worker.error is None
        assert calls == [
            "refresh",
            "launch",
            "/health",
            "fingerprint",
            "/release_memory_occupation",
        ]
        assert worker.reference == ["base"]


def test_adapter_model_mapping_preserves_base_and_request_fields():
    body = json.dumps({"model": "adapter-a", "stream": True, "max_tokens": 64}).encode()
    assert lab.adapter_body(body, "base", None) is body
    mapped = json.loads(lab.adapter_body(body, "base", ("adapter-a", ".adapters/a")))
    assert mapped == {
        "model": lab.base_pool_name("base") + ":adapter-a",
        "stream": True,
        "max_tokens": 64,
    }


@pytest.mark.parametrize("failure", [None, "resume", "fingerprint"])
def test_resume_validates_base_before_allowing_adapters(monkeypatch, failure):
    worker = lab.SGLangWorker()
    worker.error, worker.ready, worker.timings = None, False, {}
    worker.versions = {"sglang": "test"}
    worker.reference = [{"token": "391", "logprob": -0.01}]
    worker.fingerprint = Mock(
        return_value=[{"token": "wrong" if failure == "fingerprint" else "391", "logprob": -0.01}]
    )
    worker.details = Mock(local=Mock(return_value={"verified": True}))
    http = Mock(side_effect=RuntimeError("resume failed") if failure == "resume" else None)
    http.return_value = io.BytesIO(b"null")
    monkeypatch.setattr(lab, "http", http)
    if failure:
        with pytest.raises(RuntimeError):
            lab.SGLangWorker.reload_base._get_raw_f()(worker)
        assert worker.error and not worker.ready
        with pytest.raises(RuntimeError):
            worker.ensure_adapter(("a", ".adapters/a"))
    else:
        assert lab.SGLangWorker.reload_base._get_raw_f()(worker) == {"verified": True}
        assert worker.ready and worker.reload_result["base_fingerprint_passed"]
    http.assert_called_once_with("/resume_memory_occupation", body=b"{}")


@pytest.mark.parametrize("success", [False, True])
def test_adapter_load_checks_response_before_caching(monkeypatch, success):
    worker = lab.SGLangWorker()
    worker.error, worker.ready, worker.loaded = None, True, set()
    weights = Mock()
    http = Mock(return_value=io.BytesIO(json.dumps({"success": success}).encode()))
    monkeypatch.setattr(lab, "weights", weights)
    monkeypatch.setattr(lab, "http", http)
    if success:
        worker.ensure_adapter(("a", ".adapters/a"))
        worker.ensure_adapter(("a", ".adapters/a"))
        assert worker.loaded == {"a"}
    else:
        with pytest.raises(RuntimeError, match="adapter load failed"):
            worker.ensure_adapter(("a", ".adapters/a"))
        assert not worker.loaded
    weights.reload.assert_called_once()
    http.assert_called_once_with(
        "/load_lora_adapter",
        body=json.dumps({"lora_name": "a", "lora_path": "/weights/.adapters/a"}).encode(),
    )
