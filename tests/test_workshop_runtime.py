import json

import pytest

from overbae.services.datasets import store
from overbae.services.datasets.row_values import normalize_json_rows
from workshop_runtime.data import read_frame, records, stage, validate
from workshop_runtime.observations import observation
from workshop_runtime.replay import canonical, read_rows, write_rows

pytestmark = pytest.mark.covers("work-once")


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_jsonl_preserves_unicode_line_separators_in_training_text(tmp_path, newline):
    rows = [
        {
            "source_row": index,
            "messages": [{"role": "assistant", "content": text}],
        }
        for index, text in enumerate(["First\u0085next\u2028line\u2029last", "Second\nanswer"])
    ]
    source, retained = tmp_path / "source.jsonl", tmp_path / "retained.jsonl"
    source.write_bytes(
        (newline.join(json.dumps(row, ensure_ascii=False) for row in rows) + newline).encode()
    )
    assert records(read_frame(source)) == rows
    assert read_rows(source) == rows
    write_rows(retained, rows)
    assert read_rows(retained) == rows
    assert records(read_frame(retained)) == rows


def test_replay_step_values_match_published_table_with_sparse_and_mixed_columns(tmp_path):
    records = [
        {
            "source_row": 10,
            "status": "eligible",
            "detail": {"value": 3},
            "amount": 2,
            "wide": 2**53 + 1,
            "mixed": True,
        },
        {"source_row": 11, "status": "excluded", "amount": 2.5, "wide": 0.5, "mixed": 7},
        {
            "source_row": 12,
            "status": "eligible",
            "detail": None,
            "amount": None,
            "wide": None,
            "mixed": "7",
        },
    ]
    table = tmp_path / "intermediate.parquet"
    store.write_rows(table, records)
    materialized = list(store.iter_rows(table))
    assert canonical(records) != canonical(materialized)
    assert canonical(normalize_json_rows(records)) == canonical(materialized)
    assert normalize_json_rows(records)[1]["detail"] is None
    assert normalize_json_rows(records)[0]["wide"] == 2**53 + 1


def test_staging_preserves_numeric_supervision_and_rejects_lost_lineage(tmp_path):
    source = tmp_path / "source.jsonl"
    output = tmp_path / "input.jsonl"
    rows = [
        {
            "source_row": 13,
            "weight": 0.12345678901234568,
            "identifier": 9007199254740993,
            "_overmind_provenance": {"source": "retained"},
            "_overmind_secret": "platform-only",
            "_overmind_parent_rows": [13],
        },
        {
            "source_row": 27,
            "weight": None,
            "identifier": "different",
            "_overmind_parent_rows": None,
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows))
    stage(source, output)
    staged = [json.loads(line) for line in output.read_text().splitlines()]
    assert staged == [
        {
            key: value
            for key, value in row.items()
            if not key.startswith("_overmind_") or key == "_overmind_parent_rows"
        }
        for row in rows
    ]
    assert validate(source, output)["lineage"] == "preserved"
    staged[1]["source_row"] = 13
    output.write_text("\n".join(json.dumps(row) for row in staged))
    with pytest.raises(ValueError, match="each source identity"):
        validate(source, output)


def test_staging_parquet_preserves_nullable_integer_precision_and_nested_values(tmp_path):
    source, output = tmp_path / "source.parquet", tmp_path / "input.jsonl"
    records = [
        {
            "source_row": 0,
            "identifier": 2**53 + 1,
            "wide": 2**63 + 1,
            "nested": {"ids": [2**53 + 1, None]},
        },
        {"source_row": 1, "identifier": None, "wide": None, "nested": None},
    ]
    store.write_rows(source, records)
    stage(source, output)
    assert [json.loads(line) for line in output.read_text().splitlines()] == records


def test_compact_observations_preserve_failures_receipts_and_explicit_read_pages():
    result = {
        "id": "retained-replay",
        "state": "failed",
        "error": "validation failed",
        "steps": [{"output_sha256": "source-digest", "samples": list(range(1000))}],
    }
    reduced = observation(result, "receipts/000001.json", tool="replay")
    assert reduced["error"] == result["error"]
    assert reduced["steps"][0]["output_sha256"] == "source-digest"
    assert reduced["steps"][0]["samples"]["omitted_items"] == 988
    page = {"text": "x" * 32000, "next_offset": 32000}
    assert observation(page, "receipt.json", tool="read")["text"] == page["text"]
    raw = '{"finding": "a real observation"}'
    assert observation({"output": raw}, "receipt.json", tool="python")["output"] == raw
