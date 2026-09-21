from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import native_pinned_buffer as probe


@pytest.mark.parametrize("fail", [False, True])
def test_pinned_allocator_is_scoped_and_restored_on_failure(monkeypatch, fail):
    numpy = SimpleNamespace(uint8=object())
    iterator = SimpleNamespace(np=numpy)
    owner = Mock()
    owner.is_pinned.return_value = True
    torch = SimpleNamespace(empty=Mock(return_value=owner), uint8=object())
    monkeypatch.setattr(probe, "requests_iterator", iterator, raising=False)
    monkeypatch.setattr(probe, "torch", torch, raising=False)
    monkeypatch.setattr(probe, "version", lambda _: "0.16.1")
    try:
        with probe.native_pinned_buffer() as stats:
            assert iterator.np.empty(1024, dtype=numpy.uint8) is owner.numpy.return_value
            torch.empty.assert_called_once_with(1024, dtype=torch.uint8, pin_memory=True)
            assert stats["allocations"] == 1
            assert stats["bytes"] == 1024
            if fail:
                raise RuntimeError("reader failed")
    except RuntimeError as exc:
        assert fail and str(exc) == "reader failed"
    assert iterator.np is numpy


def test_pinned_allocator_rejects_unverified_streamer_version(monkeypatch):
    monkeypatch.setattr(probe, "version", lambda _: "unknown")
    with (
        pytest.raises(RuntimeError, match="requires streamer 0.16.1"),
        probe.native_pinned_buffer(),
    ):
        pytest.fail("Unverified allocator must not run")
