from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import checkpoint_engine_probe as lab


@pytest.mark.parametrize(
    "failure",
    [None, "initialize", "register", "gather", "verify", "native_store", "unregister", "destroy"],
)
def test_parameter_server_gate_preserves_stage_and_cleans_up(monkeypatch, failure):
    monkeypatch.setattr(lab, "probe", SimpleNamespace(local=lambda: {"imports_all_passed": True}))
    for key in ("RANK", "WORLD_SIZE", "MASTER_ADDR", "MASTER_PORT"):
        monkeypatch.setenv(key, "previous")

    def tensor(count, dtype):
        return SimpleNamespace(
            dtype=dtype,
            shape=(count,),
            numel=lambda: count,
            element_size=lambda: 2 if dtype == "bf16" else 4,
        )

    torch = SimpleNamespace(
        bfloat16="bf16",
        float32="fp32",
        arange=tensor,
        cuda=SimpleNamespace(
            set_device=Mock(), get_device_name=lambda _: "test GPU", is_initialized=lambda: True
        ),
    )
    monkeypatch.setattr(lab, "torch", torch, raising=False)
    metadata = {
        0: SimpleNamespace(
            rdma_device="test-rdma",
            p2p_store_addr=None if failure == "native_store" else "test-address",
            memory_buffer_metas_list=[
                SimpleNamespace(
                    size=512,
                    metas=[
                        SimpleNamespace(name="bf16", dtype="bf16", shape=(32,)),
                        SimpleNamespace(name="fp32", dtype="fp32", shape=(17,)),
                    ],
                )
            ],
        )
    }
    server = Mock(
        device_manager=SimpleNamespace(transfer_engine_protocol="rdma"),
        get_metas=Mock(return_value={} if failure == "verify" else metadata),
    )
    factory = Mock(return_value=server)
    dist = Mock(is_initialized=Mock(return_value=failure not in {"initialize", "register"}))
    operation = {
        "initialize": factory,
        "register": server.register_checkpoint,
        "gather": server.gather_metas,
        "unregister": server.unregister_checkpoint,
        "destroy": dist.destroy_process_group,
    }.get(failure)
    if operation is not None:
        operation.side_effect = RuntimeError("sentinel")
    monkeypatch.setattr(lab, "ParameterServer", factory, raising=False)
    monkeypatch.setattr(lab, "checkpoint_dist", dist, raising=False)
    result = lab.parameter_server_probe.get_raw_f()()
    assert result["parameter_server_verified"] is (failure is None)
    assert result["stage"] == (
        failure
        if failure in {"initialize", "register", "gather", "verify"}
        else "verify"
        if failure == "native_store"
        else "complete"
    )
    assert result["model_loading_tested"] is False
    assert result["snapshot_tested"] is False
    assert result["mooncake_p2p_transfer_tested"] is False
    assert result["elapsed_s"] >= 0
    if failure in {"unregister", "destroy"}:
        assert "sentinel" in result["cleanup_error"]
    elif failure:
        assert result["error"]
    else:
        assert result["payload_bytes"] == 132
        assert result["pinned_buffer_bytes"] == 512
        assert result["registered_tensors"] == 2
    if failure in {"initialize", "register"}:
        server.unregister_checkpoint.assert_not_called()
    else:
        server.unregister_checkpoint.assert_called_once_with("tiny-base")
        dist.destroy_process_group.assert_called_once()
    if failure != "initialize":
        assert server.register_checkpoint.call_args.kwargs["use_inplace_pin_memory"] is False
