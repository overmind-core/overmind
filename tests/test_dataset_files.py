"""Files land unaltered or are refused with a message the user can act on."""

from __future__ import annotations

import pytest

from overbae.services.datasets import files


def _read(tmp_path, name: str, body: str | bytes):
    path = tmp_path / name
    path.write_bytes(body if isinstance(body, bytes) else body.encode())
    return files.read_file_rows(path, filename=name)


@pytest.mark.parametrize(
    ("name", "body", "message"),
    [
        (
            "ragged.csv",
            "input,expected_output\nHello, world,The answer\n",
            "Row 2 has 3 cells; the header has 2",
        ),
        ("twice.csv", "a,A\n1,2\n", "Column 'A' appears twice"),
        ("latin.csv", "input\ncaf\xe9\n".encode("latin-1"), "not UTF-8"),
        ("empty.csv", "", "no header row"),
        ("bad.jsonl", '{"input": "a"}\n{oops\n', "Line 2 is not valid JSON"),
        ("bad.json", "{oops", "Not valid JSON"),
        ("scalar.json", "3", "object or an array"),
        ("fake.parquet", "not parquet", "not readable Parquet"),
        ("fake.csv.gz", "not gzip", "not readable gzip"),
        ("notes.txt", "hello", "Use a CSV, TSV, JSON, JSONL or Parquet file."),
    ],
)
def test_a_bad_file_is_refused_in_words(tmp_path, name, body, message):
    with pytest.raises(files.FileError, match=message):
        _read(tmp_path, name, body)


def test_csv_text_that_is_not_exactly_a_number_stays_text(tmp_path):
    body = "id,answer,big,sep,count,score\n007,18,1234567890123456789,1_000,1,1.5\n008,3.5,2,2,2,2.25\n"
    assert _read(tmp_path, "rows.csv", body) == [
        {
            "id": "007",
            "answer": "18",
            "big": "1234567890123456789",
            "sep": "1_000",
            "count": 1,
            "score": 1.5,
        },
        {"id": "008", "answer": "3.5", "big": "2", "sep": "2", "count": 2, "score": 2.25},
    ]


def test_a_blank_header_gets_a_positional_name_and_names_are_stripped(tmp_path):
    assert _read(tmp_path, "index.csv", ", input \n0,a\n") == [{"column_1": 0, "input": "a"}]


def test_a_short_row_is_padded_and_a_long_cell_lands(tmp_path):
    long = "x" * 200_000
    rows = _read(tmp_path, "rows.csv", f'input,expected_output\n"{long}"\n')
    assert rows == [{"input": long, "expected_output": None}]


def test_json_lines_under_a_json_name_land(tmp_path):
    assert _read(tmp_path, "rows.json", '{"input": "a"}\n{"input": "b"}\n') == [
        {"input": "a"},
        {"input": "b"},
    ]


@pytest.mark.parametrize(
    ("text", "rows"),
    [
        ('[{"a": 1}]', [{"a": 1}]),
        ('{"a": 1}\n{"a": 2}', [{"a": 1}, {"a": 2}]),
        ('{\n  "a": 1\n}', [{"a": 1}]),
        ("a,b\n1,x", [{"a": 1, "b": "x"}]),
    ],
)
def test_pasted_text_is_read_by_its_shape(text, rows):
    assert files.parse_text(text) == rows


def test_upload_names_are_checked_before_any_byte_is_stored(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    with pytest.raises(files.FileError, match="already compressed"):
        files.begin_upload("rows.parquet.gz")
    with pytest.raises(files.FileError, match="Use a CSV"):
        files.begin_upload("rows.xlsx")
    upload_id, name = files.begin_upload("rows.jsonl.gz")
    assert name == "rows.jsonl.gz" and files.upload_received(upload_id) == 0
