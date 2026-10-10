import hashlib
import io
import json
from dataclasses import dataclass

import pytest

from modal_shared.decision_inference import prepare_requests
from modal_shared.serving.artifacts import digest_json


@dataclass
class EncodedRecord:
    input_ids: tuple
    questions: tuple = ()
    record_id: str = "test"


def encode_record(tokenizer, value, *, max_length):
    assert set(value) == {"state", "questions"}
    ids = tuple(json.dumps(value).encode())
    assert len(ids) <= max_length
    return EncodedRecord(ids)


def record():
    request = {"state": "evidence", "question": "Which?", "kind": "choice", "options": ["a", "b"]}
    return {"key": "row", "decision": request, "input_sha256": digest_json(request)}


def prepare(records, context=1024):
    output, failures = io.StringIO(), io.StringIO()
    result = prepare_requests(
        records,
        output,
        failures,
        None,
        context,
        encode_record,
    )
    return result, output.getvalue(), failures.getvalue()


def test_native_prediction_preparation_keeps_identity_without_supervision():
    report, output, failures = prepare(iter([record()]))
    row = json.loads(output)
    assert row["key"] == "row" and row["input_sha256"] == record()["input_sha256"]
    assert set(row) == {
        "key",
        "input_sha256",
        "input_ids",
        "record",
        "option_order",
        "kind",
        "question",
        "options",
    }
    assert report["ready_decisions"] == 1 and report["failed_decisions"] == 0
    assert failures == ""


def test_native_prediction_reports_oversized_input_without_truncation():
    report, output, failures = prepare([record()], context=8)
    assert output == "" and report["failed_decisions"] == 1
    failure = json.loads(failures)
    assert failure["key"] == "row" and failure["code"] == "context_overflow"
    assert failure["tokens"] > 8


@pytest.mark.parametrize("change", ["hash", "reference", "duplicate"])
def test_invalid_benchmark_input_cannot_be_prepared(change):
    row = record()
    rows = [row]
    if change == "hash":
        row["decision"]["state"] = "changed"
    elif change == "reference":
        row["reference"] = {"probabilities": [1, 0]}
    else:
        rows.append(row)
    with pytest.raises(ValueError):
        prepare(rows)


def test_multilingual_request_uses_the_sealed_export_hash_encoding():
    row = record()
    row["decision"]["state"] = "证据"
    row["input_sha256"] = hashlib.sha256(
        json.dumps(
            row["decision"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    report, _, _ = prepare([row])
    assert report["ready_decisions"] == 1
