"""What a table can be used for, measured from the table itself.

``train`` needs a ``messages`` column whose every row is a valid chat transcript
with an assistant turn. ``eval`` needs an ``input`` column present on every row
plus an ``expected_output`` column with at least one reference.
"""

from __future__ import annotations

import contextlib
import json
import math
from collections import Counter
from typing import Any

import pandas as pd

from overbae.services.datasets.text import approx_tokens

TRAIN = "train"
EVAL = "eval"
PENDING = "pending"
_LEGACY_INTENT = {"ft": TRAIN, "unstructured": EVAL}
_FAILURE_SAMPLES = 10


def public_intent(value: str | None) -> str:
    """``ft`` → train, ``unstructured`` → eval; leftover rows were never rewritten."""
    raw = (value or "").strip()
    if raw in (TRAIN, EVAL, PENDING):
        return raw
    return _LEGACY_INTENT.get(raw, PENDING)


def stored_intents(public: str) -> tuple[str, ...]:
    """DB values that count as ``public`` until leftover rows are rewritten."""
    mapped = public_intent(public)
    if mapped == TRAIN:
        return (TRAIN, "ft")
    if mapped == EVAL:
        return (EVAL, "unstructured")
    return (PENDING,)


def _missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def _as_obj(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            with contextlib.suppress(ValueError):
                return json.loads(text)
    return value


def eval_input_type(value: Any) -> str:
    value = _as_obj(value)
    if (
        isinstance(value, list)
        and value
        and all(isinstance(m, dict) and "role" in m for m in value)
    ):
        return "messages"
    if isinstance(value, dict):
        if isinstance(value.get("messages"), list):
            return "messages"
        return "object"
    return "text"


def _train_check(df: pd.DataFrame) -> dict[str, Any]:
    from overbae.services.finetuning_validator import validate_rows

    if "messages" not in df.columns:
        return {"ok": False, "reason": "no messages column", "failures": []}
    rows = []
    failures: list[dict[str, Any]] = []
    for index, raw in enumerate(df["messages"].tolist()):
        value = _as_obj(raw)
        if _missing(value):
            failures.append({"row": index, "reason": "messages is empty"})
            continue
        if not isinstance(value, list):
            failures.append({"row": index, "reason": "messages is not a list"})
            continue
        row = {"messages": value}
        if "tools" in df.columns:
            tools = _as_obj(df["tools"].iloc[index])
            if isinstance(tools, list) and tools:
                row["tools"] = tools
        rows.append(row)
    if failures:
        return {
            "ok": False,
            "reason": f"{len(failures)} rows have no usable messages",
            "failures": failures[:_FAILURE_SAMPLES],
        }
    result = validate_rows(rows)
    return {
        "ok": result.valid,
        "reason": "" if result.valid else (result.errors[0] if result.errors else "invalid"),
        "failures": [{"reason": e} for e in result.errors[:_FAILURE_SAMPLES]],
        "warnings": result.warnings,
        "format": result.format,
    }


def _eval_check(df: pd.DataFrame) -> dict[str, Any]:
    if "input" not in df.columns:
        return {"ok": False, "reason": "no input column", "has_reference": False}
    inputs = df["input"].tolist()
    empty = [i for i, v in enumerate(inputs) if _missing(v)]
    if empty:
        return {
            "ok": False,
            "reason": f"{len(empty)} rows have an empty input",
            "failures": [{"row": i, "reason": "input is empty"} for i in empty[:_FAILURE_SAMPLES]],
            "has_reference": False,
        }
    types = Counter(eval_input_type(v) for v in inputs)
    input_type = types.most_common(1)[0][0] if types else "text"
    reference_rows = 0
    if "expected_output" in df.columns:
        reference_rows = sum(1 for v in df["expected_output"].tolist() if not _missing(v))
    if not reference_rows:
        return {
            "ok": False,
            "reason": "no expected_output values",
            "input_type": input_type,
            "has_reference": False,
        }
    return {
        "ok": True,
        "reason": "",
        "input_type": input_type,
        "input_types": dict(types),
        "has_reference": True,
        "reference_rows": reference_rows,
        "model_expected": "model_expected_output" in df.columns,
    }


def measure(df: pd.DataFrame) -> dict[str, Any]:
    """``{rows, train: {ok, reason, …}, eval: {ok, reason, …}}``."""
    rows = int(len(df))
    if rows == 0:
        return {
            "rows": 0,
            "train": {"ok": False, "reason": "no rows"},
            "eval": {"ok": False, "reason": "no rows"},
        }
    return {"rows": rows, "train": _train_check(df), "eval": _eval_check(df)}


def propose_intent(df: pd.DataFrame, report: dict[str, Any]) -> str:
    """What the rows look like: a transcript table is ``train``, an input with a
    reference is ``eval``, anything else stays ``pending``."""
    if (report.get("train") or {}).get("ok"):
        return TRAIN
    if (report.get("eval") or {}).get("ok"):
        return EVAL
    columns = set(df.columns)
    if "messages" in columns:
        assistant = 0
        for value in df["messages"].head(200).tolist():
            value = _as_obj(value)
            if isinstance(value, list) and any(
                isinstance(m, dict) and m.get("role") == "assistant" for m in value
            ):
                assistant += 1
        if assistant >= max(1, min(200, len(df)) // 2):
            return TRAIN
    if {"input", "expected_output"} <= columns or {"question", "answer"} <= columns:
        return EVAL
    return PENDING


def stats(df: pd.DataFrame) -> dict[str, Any]:
    """The six numbers training planners read. Input is ``messages`` when
    present, else ``input``; output is ``expected_output``."""
    n = int(len(df))
    if n == 0:
        return {
            "num_examples": 0,
            "has_tool_calling": False,
            "has_multi_turn_tool_calls": False,
            "max_token_length": 0,
            "avg_input_chars": 0,
            "avg_output_chars": 0,
        }
    in_col = "messages" if "messages" in df.columns else "input" if "input" in df.columns else None
    out_col = "expected_output" if "expected_output" in df.columns else None
    total_in = total_out = max_tokens = 0
    tool_calling = multi_turn = False
    ins = df[in_col].tolist() if in_col else [None] * n
    outs = df[out_col].tolist() if out_col else [None] * n
    for raw_in, raw_out in zip(ins, outs, strict=True):
        inp = _as_obj(raw_in)
        out = _as_obj(raw_out)
        in_str = (
            "" if _missing(inp) else inp if isinstance(inp, str) else json.dumps(inp, default=str)
        )
        out_str = (
            "" if _missing(out) else out if isinstance(out, str) else json.dumps(out, default=str)
        )
        total_in += len(in_str)
        total_out += len(out_str)
        max_tokens = max(max_tokens, approx_tokens(in_str + out_str))
        turns = _tool_call_turns(inp)
        if turns:
            tool_calling = True
        if turns > 1:
            multi_turn = True
    return {
        "num_examples": n,
        "has_tool_calling": tool_calling,
        "has_multi_turn_tool_calls": multi_turn,
        "max_token_length": max_tokens,
        "avg_input_chars": round(total_in / n),
        "avg_output_chars": round(total_out / n),
    }


def _tool_call_turns(inp: Any) -> int:
    messages = (
        inp if isinstance(inp, list) else inp.get("messages") if isinstance(inp, dict) else None
    )
    if not isinstance(messages, list):
        return 0
    return sum(
        1
        for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )
