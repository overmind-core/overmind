from contextlib import nullcontext
from threading import Event
from unittest.mock import Mock

import pytest

from experiments.inference_upgrade import prefetched_streamer as module
from experiments.inference_upgrade.prefetched_streamer import borrowed_prefetch, prefetched_stream


@pytest.mark.parametrize("copy_mode", ["clone", "direct", "pinned", "native-pinned", "prefetched"])
@pytest.mark.parametrize("metadata_mode", ["read", "cached"])
def test_cached_prefetch_does_not_construct_an_unused_native_reader(
    monkeypatch, copy_mode, metadata_mode
):
    reader = object()
    factory = Mock(return_value=nullcontext(reader))
    monkeypatch.setattr(module, "SafetensorsStreamer", factory, raising=False)
    unused = copy_mode == "prefetched" and metadata_mode == "cached"
    with module.single_reader_context(copy_mode, metadata_mode) as active:
        assert active is (None if unused else reader)
    assert factory.call_count == (0 if unused else 1)


def test_prefetch_overlaps_readers_without_overwriting_borrowed_buffer():
    ready = [Event(), Event()]
    advances = [0, 0]
    buffers = [bytearray(1), bytearray(1)]

    def reader(index, count):
        for value in range(1, count + 1):
            advances[index] += 1
            buffers[index][0] = value
            ready[index].set()
            yield buffers[index]

    with borrowed_prefetch([reader(0, 3), reader(1, 2)]) as items:
        index, source = next(items)
        assert index == 0 and source[0] == 1
        assert ready[1].wait(2), "Second reader did not prefetch while first was borrowed"
        assert advances == [1, 1]
        assert source[0] == 1
        output = [(index, bytes(source))]
        for index, source in items:
            output.append((index, bytes(source)))
    assert output == [(0, b"\x01"), (1, b"\x01"), (0, b"\x02"), (1, b"\x02"), (0, b"\x03")]
    assert advances == [3, 2]


@pytest.mark.parametrize("early_exit", [False, True])
def test_prefetch_joins_outstanding_reader_before_returning(early_exit):
    entered, release, finished = Event(), Event(), Event()

    def waiting_reader():
        entered.set()
        assert release.wait(2)
        finished.set()
        yield "second"

    with borrowed_prefetch([iter(["first"]), waiting_reader()]) as items:
        assert next(items) == (0, "first")
        assert entered.wait(2)
        release.set()
        if not early_exit:
            assert list(items) == [(1, "second")]
    assert finished.is_set()


def test_prefetch_propagates_reader_failure():
    def failed():
        raise RuntimeError("read failed")
        yield

    with pytest.raises(RuntimeError, match="read failed"), borrowed_prefetch([failed()]) as items:
        next(items)


@pytest.mark.parametrize(
    ("files", "metadata", "budget", "readers"),
    [
        (["a"], [(0, [], [1])], 2, 2),
        (["a", "b"], [(0, [], [1])], 2, 2),
        (["a", "b"], [(0, [], [2]), (0, [], [1])], 2, 2),
        (["a", "b"], [(0, [], [1]), (0, [], [1])], 3, 2),
    ],
)
def test_invalid_split_rejected_before_starting_readers(files, metadata, budget, readers):
    with (
        pytest.raises(ValueError),
        prefetched_stream(files, metadata, memory_bytes=budget, readers=readers),
    ):
        pytest.fail("Invalid reader split accepted")
