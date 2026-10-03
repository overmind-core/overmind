import hashlib
import io
import json

import pytest

from modal_shared.decision_inference import prepare_requests
from modal_shared.serving.artifacts import digest_json


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return messages[0]["content"] + "<decision>"

    def encode(self, text, **kwargs):
        prefix, suffix = text.split("<decision>", 1)
        return [1] * len(prefix) + [2] + ([{"A": 3, "B": 4}[suffix]] if suffix else [])


def record():
    request = {"state": "evidence", "question": "Which?", "kind": "choice", "options": ["a", "b"]}
    return {"key": "row", "decision": request, "input_sha256": digest_json(request)}


def prepare(records, context=1024):
    output, failures = io.StringIO(), io.StringIO()
    result = prepare_requests(
        records,
        output,
        failures,
        Tokenizer(),
        [{"code": "A", "token_id": 3}, {"code": "B", "token_id": 4}],
        context,
    )
    return result, output.getvalue(), failures.getvalue()


def test_native_prediction_preparation_keeps_identity_without_supervision():
    report, output, failures = prepare(iter([record()]))
    row = json.loads(output)
    assert row["key"] == "row" and row["input_sha256"] == record()["input_sha256"]
    assert set(row) == {"key", "input_sha256", "input_ids", "option_token_ids", "kind"}
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


def test_prediction_stream_preserves_every_key_and_model_identity_without_gold():
    torch = pytest.importorskip("torch")
    from types import SimpleNamespace

    from overbae.services.sft_assets.decision_readout import predict

    class Model(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.embedding = torch.nn.Embedding(8, 3)
            self.head = torch.nn.Linear(3, 8)

        def get_decoder(self):
            return lambda input_ids, **kw: SimpleNamespace(
                last_hidden_state=self.embedding(input_ids)
            )

        def get_output_embeddings(self):
            return self.head

        def get_input_embeddings(self):
            return self.embedding

    _, prepared, _ = prepare([record()])
    output = io.StringIO()
    predict(
        Model(),
        0,
        iter([json.loads(prepared)]),
        output,
        "artifact-sha",
        max_rows=8,
        max_tokens=1024,
    )
    row = json.loads(output.getvalue())
    assert row["key"] == "row" and row["model_identity"] == "artifact-sha"
    assert row["input_sha256"] == record()["input_sha256"]
    assert sum(row["probabilities"]) == pytest.approx(1)
    assert len(row["log_probabilities"]) == 2
    assert "target_probabilities" not in row
