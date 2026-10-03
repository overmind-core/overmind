import hashlib
import json

import pytest

from modal_shared.training_data import materialize_files, row_key, write_selection
from overbae.services.sft_assets.preprocess import preprocess_rows


def test_disk_materialization_preserves_order_duplicates_and_refuses_changed_targets(tmp_path):
    rows = [{"decision": {"target_probabilities": [p, 1 - p]}} for p in [0.2, 0.8]]
    tokens = tmp_path / "tokens.jsonl"
    records = [{"key": row_key(row), "input_ids": [i]} for i, row in enumerate(rows)]
    tokens.write_text("".join(json.dumps(row) + "\n" for row in records))
    digest = hashlib.sha256(tokens.read_bytes()).hexdigest()
    source, destination = tmp_path / "selected.jsonl", tmp_path / "data.jsonl"
    source.write_text("".join(json.dumps(rows[i]) + "\n" for i in [1, 0, 1]))
    selection = tmp_path / "selected.keys"
    spec = write_selection(source, selection)
    materialize_files(tokens, digest, [(selection, destination, spec)])
    assert [json.loads(line) for line in destination.read_text().splitlines()] == [
        records[1],
        records[0],
        records[1],
    ]
    before = destination.read_bytes()
    source.write_text(json.dumps({"decision": {"target_probabilities": [0.5, 0.5]}}))
    spec = write_selection(source, selection)
    with pytest.raises(ValueError, match="validated"):
        materialize_files(tokens, digest, [(selection, destination, spec)])
    assert destination.read_bytes() == before
    with pytest.raises(ValueError, match="changed"):
        materialize_files(tokens, "wrong", [(selection, destination, spec)])
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


@pytest.mark.parametrize("corruption", ["truncated", "checksum", "count", "unknown"])
def test_invalid_validation_selection_does_not_publish_training(tmp_path, corruption):
    rows = [{"messages": [{"role": "assistant", "content": text}]} for text in ("train", "val")]
    tokens = tmp_path / "tokens.jsonl"
    tokens.write_text(
        "".join(
            json.dumps({"key": row_key(row), "input_ids": [i]}) + "\n" for i, row in enumerate(rows)
        )
    )
    selections = []
    for i, name in enumerate(("data", "val")):
        source, selected, destination = (
            tmp_path / f"{name}{suffix}" for suffix in (".jsonl", ".keys", ".out")
        )
        source.write_text(json.dumps(rows[i]) + "\n")
        spec = write_selection(source, selected)
        destination.write_bytes(b"previous validated output")
        selections.append((selected, destination, spec))
    selected, _, spec = selections[1]
    if corruption == "truncated":
        selected.write_bytes(selected.read_bytes()[:-1])
        spec["sha256"] = hashlib.sha256(selected.read_bytes()).hexdigest()
    elif corruption == "checksum":
        selected.write_bytes(b"x" * 32)
    elif corruption == "count":
        spec["rows"] += 1
    else:
        selected.write_bytes(bytes.fromhex(row_key({"messages": []})))
        spec["sha256"] = hashlib.sha256(selected.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        materialize_files(tokens, hashlib.sha256(tokens.read_bytes()).hexdigest(), selections)
    for _, destination, _ in selections:
        assert destination.read_bytes() == b"previous validated output"
