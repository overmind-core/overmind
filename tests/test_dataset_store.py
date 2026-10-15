from __future__ import annotations

import datetime as dt
import io
import pathlib

import pytest

from overbae.services.datasets import files, store

ROWS = [
    {
        "source_row": 0,
        "input": {"q": "hi"},
        "expected_output": "hello",
        "score": 0.9,
        "ok": True,
        "when": dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
        "messages": [{"role": "user", "content": "hi"}],
    },
    {"source_row": 1, "input": {"q": "bye"}, "expected_output": None, "score": None, "ok": False},
    {"source_row": 2, "input": "plain", "expected_output": "x", "score": 3, "ok": None},
]


@pytest.fixture
def table(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "s.parquet"
    store.write_rows(path, ROWS)
    return path


def test_manifest_infers_column_kinds(table):
    kinds = {c["name"]: c["type"] for c in store.read_manifest(table)}
    assert kinds == {
        "source_row": "integer",
        "input": "json",
        "expected_output": "string",
        "score": "number",
        "ok": "boolean",
        "when": "datetime",
        "messages": "json",
    }


def test_values_a_typed_column_cannot_hold_are_kept_as_json(tmp_path):
    columns = {
        "huge": [2**63, -(2**63) - 1, 3],
        "mixed": [1, "a", True],
        "wide": [2**53 + 1, 0.5, None],
        "near": [2**53, 0.5, 1],
    }
    rows = [{name: values[i] for name, values in columns.items()} for i in range(3)]
    path = tmp_path / "wide.parquet"
    store.write_rows(path, rows)
    kinds = {column["name"]: column["type"] for column in store.read_manifest(path)}
    assert kinds == {"huge": "json", "mixed": "json", "wide": "json", "near": "number"}
    retained = list(store.iter_rows(path))
    for name, values in columns.items():
        assert [row[name] for row in retained] == values


def test_frames_decode_json_columns(table):
    frame = store.read_frame(table)
    assert list(frame["input"]) == [{"q": "hi"}, {"q": "bye"}, "plain"]
    messages = list(frame["messages"])
    assert messages[0] == [{"content": "hi", "role": "user"}]
    assert messages[1] is None


def test_page_sorts_filters_and_indexes(table):
    page = store.page(
        table,
        limit=2,
        sort="score",
        direction="desc",
        filters=[{"field": "expected_output", "op": "not_empty"}],
    )
    assert page["total"] == 2
    assert [(r["_index"], r["score"]) for r in page["rows"]] == [(2, 3.0), (0, 0.9)]
    assert store.page(table, search="bye")["total"] == 1
    with pytest.raises(store.StoreError):
        store.page(table, sort="nope")
    with pytest.raises(store.StoreError):
        store.page(table, filters=[{"field": "score", "op": "regex", "value": "x"}])


def test_column_stats_and_rows_by_index(table):
    stats = {c["name"]: c for c in store.column_stats(table)}
    assert stats["expected_output"]["null_rate"] == pytest.approx(1 / 3)
    assert stats["score"]["max"] == 3.0
    assert store.read_row(table, 2)["input"] == "plain"
    assert store.read_row(table, 9) is None
    assert [r["source_row"] for r in store.read_rows(table, [2, 0])] == [2, 0]


def test_query_is_sandboxed(table):
    result = store.query("select source_row, json_extract_string(input, '$.q') q from t", t=table)
    assert result["rows"][0] == {"source_row": 0, "q": "hi"}
    result = store.query("select input, input -> '$.q' AS q, expected_output from t", t=table)
    assert result["rows"][0] == {"input": {"q": "hi"}, "q": "hi", "expected_output": "hello"}
    assert result["rows"][2]["input"] == "plain"
    for sql in (
        "select * from read_csv('/etc/passwd')",
        "copy (select 1) to '/tmp/out.csv'",
        "attach '/tmp/other.db'",
        "install httpfs",
    ):
        with pytest.raises(Exception, match="Permission|Only one read-only SELECT"):
            store.query(sql, limit=None, t=table)
    with pytest.raises(Exception, match="configuration|Only one read-only SELECT"):
        store.query("set enable_external_access = true", limit=None, t=table)


def test_write_frame_round_trips_and_hashes(table, tmp_path):
    df = store.read_frame(table).assign(n=[1, 2, 3])
    out = tmp_path / "p.parquet"
    manifest = store.write_frame(out, df)
    assert {c["name"]: c["type"] for c in manifest}["n"] == "integer"
    assert store.row_count(out) == 3
    first = store.file_sha256(out)
    store.write_frame(out, df)
    assert store.file_sha256(out) == first


def test_csv_numbers_become_numeric(tmp_path):
    path = tmp_path / "rows.csv"
    path.write_text("name,count,score,note\nA,1,1.5,x\nB,2,2.5,\n", encoding="utf-8")
    rows = list(files.FileRows(path, filename="rows.csv"))
    assert rows == [
        {"name": "A", "count": 1, "score": 1.5, "note": "x"},
        {"name": "B", "count": 2, "score": 2.5, "note": ""},
    ]


def test_jsonl_and_json_wrappers(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text('{"a": 1}\n\n{"a": {"b": 2}}\n', encoding="utf-8")
    assert list(files.FileRows(path, filename="rows.jsonl")) == [{"a": 1}, {"a": {"b": 2}}]
    path = tmp_path / "rows.json"
    path.write_text('{"data": [{"a": 1}, 2]}', encoding="utf-8")
    assert list(files.FileRows(path, filename="rows.json")) == [{"a": 1}, {"value": 2}]
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(files.FileError, match="Line 1"):
        files.FileRows(bad, filename="bad.jsonl")


def test_paste_detects_shape():
    assert files.parse_text('{"a": 1}\n{"a": 2}') == [{"a": 1}, {"a": 2}]
    assert files.parse_text("a,b\n1,x") == [{"a": 1, "b": "x"}]
    assert files.parse_text('[{"a": 1}]') == [{"a": 1}]
    with pytest.raises(files.FileError):
        files.parse_text("   ")


def test_stream_rows_rejects_unknown_extension():
    with pytest.raises(files.FileError, match="Use a CSV"):
        list(files.iter_stream_rows(io.StringIO("x"), filename="rows.xlsx"))


def test_query_exposes_declared_nested_columns_as_json(tmp_path):
    path = tmp_path / "nested.parquet"
    store.write_rows(
        path,
        [
            {
                "source_row": 0,
                "decision": {"kind": "choice", "target": [0.25, 0.75]},
                "literal": '{"kind":"text"}',
            },
            {
                "source_row": 1,
                "decision": {"kind": "score", "target": [1.0, 0.0]},
                "literal": "plain",
            },
        ],
    )
    result = store.query(
        "SELECT json_extract_string(decision.kind, '$') AS kind, "
        "CAST(decision.target AS DOUBLE[]) AS probabilities, typeof(literal) AS literal_type "
        "FROM t ORDER BY source_row",
        t=path,
    )
    assert result["rows"] == [
        {"kind": "choice", "probabilities": [0.25, 0.75], "literal_type": "VARCHAR"},
        {"kind": "score", "probabilities": [1.0, 0.0], "literal_type": "VARCHAR"},
    ]
