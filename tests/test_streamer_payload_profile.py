from types import SimpleNamespace as Object

import pytest

from experiments.inference_upgrade.streamer_payload_profile import PayloadTrace


def tensor(name, start):
    return Object(name=name, offsets=Object(start=start))


def request(index, offset, sizes):
    class Request:
        files = [Object(id=index, offset=offset, chunks=sizes)]

    return Request()


def test_trace_interleaves_readers_with_local_file_ids():
    trace = PayloadTrace(
        ["part0", "part1"],
        [(8, [tensor("a", 0), tensor("b", 4)], [4, 6]), (16, [tensor("c", 0)], [3])],
    )
    first, second = request(0, 8, [4, 6]), request(0, 16, [3])
    trace.arrive(first, "a", 4, 1.0, 1.0, file_indices=[0])
    trace.arrive(second, "c", 3, 0.1, 1.2, file_indices=[1])
    trace.arrive(first, "b", 6, 0.1, 1.3, file_indices=[0])
    result = trace.result()
    assert [row["batch"] for row in result["tensors"]] == [0, 1, 0]
    assert [row["batch_first"] for row in result["tensors"]] == [True, True, False]
    assert [row["delivered_bytes"] for row in result["batches"]] == [10, 3]
    assert [row["ranges"][0]["file"] for row in result["batches"]] == [0, 1]


def test_trace_records_actual_batch_changes_and_payload_offsets():
    trace = PayloadTrace(
        ["/artifacts/part0", "/artifacts/part1"],
        [(8, [tensor("a", 0), tensor("b", 4)], [4, 6]), (16, [tensor("c", 0)], [3])],
    )
    first = request(0, 8, [4, 6])
    trace.arrive(first, "b", 6, 2.0, 3.0)
    trace.arrive(first, "a", 4, 0.1, 3.2)
    trace.arrive(request(1, 16, [3]), "c", 3, 5.0, 9.0)
    result = trace.result()
    assert result["files"] == ["part0", "part1"]
    assert [t["batch"] for t in result["tensors"]] == [0, 0, 1]
    assert [t["batch_first"] for t in result["tensors"]] == [True, False, True]
    assert [t["delivered_before"] for t in result["tensors"]] == [0, 6, 10]
    assert [b["wait_s"] for b in result["batches"]] == [2.1, 5.0]
    assert [b["delivered_bytes"] for b in result["batches"]] == [10, 3]


def test_trace_handles_one_shard_split_across_native_batches():
    trace = PayloadTrace(["part"], [(8, [tensor("a", 0), tensor("b", 4)], [4, 6])])
    trace.arrive(request(0, 8, [4]), "a", 4, 1.0, 1.0)
    trace.arrive(request(0, 12, [6]), "b", 6, 2.0, 4.0)
    assert len(trace.result()["batches"]) == 2


def test_trace_rejects_incomplete_batch():
    trace = PayloadTrace(["part"], [(8, [tensor("a", 0)], [4])])
    trace.arrive(request(0, 8, [4, 4]), "a", 4, 1.0, 1.0)
    with pytest.raises(RuntimeError, match="coverage"):
        trace.result()


@pytest.mark.parametrize("active", [None, request(1, 8, [4]), request(0, 12, [4])])
def test_trace_rejects_tensor_outside_actual_request(active):
    trace = PayloadTrace(["part"], [(8, [tensor("a", 0)], [4])])
    with pytest.raises(RuntimeError):
        trace.arrive(active, "a", 4, 0.0, 0.0)
