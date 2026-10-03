from __future__ import annotations

import uuid
import weakref
from types import SimpleNamespace

from overbae.services.datasets.rows import DatasetRow
from overbae.services.finetuning_split import split_datapoint_ids


def _dp(
    *,
    order: int,
    source_trace_id: str = "",
    expected_output: str = "",
    persona: str = "",
    tags: list | None = None,
    extra: dict | None = None,
    dp_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=dp_id or uuid.uuid4(),
        order=order,
        source_trace_id=source_trace_id,
        expected_output=expected_output,
        persona=persona,
        tags=tags or [],
        extra=extra or {},
    )


def test_ordered_split_80_20():
    datapoints = [_dp(order=i) for i in range(100)]
    train_ids, val_ids, warnings = split_datapoint_ids(datapoints, 0.2, method="ordered")
    assert warnings == []
    assert len(train_ids) == 80
    assert len(val_ids) == 20
    ordered_ids = [dp.id for dp in sorted(datapoints, key=lambda d: d.order)]
    assert train_ids == ordered_ids[:80]
    assert val_ids == ordered_ids[80:]


def test_random_split_trace_grouping():
    trace_a = "trace-a"
    trace_b = "trace-b"
    datapoints = []
    for i in range(5):
        datapoints.append(_dp(order=i, source_trace_id=trace_a))
    for i in range(5, 10):
        datapoints.append(_dp(order=i, source_trace_id=trace_b))

    train_ids, val_ids, _ = split_datapoint_ids(datapoints, 0.2, method="random")
    by_id = {dp.id: dp for dp in datapoints}

    def split_side(ids):
        return {by_id[i].source_trace_id for i in ids}

    train_traces = split_side(train_ids)
    val_traces = split_side(val_ids)
    assert not train_traces.intersection(val_traces)


def test_split_min_one_each():
    datapoints = [_dp(order=i) for i in range(3)]
    train_ids, val_ids, _ = split_datapoint_ids(datapoints, 0.2, method="ordered")
    assert len(train_ids) == 2
    assert len(val_ids) == 1


def test_training_split_keeps_reviewed_duplicate_rows():
    rows = [
        {
            "id": i,
            "order": i,
            "input": "same" if i < 2 else "different",
            "expected_output": "answer",
        }
        for i in range(3)
    ]
    train, validation, warnings = split_datapoint_ids(rows, 0.3)
    assert len(train) + len(validation) == 3
    assert ({0, 1} <= set(train)) or ({0, 1} <= set(validation))
    assert not any("duplicates removed" in warning for warning in warnings)


def test_split_does_not_retain_large_input_rows():
    references = []

    def rows():
        for index in range(30):
            assert sum(reference() is not None for reference in references) <= 2
            row = DatasetRow(index=index, input=str(index) + " evidence" * 1000)
            references.append(weakref.ref(row))
            yield row

    training, validation, _ = split_datapoint_ids(rows(), 0.2, method="ordered")
    assert training == [str(i) for i in range(24)]
    assert validation == [str(i) for i in range(24, 30)]
