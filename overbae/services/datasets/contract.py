"""Technical contracts for chat transcripts, native decisions and evaluation references."""

from __future__ import annotations

import contextlib
import json
import math
import random
import re
from collections import Counter
from typing import Any

import pandas as pd

from modal_shared.decisions import (
    DECISION_OBJECTIVE,
    MEAN_DECISION_OBJECTIVE,
    decision_line,
    decision_reference,
)
from overbae.services.datasets import store
from overbae.services.datasets.examples import identifier_only, normalize_record
from overbae.services.datasets.text import approx_tokens
from overbae.services.finetuning_tool_validation import tool_schema_errors

TRAIN = "train"
EVAL = "eval"
PENDING = "pending"
EXPLORE = "explore"
_LEGACY_INTENT = {"ft": TRAIN, "unstructured": EVAL}
_FAILURE_SAMPLES = 10


def public_intent(value: str | None) -> str:
    """``ft`` → train, ``unstructured`` → eval; leftover rows were never rewritten."""
    raw = (value or "").strip()
    if raw in (TRAIN, EVAL, PENDING, EXPLORE):
        return raw
    return _LEGACY_INTENT.get(raw, PENDING)


def stored_intents(public: str) -> tuple[str, ...]:
    """DB values that count as ``public`` until leftover rows are rewritten."""
    mapped = public_intent(public)
    if mapped == TRAIN:
        return (TRAIN, "ft")
    if mapped == EVAL:
        return (EVAL, "unstructured")
    return (mapped,)


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
        if "decision" in value:
            return "decision"
        if isinstance(value.get("messages"), list):
            return "messages"
        return "object"
    return "text"


def evaluation_generation_error(report: dict) -> str:
    evaluation = report.get("eval", {})
    if evaluation.get("input_type") == "decision" or evaluation.get("input_types", {}).get(
        "decision", 0
    ):
        return (
            "This dataset requires native probability evaluation. "
            "Ordinary chat generation does not produce the required probability vector. "
            "Select this version in the training job’s native evaluation plan."
        )
    return ""


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
    if "decision" in record:
        return decision_line(record)
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


def _train_check(df: pd.DataFrame) -> dict[str, Any]:
    # The validator also imports training_line for the shared export contract.
    from overbae.services.finetuning_validator import validate_rows

    if "decision" in df.columns:
        result = validate_rows(df[["decision"]].to_dict("records"))
        semantics = Counter(
            value.get("target_semantics") or "unspecified_distribution"
            for value in df["decision"]
            if isinstance(value, dict)
        )
        return {
            "ok": result.valid,
            "reason": result.errors[0] if result.errors else "",
            "failures": [{"reason": error} for error in result.errors[:_FAILURE_SAMPLES]],
            "format": "decision",
            "target_semantics": dict(semantics),
            "objective": MEAN_DECISION_OBJECTIVE
            if semantics.get("ordinal_mean")
            else DECISION_OBJECTIVE,
        }
    if "messages" not in df.columns:
        return {"ok": False, "reason": "no messages column", "failures": []}
    columns = [c for c in ("messages", "tools") if c in df.columns]
    rows = []
    failures: list[dict[str, Any]] = []
    for index, record in enumerate(df[columns].to_dict("records")):
        try:
            rows.append(training_line(record))
        except ValueError as exc:
            failures.append({"row": index, "reason": str(exc)})
    if failures:
        reasons = {f["reason"] for f in failures}
        return {
            "ok": False,
            "reason": f"{len(failures)} rows: {reasons.pop()}"
            if len(reasons) == 1
            else f"{len(failures)} rows have no usable messages",
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
    failures = []
    for index, value in enumerate(inputs):
        payload = normalize_record({"input": _as_obj(value)})["input"]
        if isinstance(payload, dict) and "decision" in payload:
            reference = _as_obj(df.iloc[index].get("expected_output"))
            try:
                decision_reference(payload["decision"], reference)
            except (TypeError, ValueError) as exc:
                failures.append({"row": index, "reason": str(exc)})
        if isinstance(payload, dict) and "messages" in payload:
            failures.extend(
                {"row": index, "reason": reason}
                for reason in tool_schema_errors(payload.get("tools"))
            )
    if failures:
        return {
            "ok": False,
            "reason": failures[0]["reason"],
            "failures": failures[:_FAILURE_SAMPLES],
            "has_reference": True,
        }
    return {
        "ok": True,
        "reason": "",
        "input_type": input_type,
        "input_types": dict(types),
        "has_reference": True,
        "reference_rows": reference_rows,
        "model_expected": "model_expected_output" in df.columns,
        "warnings": [
            f"{sum(identifier_only(value) for value in inputs)} eval inputs contain identifiers without evidence; model evaluation cannot retrieve those records."
        ]
        if any(identifier_only(value) for value in inputs)
        else [],
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
    report = {"rows": rows, "train": _train_check(df), "eval": _eval_check(df)}
    if not (report["train"]["ok"] or report["eval"]["ok"]) and not _has_text(df):
        for shape in (report["train"], report["eval"]):
            shape["fixable"] = False
    return report


def measure_path(path):
    result = None
    offset = 0
    types = Counter()
    semantics = Counter()
    references = 0
    for df in store.iter_frames(path):
        part = measure(df)
        semantics.update(part["train"].get("target_semantics", {}))
        evaluation = part["eval"]
        references += evaluation.get("reference_rows", 0)
        types.update(evaluation.get("input_types", {}))
        for intent in ("train", "eval"):
            for failure in part[intent].get("failures", []):
                if "row" in failure:
                    failure["row"] += offset
        if result is None:
            result = part
        else:
            result["rows"] += part["rows"]
            for intent in ("train", "eval"):
                target, incoming = result[intent], part[intent]
                if not incoming["ok"]:
                    if target["ok"] or target.get("reason") == "no expected_output values":
                        target["reason"] = incoming["reason"]
                    target["ok"] = False
                target["failures"] = (target.get("failures", []) + incoming.get("failures", []))[
                    :_FAILURE_SAMPLES
                ]
                target["warnings"] = list(
                    dict.fromkeys([*target.get("warnings", []), *incoming.get("warnings", [])])
                )[:20]
                if incoming.get("fixable") is not False:
                    target.pop("fixable", None)
        offset += len(df)
    if result is None:
        return measure(pd.DataFrame(columns=[c["name"] for c in store.read_manifest(path)]))
    evaluation = result["eval"]
    if semantics:
        result["train"].update(
            target_semantics=dict(semantics),
            objective=MEAN_DECISION_OBJECTIVE
            if semantics.get("ordinal_mean")
            else DECISION_OBJECTIVE,
        )
    if references:
        evaluation["has_reference"] = True
        evaluation["reference_rows"] = references
        if evaluation.get("reason") == "no expected_output values":
            evaluation.update(ok=True, reason="")
    if types:
        evaluation.update(input_types=dict(types), input_type=types.most_common(1)[0][0])
    return result


def _has_text(df: pd.DataFrame) -> bool:
    """Whether any cell holds words or a structure. A table of numbers and ids
    has nothing a cell could shape into messages or an input."""
    for name in df.columns:
        if name == "source_row":
            continue
        for value in df[name].head(200).tolist():
            value = _as_obj(value)
            if isinstance(value, (list, dict)) and value:
                return True
            if isinstance(value, str) and any(ch.isalpha() for ch in value):
                return True
    return False


def stats(df: pd.DataFrame) -> dict[str, Any]:
    """The six numbers training planners read. Input is ``messages`` when
    present, else ``input``; output is ``expected_output``."""
    return stats_rows(df.to_dict(orient="records"), set(df.columns))


def stats_rows(records, columns) -> dict[str, Any]:
    n = 0
    in_col = "messages" if "messages" in columns else "input" if "input" in columns else None
    out_col = "expected_output" if "expected_output" in columns else None
    total_in = total_out = max_tokens = 0
    tool_calling = multi_turn = False
    lengths = []
    rng = random.Random(0)
    if "decision" in columns:
        in_col = "decision"
    for record in records:
        n += 1
        inp = _as_obj(record.get(in_col))
        out = _as_obj(record.get(out_col))
        in_str = (
            "" if _missing(inp) else inp if isinstance(inp, str) else json.dumps(inp, default=str)
        )
        out_str = (
            "" if _missing(out) else out if isinstance(out, str) else json.dumps(out, default=str)
        )
        total_in += len(in_str)
        total_out += len(out_str)
        length = approx_tokens(in_str + out_str)
        max_tokens = max(max_tokens, length)
        if len(lengths) < 10000:
            lengths.append(length)
        else:
            index = rng.randrange(n)
            if index < len(lengths):
                lengths[index] = length
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
        "p95_token_length": sorted(lengths)[math.ceil(len(lengths) * 0.95) - 1] if lengths else 0,
        "length_profile_method": "deterministic_reservoir_10000_character_estimate",
        "avg_input_chars": round(total_in / n) if n else 0,
        "avg_output_chars": round(total_out / n) if n else 0,
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
