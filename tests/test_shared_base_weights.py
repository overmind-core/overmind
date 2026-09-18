import importlib.util
import os
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest


@pytest.fixture
def loader(monkeypatch):
    torch = MagicMock()
    torch.inference_mode.side_effect = lambda: lambda method: method
    for name, module in {
        "torch": torch,
        "runai_model_streamer": MagicMock(),
        "runai_model_streamer.file_streamer": MagicMock(),
        "runai_model_streamer.safetensors_streamer.safetensors_pytorch": MagicMock(),
        "safetensors.torch": MagicMock(),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[1] / "modal_shared/serving/weights.py"
    spec = importlib.util.spec_from_file_location("tested_shared_base_weights", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "version", lambda _: "0.16.1")
    return module


@pytest.mark.parametrize("fail", [False, True])
def test_pinned_reader_owns_memory_and_restores_allocator(loader, fail):
    original = loader.requests_iterator.np
    owner = loader.torch.empty.return_value
    owner.is_pinned.return_value = True
    try:
        with loader.pinned_reader_buffer():
            assert loader.requests_iterator.np.empty(1024, original.uint8) is owner.numpy()
            loader.torch.empty.assert_called_once_with(
                1024, dtype=loader.torch.uint8, pin_memory=True
            )
            if fail:
                raise RuntimeError("reader failure")
    except RuntimeError:
        assert fail
    assert loader.requests_iterator.np is original


def test_pinned_reader_rejects_changed_dependency(loader, monkeypatch):
    monkeypatch.setattr(loader, "version", lambda _: "different")
    with pytest.raises(RuntimeError, match="0.16.1"), loader.pinned_reader_buffer():
        pytest.fail("Unverified reader must not run")


def test_capture_rejects_tenant_adapters(loader):
    worker = loader.SharedBaseWeights()
    worker.model_runner = Mock()
    worker.model_runner.get_model.return_value.lora_manager.list_adapters.return_value = {1}
    with pytest.raises(RuntimeError, match="must not contain adapters"):
        worker.prepare_shared_base("unused", "base", [])


@pytest.mark.parametrize("failure", [None, "missing", "duplicate", "unpinned", "address"])
def test_restore_checks_coverage_addresses_and_resets_lora_banks(loader, monkeypatch, failure):
    parameter = Mock(shape=(2,), dtype="torch.bfloat16")
    parameter.stride.return_value = (1,)
    parameter.data_ptr.return_value = 123
    parameter.numel.return_value = 2
    parameter.element_size.return_value = 2
    parameter.is_pinned.return_value = failure != "unpinned"
    manager = Mock(lora_slots=2, modules={"layer": Mock()})
    manager.list_adapters.return_value = []
    model = Mock(lora_manager=manager)
    model.named_parameters.return_value = [("weight", parameter)]
    manifest = {
        "contract": {},
        "layout": loader.parameter_layout(model),
        "bytes": 4,
        "files": {"part-0000.safetensors": {"size": 12}},
    }
    worker = loader.SharedBaseWeights()
    worker.model_runner = Mock(get_model=Mock(return_value=model))
    worker.shared_base_manifest = manifest
    worker.shared_base_directory = "unused"
    worker.shared_base_metadata = [(8, [], [4])]
    worker.shared_base_addresses = {"weight": 456 if failure == "address" else 123}
    monkeypatch.setattr(loader, "read_artifact", lambda *_: manifest)
    monkeypatch.setattr(loader, "pinned_reader_buffer", nullcontext)
    streamer = loader.SafetensorsStreamer.return_value.__enter__.return_value
    count = 0 if failure == "missing" else 2 if failure == "duplicate" else 1
    streamer.get_tensors.return_value = [("weight", parameter)] * count
    monkeypatch.setenv("RUNAI_STREAMER_CONCURRENCY", "3")
    monkeypatch.delenv("RUNAI_STREAMER_MEMORY_LIMIT", raising=False)
    if failure:
        with pytest.raises(RuntimeError):
            worker.restore_shared_base()
        manager.modules["layer"].reset_lora.assert_not_called()
    else:
        assert worker.restore_shared_base()["bytes"] == 4
        parameter.copy_.assert_called_once_with(parameter, non_blocking=False)
        assert manager.modules["layer"].reset_lora.call_count == 2
        assert manager._last_mapping is None
        assert manager._last_slot_layout is None
        loader.torch.cuda.synchronize.assert_called_once()
    assert os.environ["RUNAI_STREAMER_CONCURRENCY"] == "3"
    assert "RUNAI_STREAMER_MEMORY_LIMIT" not in os.environ


def test_metadata_rejects_incorrect_payload_coverage(loader):
    tensor = SimpleNamespace(
        name="weight",
        shape=[2],
        offsets=SimpleNamespace(start=0, end=4),
        get_torch_dtype=lambda: "torch.bfloat16",
        get_bytesize=lambda: 4,
    )
    manifest = {
        "files": {"part": {"size": 12}},
        "bytes": 4,
        "layout": {"weight": {"shape": [2], "dtype": "torch.bfloat16"}},
    }
    loader.validate_metadata([(8, [tensor], [4])], manifest)
    tensor.offsets.start = 1
    with pytest.raises(ValueError, match="layout mismatch"):
        loader.validate_metadata([(8, [tensor], [4])], manifest)
