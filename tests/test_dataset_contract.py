"""The train contract and the training export agree on every row shape."""

from __future__ import annotations

import pandas as pd
import pytest

from overbae.services.datasets import contract
from overbae.services.datasets.rows import row_from_record
from overbae.services.finetuning_validator import row_to_finetuning_line, validate_rows

TURNS = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
TOOLS = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]

SHAPES = {
    "transcript": {"messages": TURNS},
    "transcript with tools": {"messages": TURNS, "tools": TOOLS},
    "trace row": {"input": "hi", "output": "hello", "messages": TURNS, "trace_id": "t1"},
    "json text": {
        "messages": '[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]'
    },
    "no assistant turn": {"messages": TURNS[:1]},
    "empty transcript": {"messages": []},
    "not a list": {"messages": "hello"},
    "eval row": {"input": "q", "expected_output": "a"},
    "prompt and completion": {"prompt": "q", "completion": "a"},
}


@pytest.mark.parametrize("name", SHAPES)
def test_a_row_fits_train_exactly_when_the_export_trains_on_it(name):
    record = SHAPES[name]
    fits = contract.measure(pd.DataFrame([record]))["train"]["ok"]
    try:
        exported = validate_rows([row_to_finetuning_line(row_from_record(0, record))]).valid
    except ValueError:
        exported = False
    assert fits is exported
    assert fits is (name in {"transcript", "transcript with tools", "trace row", "json text"})


def test_one_bad_row_fails_the_train_contract_and_names_it():
    df = pd.DataFrame([{"messages": TURNS}, {"messages": None}])
    report = contract.measure(df)["train"]
    assert report["ok"] is False
    assert report["failures"] == [{"row": 1, "reason": "messages is empty"}]


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        ([{"question": "q"}], "no input column"),
        (
            [{"input": "q", "expected_output": "a"}, {"input": " ", "expected_output": "a"}],
            "1 rows have an empty input",
        ),
        ([{"input": "q", "expected_output": None}], "no expected_output values"),
        ([{"input": "q"}], "no expected_output values"),
    ],
)
def test_the_eval_contract_says_what_is_missing(rows, reason):
    report = contract.measure(pd.DataFrame(rows))["eval"]
    assert (report["ok"], report["reason"]) == (False, reason)


def test_an_empty_table_fits_nothing():
    report = contract.measure(pd.DataFrame())
    assert report["train"] == report["eval"] == {"ok": False, "reason": "no rows"}


@pytest.mark.parametrize(
    ("rows", "intent"),
    [
        ([{"messages": TURNS}], "train"),
        ([{"input": "q", "expected_output": "a"}], "eval"),
        ([{"question": "q", "answer": "a"}], "eval"),
        ([{"sensor": 1, "temp": 20.5}], "pending"),
    ],
)
def test_the_proposed_intent_follows_the_rows(rows, intent):
    df = pd.DataFrame(rows)
    assert contract.propose_intent(df, contract.measure(df)) == intent


def test_a_table_without_text_is_marked_as_not_fixable():
    numbers = contract.measure(pd.DataFrame([{"sensor": 1, "temp": 20.5}]))
    words = contract.measure(pd.DataFrame([{"prompt": "hi", "completion": "hello"}]))
    assert numbers["train"]["fixable"] is numbers["eval"]["fixable"] is False
    assert "fixable" not in words["train"] and "fixable" not in words["eval"]


def test_a_transcript_that_landing_cut_does_not_fit_train():
    from overbae.services.datasets import land

    huge = [
        {"role": "user", "content": "x" * (land.MAX_CELL_CHARS + 10)},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "f", "arguments": '{"a": "' + "y" * 400_000 + '"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    row = land.bounded_row({"messages": huge})
    report = contract.measure(pd.DataFrame([row, {"messages": TURNS}]))["train"]
    assert report["ok"] is False
    assert report["failures"] == [{"row": 0, "reason": "a value was cut at landing"}]
