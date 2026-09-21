import hashlib
import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from experiments.inference_upgrade import small_snapshot_lab as lab
from experiments.inference_upgrade.streamer_metadata import artifact_identity


def test_lab_command_enables_sleep_without_extending_platform_context():
    args = lab.command({"base_path": ".base_models/test-base", "max_model_len": 32768, "rank": 16})
    assert args.count("--enable-sleep-mode") == 1
    assert "--enable-lora" in args
    assert args[args.index("--distributed-executor-backend") + 1] == (
        "modal_shared.serving.snapshot.SnapshotExecutor"
    )


@pytest.mark.parametrize("shard_gib", [1, 2, 4])
def test_startup_selects_base_artifact_size_and_caches_its_metadata(monkeypatch, shard_gib):
    cfg = {"base_path": ".base_models/base", "artifact_shard_gib": shard_gib}
    instance = lab.Worker()
    instance.config_json = json.dumps(cfg, sort_keys=True)
    instance.mode = "streamed"
    instance.fingerprint = Mock(return_value=[])
    monkeypatch.setattr(lab, "weights", Mock())
    monkeypatch.setattr(lab, "artifacts", Mock())
    monkeypatch.setattr(lab, "command", Mock(return_value=["unused"]))
    monkeypatch.setattr(lab.subprocess, "Popen", Mock(return_value=Mock(poll=lambda: None)))
    monkeypatch.setattr(lab, "http", MagicMock())
    rpc = Mock(return_value={"results": [{"reused": True}]})
    monkeypatch.setattr(lab, "rpc", rpc)
    lab.Worker.startup._get_raw_f()(instance)
    assert instance.error is None
    directory = "/artifacts/" + hashlib.sha256(instance.config_json.encode()).hexdigest()[:20]
    assert [call.args for call in rpc.call_args_list] == [
        ("prepare_kernel_artifact", directory, shard_gib * 1024**3),
        ("prepare_stream_metadata", directory),
    ]
    lab.artifacts.commit.assert_called_once()


@pytest.mark.parametrize(
    "shard_gib,mode", [(0, "streamed"), (True, "streamed"), (8, "streamed"), (1, "preserved")]
)
def test_invalid_artifact_size_fails_before_storage_or_engine(monkeypatch, shard_gib, mode):
    instance = lab.Worker()
    instance.config_json = json.dumps({"artifact_shard_gib": shard_gib})
    instance.mode = mode
    weights = Mock()
    popen = Mock()
    monkeypatch.setattr(lab, "weights", weights)
    monkeypatch.setattr(lab.subprocess, "Popen", popen)
    lab.Worker.startup._get_raw_f()(instance)
    assert instance.error
    weights.reload.assert_not_called()
    popen.assert_not_called()


@pytest.mark.parametrize("cls", [lab.H200Worker, lab.B200Worker])
def test_concrete_workers_declare_modal_parameters(cls):
    assert cls._get_user_cls().__annotations__ == {"config_json": str, "mode": str}


def worker(mode):
    result = lab.Worker()
    result.mode = mode
    result.origin = "test-origin"
    result.runtime = "test-runtime"
    result.error = None
    result.ready = False
    result.reload_result = None
    result.details = Mock(local=Mock(return_value={}))
    result.timings = {}
    result.directory = "/artifacts/base"
    result.reference = [{"token": "391", "logprob": -0.01}]
    result.fingerprint = Mock(return_value=result.reference)
    return result


def test_details_reports_only_public_runtime_environment(monkeypatch):
    expected = {
        "MODAL_REGION": "us-west-2",
        "MODAL_CLOUD_PROVIDER": "CLOUD_PROVIDER_AWS",
        "MODAL_TASK_ID": "test-task",
        "MODAL_IMAGE_ID": None,
    }
    for key, value in expected.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("MODAL_IDENTITY_TOKEN", "private-sentinel")
    result = lab.Worker.details._get_raw_f()(worker("streamed"))
    assert result["runtime_environment"] == expected
    assert "private-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("mode", ["preserved", "streamed"])
@pytest.mark.parametrize("copy_mode", ["clone", "direct", "pinned", "native-pinned", "prefetched"])
@pytest.mark.parametrize("metadata_mode", ["read", "cached"])
def test_restore_order_and_base_validation(monkeypatch, mode, copy_mode, metadata_mode):
    calls = []
    monkeypatch.setattr(lab, "artifacts", Mock(reload=lambda: calls.append("refresh")))
    monkeypatch.setattr(lab, "http", lambda path: calls.append(path) or MagicMock())
    monkeypatch.setattr(
        lab,
        "rpc",
        lambda method, directory, concurrency, copy_mode, cpu_threads, metadata_mode, stream_memory_gb, shard_order, s3_index: (
            calls.append(
                (
                    method,
                    concurrency,
                    copy_mode,
                    cpu_threads,
                    metadata_mode,
                    stream_memory_gb,
                    shard_order,
                    s3_index,
                )
            )
        ),
    )
    instance = worker(mode)
    lab.Worker.reload_base._get_raw_f()(instance, 64, copy_mode, None, metadata_mode, 16)
    assert instance.error is None
    assert instance.ready
    assert calls == (
        [
            "refresh",
            "/wake_up?tags=weights",
            ("reload_kernel_artifact", 64, copy_mode, None, metadata_mode, 16, "manifest", None),
            "/wake_up?tags=kv_cache",
        ]
        if mode == "streamed"
        else ["/wake_up"]
    )
    instance.fingerprint.assert_called_once()


@pytest.mark.parametrize("order", ["smallest-first", "largest-first"])
def test_reload_passes_shard_order_without_changing_artifact(monkeypatch, order):
    monkeypatch.setattr(lab, "artifacts", Mock())
    monkeypatch.setattr(lab, "http", MagicMock())
    rpc = Mock()
    monkeypatch.setattr(lab, "rpc", rpc)
    instance = worker("streamed")
    lab.Worker.reload_base._get_raw_f()(instance, shard_order=order)
    rpc.assert_called_once_with(
        "reload_kernel_artifact", "/artifacts/base", 16, "clone", None, "read", 40, order, None
    )
    assert instance.ready


@pytest.mark.parametrize("mode,order", [("preserved", "smallest-first"), ("streamed", "invalid")])
def test_reload_rejects_invalid_shard_order_before_waking(monkeypatch, mode, order):
    http = MagicMock()
    monkeypatch.setattr(lab, "http", http)
    with pytest.raises(ValueError, match="Shard ordering requires"):
        lab.Worker.reload_base._get_raw_f()(worker(mode), shard_order=order)
    http.assert_not_called()


def test_reload_failure_does_not_wake_kv_or_serve(monkeypatch):
    http = MagicMock()
    monkeypatch.setattr(lab, "artifacts", Mock())
    monkeypatch.setattr(lab, "http", http)
    monkeypatch.setattr(lab, "rpc", Mock(side_effect=RuntimeError("incomplete artifact")))
    instance = worker("streamed")
    with pytest.raises(RuntimeError, match="incomplete artifact"):
        lab.Worker.reload_base._get_raw_f()(instance)
    assert instance.error == "incomplete artifact"
    assert http.call_count == 1
    instance.fingerprint.assert_not_called()
    with pytest.raises(RuntimeError, match="incomplete artifact"):
        instance.ensure_adapter(None)


@pytest.mark.parametrize("failure", [None, "copy", "reload"])
def test_local_staging_preserves_artifact_identity_and_cleans_only_temporary_copy(
    monkeypatch, tmp_path, failure
):
    source = tmp_path / "base"
    source.mkdir()
    manifest = {"files": ["part.safetensors"]}
    (source / "ready.json").write_text(json.dumps(manifest))
    (source / "part.safetensors").write_bytes(b"immutable base weights")
    identity = artifact_identity(source, manifest)
    calls, staged = [], []
    original_copy = lab.shutil.copytree

    def copy(directory, destination, **kwargs):
        calls.append("copy")
        staged.append(Path(destination))
        if failure == "copy":
            (Path(destination) / "partial").write_bytes(b"incomplete")
            raise OSError("copy failed")
        return original_copy(directory, destination, **kwargs)

    def reload(method, directory, *args):
        calls.append("reload")
        assert method == "reload_kernel_artifact"
        assert Path(directory) == staged[0] and Path(directory) != source
        assert artifact_identity(directory, manifest) == identity
        assert (Path(directory) / "part.safetensors").read_bytes() == b"immutable base weights"
        if failure == "reload":
            raise RuntimeError("reload failed")
        return {"verified": True}

    monkeypatch.setattr(lab.shutil, "copytree", copy)
    monkeypatch.setattr(lab, "artifacts", Mock(reload=lambda: calls.append("refresh")))
    monkeypatch.setattr(lab, "http", lambda path: calls.append(path) or MagicMock())
    monkeypatch.setattr(lab, "rpc", reload)
    instance = worker("streamed")
    instance.directory = str(source)
    if failure:
        with pytest.raises((OSError, RuntimeError), match=f"{failure} failed"):
            lab.Worker.reload_base._get_raw_f()(instance, storage_mode="local")
        assert not instance.ready
        assert instance.error == f"{failure} failed"
        instance.fingerprint.assert_not_called()
    else:
        result = lab.Worker.reload_base._get_raw_f()(instance, storage_mode="local")
        assert result["storage_mode"] == "local"
        assert instance.ready
        assert instance.timings["artifact_staging"] >= 0
        assert instance.timings["artifact_cleanup"] >= 0
        instance.fingerprint.assert_called_once()
    assert calls == (
        ["refresh", "copy"]
        if failure == "copy"
        else ["refresh", "copy", "/wake_up?tags=weights", "reload"]
        + ([] if failure else ["/wake_up?tags=kv_cache"])
    )
    assert len(staged) == 1 and not staged[0].exists()
    assert artifact_identity(source, manifest) == identity


@pytest.mark.parametrize("mode,storage_mode", [("preserved", "local"), ("streamed", "unknown")])
def test_reload_rejects_invalid_storage_combinations(mode, storage_mode):
    with pytest.raises(ValueError, match="Local storage requires streamed mode"):
        lab.Worker.reload_base._get_raw_f()(worker(mode), storage_mode=storage_mode)


def test_s3_reload_keeps_base_directory_and_passes_index_to_engine(monkeypatch):
    monkeypatch.setattr(lab, "artifacts", Mock())
    monkeypatch.setattr(lab, "http", MagicMock())
    rpc = Mock()
    monkeypatch.setattr(lab, "rpc", rpc)
    instance = worker("streamed")
    result = lab.Worker.reload_base._get_raw_f()(
        instance,
        storage_mode="s3",
        s3_index="s3://index",
        copy_mode="native-pinned",
        metadata_mode="cached",
    )
    rpc.assert_called_once_with(
        "reload_kernel_artifact",
        "/artifacts/base",
        16,
        "native-pinned",
        None,
        "cached",
        40,
        "manifest",
        "s3://index",
    )
    assert instance.ready and result["storage_mode"] == "s3"
    instance.fingerprint.assert_called_once()


@pytest.mark.parametrize(
    "options",
    [
        {"storage_mode": "s3"},
        {"storage_mode": "volume", "s3_index": "s3://index"},
        {"storage_mode": "s3", "s3_index": "s3://index", "copy_mode": "direct"},
        {
            "storage_mode": "s3",
            "s3_index": "s3://index",
            "copy_mode": "native-pinned",
            "metadata_mode": "read",
        },
    ],
)
def test_invalid_s3_reload_fails_before_waking(monkeypatch, options):
    http = MagicMock()
    monkeypatch.setattr(lab, "http", http)
    with pytest.raises(ValueError, match="S3"):
        lab.Worker.reload_base._get_raw_f()(worker("streamed"), **options)
    http.assert_not_called()


def test_changed_base_fingerprint_blocks_serving(monkeypatch):
    monkeypatch.setattr(lab, "http", MagicMock())
    instance = worker("preserved")
    instance.fingerprint.return_value = [{"token": "wrong", "logprob": -0.01}]
    with pytest.raises(RuntimeError, match="Base fingerprint changed"):
        lab.Worker.reload_base._get_raw_f()(instance)
    with pytest.raises(RuntimeError, match="Base fingerprint changed"):
        instance.ensure_adapter(None)


def test_restore_defers_reload_and_blocks_inference(monkeypatch):
    http = Mock()
    monkeypatch.setattr(lab, "http", http)
    instance = worker("streamed")
    lab.Worker.restore._get_raw_f()(instance)
    http.assert_not_called()
    with pytest.raises(RuntimeError, match="Base reload has not completed"):
        instance.ensure_adapter(None)


def test_benchmark_rejects_reloading_a_warm_container():
    instance = worker("streamed")
    instance.ready = True
    with pytest.raises(RuntimeError, match="fresh container"):
        lab.Worker.reload_base._get_raw_f()(instance)


@pytest.mark.parametrize("failure", [None, "refresh", "attach"])
def test_adapter_refreshes_volume_before_attach_and_caches_only_success(monkeypatch, failure):
    calls = []

    def refresh():
        calls.append("refresh")
        if failure == "refresh":
            raise RuntimeError("refresh failed")

    def attach(path, **kwargs):
        calls.append(path)
        if failure == "attach":
            raise RuntimeError("attach failed")
        return MagicMock()

    monkeypatch.setattr(lab, "weights", Mock(reload=refresh))
    monkeypatch.setattr(lab, "http", attach)
    instance = worker("streamed")
    instance.ready = True
    instance.loaded = set()
    instance.ensure_adapter(None)
    assert calls == []
    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            instance.ensure_adapter(("new-adapter", ".adapters/new"))
        assert instance.loaded == set()
    else:
        instance.ensure_adapter(("new-adapter", ".adapters/new"))
        instance.ensure_adapter(("new-adapter", ".adapters/new"))
        assert instance.loaded == {"new-adapter"}
    assert calls == (["refresh"] if failure == "refresh" else ["refresh", "/v1/load_lora_adapter"])


def test_boot_timeout_cancels_call_without_starting_shutdown_worker(monkeypatch):
    monkeypatch.setattr(
        lab.argparse.ArgumentParser,
        "parse_args",
        lambda self: Namespace(
            model="base",
            mode="streamed",
            rounds=1,
            concurrency=[64],
            copy_mode=["direct"],
            warm_compare=False,
            cpu_threads=4,
            thread_sweep=False,
            client_ttft=False,
            metadata_mode=["cached"],
            stream_memory_gb=[16],
            storage_mode=["volume"],
            shard_order=["manifest"],
            boot_timeout=900,
        ),
    )
    monkeypatch.setattr(lab, "configuration", lambda *args: {"serve_image": "vllm", "gpu": "H200"})
    instance = Mock()
    call = instance.benchmark.spawn.return_value
    call.object_id = "fc-test"
    call.get.side_effect = TimeoutError("boot deadline")
    monkeypatch.setattr(lab.modal.Cls, "from_name", Mock(return_value=Mock(return_value=instance)))
    with pytest.raises(TimeoutError, match="boot deadline"):
        lab.main()
    instance.benchmark.spawn.assert_called_once_with(
        64, {"serve_image": "vllm", "gpu": "H200"}, "direct", False, 4, False, "cached", 16
    )
    call.get.assert_called_once_with(timeout=900)
    call.cancel.assert_called_once_with(terminate_containers=True)
    instance.shutdown_self.remote.assert_not_called()


@pytest.mark.parametrize("copy_mode", ["pinned", "native-pinned"])
@pytest.mark.parametrize("storage_mode", ["volume", "local"])
@pytest.mark.parametrize("shard_order", ["manifest", "smallest-first", "largest-first"])
def test_cli_client_probe_passes_options_without_starting_benchmark(
    monkeypatch, capsys, copy_mode, storage_mode, shard_order
):
    monkeypatch.setattr(
        "sys.argv",
        [
            "lab",
            "--model",
            "base",
            "--mode",
            "streamed",
            "--client-ttft",
            "--stream-memory-gb",
            "4",
            "--copy-mode",
            copy_mode,
            "--metadata-mode",
            "cached",
            "--storage-mode",
            storage_mode,
            "--shard-order",
            shard_order,
        ],
    )
    cfg = {"serve_image": "vllm", "gpu": "H200", "adapters": ["a", "b"]}
    monkeypatch.setattr(lab, "configuration", lambda *args: cfg)
    instance = Mock()
    factory = Mock(return_value=instance)
    monkeypatch.setattr(lab.modal.Cls, "from_name", Mock(return_value=factory))
    probe = Mock(return_value={"client_ttft_s": 12.0, "adapter_effect_verified": True})
    monkeypatch.setattr(lab, "client_first_token_probe", probe)
    monkeypatch.setattr(lab.time, "sleep", Mock())
    lab.main()
    probe.assert_called_once_with(
        instance,
        cfg,
        {
            "concurrency": 16,
            "copy_mode": copy_mode,
            "cpu_threads": None,
            "metadata_mode": "cached",
            "stream_memory_gb": 4,
            "storage_mode": storage_mode,
            "shard_order": shard_order,
        },
    )
    instance.benchmark.spawn.assert_not_called()
    assert "adapters" not in factory.call_args.kwargs["config_json"]
    assert "storage_mode" not in factory.call_args.kwargs["config_json"]
    assert "shard_order" not in factory.call_args.kwargs["config_json"]
    assert '"client_ttft_s": 12.0' in capsys.readouterr().out


@pytest.mark.parametrize("mode,extra", [("preserved", ["--client-ttft"]), ("streamed", [])])
def test_cli_rejects_shard_order_without_streamed_client_timing(monkeypatch, mode, extra):
    monkeypatch.setattr(
        "sys.argv",
        ["lab", "--model", "base", "--mode", mode, "--shard-order", "largest-first", *extra],
    )
    with pytest.raises(SystemExit) as exc:
        lab.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("mode,extra", [("preserved", ["--client-ttft"]), ("streamed", [])])
def test_cli_rejects_local_storage_without_streamed_client_timing(monkeypatch, mode, extra):
    monkeypatch.setattr(
        "sys.argv", ["lab", "--model", "base", "--mode", mode, "--storage-mode", "local", *extra]
    )
    with pytest.raises(SystemExit) as exc:
        lab.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("comparison", ["--warm-compare", "--thread-sweep"])
def test_cli_rejects_warm_client_ttft_comparisons(monkeypatch, comparison):
    monkeypatch.setattr(
        "sys.argv", ["lab", "--model", "base", "--mode", "streamed", "--client-ttft", comparison]
    )
    with pytest.raises(SystemExit) as exc:
        lab.main()
    assert exc.value.code == 2


@pytest.mark.parametrize("copy_mode", ["clone", "direct", "pinned"])
def test_benchmark_keeps_validation_and_shutdown_local(monkeypatch, copy_mode):
    instance = worker("streamed")
    instance.infer_stream = Mock()
    instance.infer = Mock()
    instance.reload_base = Mock()
    instance.reload_base.local.return_value = {"runtime": "same-container"}
    instance.shutdown_self = Mock()
    variants = []

    def request(local, cfg, variant):
        assert local.infer_stream.remote_gen is instance.infer_stream.local
        variants.append(variant)
        return {"ttft_s": 0.1, "content": "391"}

    monkeypatch.setattr(lab, "request", request)
    monkeypatch.setattr(
        lab, "verify_adapter_effect", lambda *args: {"adapter_effect_verified": True}
    )
    result = lab.Worker.benchmark._get_raw_f()(instance, 64, {}, copy_mode)
    assert variants == [0, 0, 1, 0]
    assert result["runtime"] == "same-container"
    assert result["adapter_effect_verified"]
    instance.reload_base.local.assert_called_once_with(64, copy_mode, None, "read", 40)
    instance.shutdown_self.local.assert_called_once()
    instance.shutdown_self.remote.assert_not_called()


def test_benchmark_shuts_down_locally_when_validation_fails():
    instance = worker("streamed")
    instance.infer_stream = Mock()
    instance.infer = Mock()
    instance.reload_base = Mock()
    instance.reload_base.local.side_effect = RuntimeError("invalid base")
    instance.shutdown_self = Mock()
    with pytest.raises(RuntimeError, match="invalid base"):
        lab.Worker.benchmark._get_raw_f()(instance, 16, {})
    instance.shutdown_self.local.assert_called_once()


def test_first_token_probe_streams_before_canaries_and_large_diagnostics(monkeypatch):
    instance = worker("streamed")
    instance.reload_base = Mock(local=Mock(return_value={"origin": "same", "reload": "large"}))
    instance.infer_stream = Mock(local=Mock(return_value=iter([b"first token", b"done"])))
    instance.infer = Mock()
    instance.shutdown_self = Mock()
    canary = Mock(return_value={"content": "391"})
    effect = Mock(return_value={"adapter_effect_verified": True})
    monkeypatch.setattr(lab, "request", canary)
    monkeypatch.setattr(lab, "verify_adapter_effect", effect)
    options = {"copy_mode": "pinned", "metadata_mode": "cached", "stream_memory_gb": 4}
    stream = lab.Worker.first_token_probe._get_raw_f()(instance, {}, options, body=b"request")
    assert next(stream) == b"first token"
    instance.reload_base.local.assert_called_once_with(**options)
    instance.infer_stream.local.assert_called_once_with(body=b"request")
    canary.assert_not_called()
    effect.assert_not_called()
    assert next(stream) == b"done"
    event = next(stream)
    assert event["event"] == "validated"
    assert event["result"]["origin"] == "same"
    assert event["result"]["adapter_effect_verified"]
    assert len(event["result"]["followup_requests"]) == 3
    assert [call.args[2] for call in canary.call_args_list] == [0, 1, 0]
    assert canary.call_args.args[0].infer_stream.remote_gen is instance.infer_stream.local
    with pytest.raises(StopIteration):
        next(stream)
    instance.shutdown_self.local.assert_called_once()
    instance.shutdown_self.remote.assert_not_called()


def test_first_token_probe_shuts_down_on_reload_failure():
    instance = worker("streamed")
    instance.reload_base = Mock(local=Mock(side_effect=RuntimeError("bad base")))
    instance.infer_stream = Mock()
    instance.shutdown_self = Mock()
    with pytest.raises(RuntimeError, match="bad base"):
        list(lab.Worker.first_token_probe._get_raw_f()(instance, {}, {}))
    instance.infer_stream.local.assert_not_called()
    instance.shutdown_self.local.assert_called_once()


@pytest.mark.parametrize("validated", [True, False])
def test_client_probe_times_content_and_requires_final_validation(monkeypatch, validated):
    # The real SSE parser must ignore role-only events and join split data lines.
    chunks = [
        b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
        b'data: {"choices":[{"delta":{"content":',
        b'"391"}}]}\n\ndata: [DONE]\n\n',
    ]
    if validated:
        chunks.append(
            {"event": "validated", "result": {"origin": "same", "adapter_effect_verified": True}}
        )
    remote = Mock(return_value=iter(chunks))
    instance = Mock(first_token_probe=Mock(remote_gen=remote))
    times = iter([10.0, 14.0, 19.0])
    monkeypatch.setattr(lab.time, "monotonic", lambda: next(times))
    cfg = {"base_path": "base", "adapters": ["a", "b"]}
    if not validated:
        with pytest.raises(RuntimeError, match="without successful validation"):
            lab.client_first_token_probe(instance, cfg, {})
        return
    result = lab.client_first_token_probe(instance, cfg, {"stream_memory_gb": 4})
    assert result["client_ttft_s"] == 4.0
    assert result["client_first_request"]["elapsed_s"] == 9.0
    assert result["client_first_request"]["content"] == "391"
    assert result["origin"] == "same"
    assert remote.call_args.args == (cfg, {"stream_memory_gb": 4})
    assert remote.call_args.kwargs["adapter"] == ("snapshot-validation-0", "a")


@pytest.mark.parametrize("thread_sweep", [False, True])
def test_warm_comparison_unloads_adapters_and_preserves_first_result(monkeypatch, thread_sweep):
    instance = worker("streamed")
    instance.infer_stream = Mock()
    instance.infer = Mock()
    instance.loaded = set()
    instance.shutdown_self = Mock()
    calls = []

    def reload(concurrency, copy_mode, cpu_threads, metadata_mode, stream_memory_gb):
        assert stream_memory_gb == 40
        assert metadata_mode == "read"
        assert not instance.loaded
        assert not instance.ready
        instance.ready = True
        return {"copy_mode": copy_mode, "cpu_threads": cpu_threads, "timings": instance.timings}

    def request(*args):
        instance.loaded.add("adapter-a")
        return {"ttft_s": 0.1}

    def http(path, **kwargs):
        calls.append((path, kwargs))
        return MagicMock()

    instance.reload_base = Mock(local=reload)
    monkeypatch.setattr(lab, "request", request)
    monkeypatch.setattr(lab, "http", http)
    monkeypatch.setattr(lab, "rpc", lambda method, directory: calls.append((method, {})))
    monkeypatch.setattr(lab, "verify_adapter_effect", lambda *args: {})
    result = lab.Worker.benchmark._get_raw_f()(
        instance, 16, {}, "pinned", not thread_sweep, None, thread_sweep
    )
    assert result["copy_mode"] == "pinned"
    expected = (
        [("pinned", n) for n in (None, 1, 2, 4, 8, 8, 4, 2, 1, None)]
        if thread_sweep
        else [(m, None) for m in ("direct", "pinned", "pinned", "direct")]
    )
    assert [(r["copy_mode"], r["cpu_threads"]) for r in result["warm_comparisons"]] == expected
    assert [path for path, _ in calls] == [
        "/v1/unload_lora_adapter",
        "clear_lab_adapters",
        "/sleep?level=2",
    ] * len(expected)
    assert (
        len({id(r["timings"]) for r in [result, *result["warm_comparisons"]]}) == len(expected) + 1
    )
    instance.shutdown_self.local.assert_called_once()
