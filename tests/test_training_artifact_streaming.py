import hashlib
import json

import pytest

from modal_shared.training_data import materialize_files, row_key
from overbae.services.sft_assets.preprocess import preprocess_rows


def test_disk_materialization_preserves_order_duplicates_and_refuses_changed_targets(tmp_path):
    rows = [{"decision": {"target_probabilities": [p, 1 - p]}} for p in [0.2, 0.8]]
    tokens = tmp_path / "tokens.jsonl"
    records = [{"key": row_key(row), "input_ids": [i]} for i, row in enumerate(rows)]
    tokens.write_text("".join(json.dumps(row) + "\n" for row in records))
    digest = hashlib.sha256(tokens.read_bytes()).hexdigest()
    source, destination = tmp_path / "selected.jsonl", tmp_path / "data.jsonl"
    source.write_text("".join(json.dumps(rows[i]) + "\n" for i in [1, 0, 1]))
    materialize_files(tokens, digest, [(source, destination)])
    assert [json.loads(line) for line in destination.read_text().splitlines()] == [
        records[1],
        records[0],
        records[1],
    ]
    before = destination.read_bytes()
    source.write_text(json.dumps({"decision": {"target_probabilities": [0.5, 0.5]}}))
    with pytest.raises(ValueError, match="validated"):
        materialize_files(tokens, digest, [(source, destination)])
    assert destination.read_bytes() == before
    with pytest.raises(ValueError, match="changed"):
        materialize_files(tokens, "wrong", [(source, destination)])
    assert destination.read_bytes() == before


def test_preprocessing_consumes_one_pass_input_and_writes_without_retaining_artifacts(tmp_path):
    output = tmp_path / "tokens.jsonl"
    seen = []

    def source():
        for index in range(500):
            if index:
                assert len(seen) == index
            yield {"messages": [{"role": "assistant", "content": str(index)}]}

    def tokenize(tokenizer, model, messages, tools):
        seen.append(messages[0]["content"])
        return {"input_ids": [1, 2], "labels": [-100, 2]}

    class Tokenizer:
        def decode(self, ids):
            return "answer"

    with output.open("w") as stream:
        artifacts, report = preprocess_rows(
            source(), Tokenizer(), "test", 128, tokenize, output=stream
        )
    assert artifacts == []
    assert report["ready"] and report["rows"] == 500 and report["tokens"] == 1000
    assert len(output.read_text().splitlines()) == 500
