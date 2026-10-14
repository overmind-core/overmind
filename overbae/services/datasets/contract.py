"""What a table can be used for, measured from the table itself.

``train`` needs a ``messages`` column whose every row is a valid chat transcript
with an assistant turn. ``eval`` needs an ``input`` column present on every row
plus an ``expected_output`` column with at least one reference. ``decision`` needs
every row to be a decision request with its answers (``datasets.decisions``); such
rows train and evaluate decision models, never chat models.
"""

from __future__ import annotations

import contextlib
import json
import math
import re
from collections import Counter
from typing import Any

from overbae.services.datasets import decisions
from overbae.services.datasets.store import Frame
from overbae.services.datasets.text import approx_tokens
from overbae.services.finetuning_tool_validation import tool_schema_errors
from sandbox.examples import identifier_only, normalize_record

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
    if decisions.is_request(value):
        return "decision"
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


CUT_KEY = "_truncated"
_CUT_TEXT = re.compile(r"…\[\+\d+ chars\]$")


def cut_at_landing(value: Any) -> bool:
    """Whether landing cut any part of ``value``. A cut transcript still has
    a valid shape, so only this keeps it out of training."""
    value = _as_obj(value)
    if isinstance(value, str):
        return bool(_CUT_TEXT.search(value)) or f'"{CUT_KEY}": true' in value
    if isinstance(value, dict):
        return value.get(CUT_KEY) is True or any(cut_at_landing(v) for v in value.values())
    if isinstance(value, list):
        return any(cut_at_landing(v) for v in value)
    return False


def training_line(record: dict[str, Any]) -> dict[str, Any]:
    """The ``{messages, tools?}`` line a record trains as. The train contract
    and the training export both build it here, so neither accepts a row the
    other refuses."""
    record = normalize_record(record)
    messages = record.get("messages")
    if _missing(messages):
        raise ValueError("messages is empty")
    if not isinstance(messages, list):
        raise ValueError("messages is not a list")
    if not any(isinstance(m, dict) and m.get("role") == "assistant" for m in messages):
        raise ValueError("no assistant turn to train on")
    if cut_at_landing(messages):
        raise ValueError("a value was cut at landing")
    line: dict[str, Any] = {"messages": messages}
    tools = record.get("tools")
    errors = tool_schema_errors(tools)
    if errors:
        raise ValueError("; ".join(errors[:3]))
    if tools:
        line["tools"] = tools
    return line


def _holds_decision_rows(frame: Frame) -> bool:
    return "input" in frame and any(decisions.is_request(v) for v in frame.values("input"))


def _train_check(frame: Frame) -> dict[str, Any]:
    # The validator also imports training_line for the shared export contract.
    from overbae.services.finetuning_validator import RowValidator

    if _holds_decision_rows(frame):
        return {
            "ok": False,
            "reason": "decision rows train decision models, not chat models",
            "failures": [],
        }
    if "messages" not in frame:
        return {"ok": False, "reason": "no messages column", "failures": []}
    validator = RowValidator()
    failed = 0
    failures: list[dict[str, Any]] = []
    reasons: set[str] = set()
    for index, record in enumerate(frame.records(["messages", "tools"]), start=1):
        try:
            line = training_line(record)
        except ValueError as exc:
            failed += 1
            reasons.add(str(exc))
            if len(failures) < _FAILURE_SAMPLES:
                failures.append({"row": index, "reason": str(exc)})
            continue
        if not failed:
            validator.add(line)
    if failed:
        return {
            "ok": False,
            "reason": f"{failed} rows: {reasons.pop()}"
            if len(reasons) == 1
            else f"{failed} rows have no usable messages",
            "failures": failures,
        }
    result = validator.result()
    return {
        "ok": result.valid,
        "reason": "" if result.valid else (result.errors[0] if result.errors else "invalid"),
        "failures": [{"reason": e} for e in result.errors[:_FAILURE_SAMPLES]],
        "warnings": result.warnings,
        "format": result.format,
    }


def _eval_check(frame: Frame) -> dict[str, Any]:
    if "input" not in frame:
        return {"ok": False, "reason": "no input column", "has_reference": False}
    empty = 0
    empty_rows: list[int] = []
    types: Counter[str] = Counter()
    failures: list[dict[str, Any]] = []
    failed = 0
    identifiers = 0
    for index, value in enumerate(frame.values("input"), start=1):
        if _missing(value):
            empty += 1
            if len(empty_rows) < _FAILURE_SAMPLES:
                empty_rows.append(index)
            continue
        types[eval_input_type(value)] += 1
        identifiers += identifier_only(value)
        payload = normalize_record({"input": value})["input"]
        if isinstance(payload, dict) and "messages" in payload:
            for reason in tool_schema_errors(payload.get("tools")):
                failed += 1
                if len(failures) < _FAILURE_SAMPLES:
                    failures.append({"row": index, "reason": reason})
    if empty:
        return {
            "ok": False,
            "reason": f"{empty} rows have an empty input",
            "failures": [{"row": i, "reason": "input is empty"} for i in empty_rows],
            "has_reference": False,
        }
    if types["decision"]:
        return {
            "ok": False,
            "reason": "decision rows evaluate decision models, not chat models",
            "input_type": "decision",
            "has_reference": True,
        }
    input_type = types.most_common(1)[0][0] if types else "text"
    reference_rows = (
        sum(1 for v in frame.values("expected_output") if not _missing(v))
        if "expected_output" in frame
        else 0
    )
    if not reference_rows:
        return {
            "ok": False,
            "reason": "no expected_output values",
            "input_type": input_type,
            "has_reference": False,
        }
    if failed:
        return {
            "ok": False,
            "reason": failures[0]["reason"],
            "failures": failures,
            "has_reference": True,
        }
    return {
        "ok": True,
        "reason": "",
        "input_type": input_type,
        "input_types": dict(types),
        "has_reference": True,
        "reference_rows": reference_rows,
        "model_expected": "model_expected_output" in frame,
        "warnings": [
            f"{identifiers} eval inputs contain identifiers without evidence; model evaluation cannot retrieve those records."
        ]
        if identifiers
        else [],
    }


def measure(frame: Frame) -> dict[str, Any]:
    """``{rows, train: {ok, reason, …}, eval: {ok, reason, …}}``."""
    rows = len(frame)
    if rows == 0:
        return {
            "rows": 0,
            "train": {"ok": False, "reason": "no rows"},
            "eval": {"ok": False, "reason": "no rows"},
            "decision": {"ok": False, "reason": "no rows"},
        }
    report = {
        "rows": rows,
        "train": _train_check(frame),
        "eval": _eval_check(frame),
        "decision": _decision_check(frame),
    }
    if not (report["train"]["ok"] or report["eval"]["ok"]) and not _has_text(frame):
        for shape in (report["train"], report["eval"]):
            shape["fixable"] = False
    return report


def _decision_check(frame: Frame) -> dict[str, Any]:
    if "input" not in frame or "expected_output" not in frame:
        return {"ok": False, "reason": "no decision rows"}
    failures: list[dict[str, Any]] = []
    failed = decision_rows = 0
    shapes: list[dict[str, int]] = []
    coverage = decisions.Coverage()
    for index, record in enumerate(frame.records(["input", "expected_output"]), start=1):
        if decisions.is_request(record.get("input")):
            decision_rows += 1
        try:
            parsed = decisions.record(record.get("input"), record.get("expected_output"))
        except decisions.DecisionRowError as exc:
            failed += 1
            if len(failures) < _FAILURE_SAMPLES:
                failures.append({"row": index, "reason": str(exc)})
        else:
            shapes.append(decisions.shape(parsed["questions"]))
            coverage.add(parsed)
    if not decision_rows:
        return {"ok": False, "reason": "no decision rows"}
    if failed:
        first = failures[0]
        return {
            "ok": False,
            "reason": f"{failed} rows are not valid decisions; row {first['row']}: {first['reason']}",
            "failures": failures,
        }
    return {
        "ok": True,
        "reason": "",
        "rows": decision_rows,
        "shape": decisions.widest(shapes),
        "warnings": coverage.warnings(),
    }


def _has_text(frame: Frame) -> bool:
    """Whether any cell holds words or a structure. A table of numbers and ids
    has nothing a cell could shape into messages or an input."""
    head = frame.head(200, [c for c in frame.columns if c != "source_row"])
    for record in head:
        for value in record.values():
            value = _as_obj(value)
            if isinstance(value, (list, dict)) and value:
                return True
            if isinstance(value, str) and any(ch.isalpha() for ch in value):
                return True
    return False


def propose_intent(frame: Frame, report: dict[str, Any]) -> str:
    """What the rows look like: a transcript table is ``train``, an input with a
    reference is ``eval``, decision rows are ``train`` (they mostly train a decision
    model), anything else stays ``pending``."""
    if (report.get("train") or {}).get("ok") or (report.get("decision") or {}).get("ok"):
        return TRAIN
    if (report.get("eval") or {}).get("ok"):
        return EVAL
    columns = set(frame.columns)
    if "messages" in columns:
        assistant = 0
        for record in frame.head(200, ["messages"]):
            value = _as_obj(record["messages"])
            if isinstance(value, list) and any(
                isinstance(m, dict) and m.get("role") == "assistant" for m in value
            ):
                assistant += 1
        if assistant >= max(1, min(200, len(frame)) // 2):
            return TRAIN
    if {"input", "expected_output"} <= columns or {"question", "answer"} <= columns:
        return EVAL
    return PENDING


def stats(frame: Frame) -> dict[str, Any]:
    """The six numbers training planners read. Input is ``messages`` when
    present, else ``input``; output is ``expected_output``."""
    n = len(frame)
    if n == 0:
        return {
            "num_examples": 0,
            "has_tool_calling": False,
            "has_multi_turn_tool_calls": False,
            "max_token_length": 0,
            "avg_input_chars": 0,
            "avg_output_chars": 0,
        }
    in_col = "messages" if "messages" in frame else "input" if "input" in frame else None
    out_col = "expected_output" if "expected_output" in frame else None
    total_in = total_out = max_tokens = 0
    tool_calling = multi_turn = False
    for record in frame.records([c for c in (in_col, out_col) if c]):
        inp = _as_obj(record.get(in_col) if in_col else None)
        out = _as_obj(record.get(out_col) if out_col else None)
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


_LABEL_CHARS = 120
_MAX_LABELS = 50
_SHOWN_LABELS = 20
_MAX_LABEL_FIELDS = 20
_MAX_INPUT_FIELDS = 100
_LISTED_FIELDS = 50


def _label(value: Any) -> str | None:
    """``value`` as a class label, or None when it is not one. A ``{option: weight}``
    answer counts as its heaviest option."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        return text if 0 < len(text) <= _LABEL_CHARS else None
    if (
        isinstance(value, dict)
        and value
        and all(isinstance(w, (int, float)) and not isinstance(w, bool) for w in value.values())
    ):
        return _label(str(max(value, key=lambda option: value[option])))
    return None


def _label_balance(frame: Frame) -> list[dict[str, Any]]:
    """Label counts per categorical field of ``expected_output``: the value itself, or
    each key of an object (a decision row's question keys). A field stops counting once
    it shows free text or more than ``_MAX_LABELS`` labels."""
    if "expected_output" not in frame:
        return []
    fields: dict[str, Counter[str] | None] = {}
    for value in frame.values("expected_output"):
        value = _as_obj(value)
        if _missing(value):
            continue
        items = (
            [(f"expected_output.{key}", item) for key, item in value.items()]
            if isinstance(value, dict) and _label(value) is None
            else [("expected_output", value)]
        )
        for name, item in items:
            if name not in fields and len(fields) >= _MAX_LABEL_FIELDS:
                continue
            counts = fields.setdefault(name, Counter())
            if counts is None or _missing(item):
                continue
            label = _label(item)
            if label is None or (label not in counts and len(counts) >= _MAX_LABELS):
                fields[name] = None
                continue
            counts[label] += 1
    report = []
    for name, counts in fields.items():
        labelled = sum(counts.values()) if counts else 0
        if not counts or labelled < 2 * len(counts):
            continue
        ranked = counts.most_common()
        report.append(
            {
                "field": name,
                "rows": labelled,
                "labels": len(ranked),
                "classes": [{"label": k, "rows": v} for k, v in ranked[:_SHOWN_LABELS]],
                "majority": {"label": ranked[0][0], "share": round(ranked[0][1] / labelled, 4)},
            }
        )
    return report


def _input_fields(frame: Frame, column_stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Columns, and the keys of an object ``input`` (a decision row's state), that are
    empty on some rows, or constant: one value on every non-empty row, with at least two
    such rows. A field empty on every row is only empty."""
    rows = len(frame)
    empty: list[dict[str, Any]] = []
    constant: list[dict[str, Any]] = []
    checked = 0
    for stat in column_stats:
        name = stat["name"]
        if name in ("source_row", "expected_output") or name.startswith("_overmind_"):
            continue
        checked += 1
        missing = round(stat["null_rate"] * rows)
        if missing:
            empty.append({"field": name, "rows": missing, "of": rows})
        if rows - missing > 1 and stat["distinct"] == 1:
            constant.append({"field": name, "rows": rows - missing, "of": rows})
    # name -> [rows holding the key, rows where it is empty, first value, varies]
    keys: dict[str, list[Any]] = {}
    objects = 0
    if "input" in frame:
        for value in frame.values("input"):
            value = _as_obj(value)
            if decisions.is_request(value):
                value = _as_obj(value.get("state"))
            if not isinstance(value, dict):
                continue
            objects += 1
            for key, item in value.items():
                name = f"input.{key}"
                if name not in keys and len(keys) >= _MAX_INPUT_FIELDS:
                    continue
                seen = keys.setdefault(name, [0, 0, None, False])
                seen[0] += 1
                if _missing(item):
                    seen[1] += 1
                    continue
                digest = json.dumps(item, sort_keys=True, default=str)[:4096]
                if seen[2] is None:
                    seen[2] = digest
                elif digest != seen[2]:
                    seen[3] = True
    for name, (present, blank, _first, varies) in keys.items():
        checked += 1
        missing = objects - present + blank
        if missing:
            empty.append({"field": name, "rows": missing, "of": objects})
        if present - blank > 1 and not varies:
            constant.append({"field": name, "rows": present - blank, "of": objects})
    return {
        "fields": checked,
        "empty": empty[:_LISTED_FIELDS],
        "constant": constant[:_LISTED_FIELDS],
    }


def analytics(frame: Frame, column_stats: list[dict[str, Any]]) -> dict[str, Any]:
    """Label balance with its majority baseline wherever ``expected_output`` is
    categorical, and the input fields that are empty or constant."""
    return {"labels": _label_balance(frame), "inputs": _input_fields(frame, column_stats)}
