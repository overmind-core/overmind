"""How a table relates to the project's capabilities: the row-level contract
against one capability, and the rank that proposes one at landing. Exact
counts, no LLM."""

from __future__ import annotations

import contextlib
import json
import math
from collections import Counter
from typing import Any

import pandas as pd


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value == ""


def _card(capability: Any) -> dict[str, Any]:
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    card = meta.get("capability_card")
    return card if isinstance(card, dict) else {}


def _system_prompt(capability: Any) -> str:
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    return str(_card(capability).get("system_prompt") or meta.get("system_prompt") or "").strip()


def _tools(capability: Any) -> set[str]:
    return {
        str(t.get("name") if isinstance(t, dict) else t)
        for t in (_card(capability).get("tool_spec") or [])
    } - {""}


def _schema(capability: Any) -> Any:
    return _card(capability).get("input_schema") or getattr(capability, "input_schema", None)


def _schema_keys(schema: Any) -> list[str]:
    if isinstance(schema, dict):
        props = schema.get("properties")
        if isinstance(props, dict):
            return list(props)
        return [k for k in schema if not str(k).startswith("$")]
    return []


def _required_keys(schema: Any) -> list[str]:
    """Keys marked required, in either schema dialect: JSON Schema's ``required``
    list, or the sync card's ``{key: {required: true}}``."""
    if not isinstance(schema, dict):
        return []
    if isinstance(schema.get("required"), list):
        return [str(k) for k in schema["required"]]
    return [k for k, v in schema.items() if isinstance(v, dict) and v.get("required") is True]


def _as_object(value: Any) -> Any:
    if isinstance(value, str) and value[:1] in "{[":
        with contextlib.suppress(ValueError):
            return json.loads(value)
    return value


def capability_contract(capability: Any, df: pd.DataFrame, intent: str) -> dict[str, Any]:
    """Row by row, does the table belong to this capability? eval: every ``input``
    carries the required input keys. train: every transcript is the capability's
    own — its system turn, its tools. Vacuous when nothing is declared."""
    rows = int(len(df))
    if intent == "eval":
        required = _required_keys(_schema(capability))
        if not required:
            return {"ok": True, "rows": rows, "rows_ok": rows, "reason": "no input schema declared"}
        if "input" not in df.columns:
            return {"ok": False, "rows": rows, "rows_ok": 0, "reason": "no input column"}
        missing: Counter[str] = Counter()
        ok = 0
        for value in df["input"].tolist():
            value = _as_object(value)
            absent = [k for k in required if not isinstance(value, dict) or _missing(value.get(k))]
            if absent:
                missing.update(absent)
            else:
                ok += 1
        reason = (
            "every input carries " + ", ".join(required)
            if ok == rows
            else "missing " + ", ".join(f"{k} on {n:,} rows" for k, n in missing.most_common(4))
        )
        return {"ok": ok == rows and rows > 0, "rows": rows, "rows_ok": ok, "reason": reason}
    if intent != "train":
        return {}

    system_prompt = _system_prompt(capability)
    tools = _tools(capability)
    if not system_prompt and not tools:
        return {
            "ok": True,
            "rows": rows,
            "rows_ok": rows,
            "reason": "no system prompt or tools declared",
        }
    if "messages" not in df.columns:
        return {"ok": False, "rows": rows, "rows_ok": 0, "reason": "no messages column"}
    wrong_system = 0
    wrong_tools = 0
    for value in df["messages"].tolist():
        value = _as_object(value)
        if not isinstance(value, list):
            wrong_system += 1
            continue
        bad = False
        if system_prompt:
            first = value[0] if value and isinstance(value[0], dict) else {}
            if (
                first.get("role") != "system"
                or str(first.get("content") or "").strip() != system_prompt
            ):
                wrong_system += 1
                bad = True
        if tools and not bad:
            for turn in value:
                for call in (turn.get("tool_calls") or []) if isinstance(turn, dict) else []:
                    name = (
                        (call.get("function") or {}).get("name") if isinstance(call, dict) else None
                    )
                    if name and name not in tools:
                        wrong_tools += 1
                        bad = True
                        break
                if bad:
                    break
    rows_ok = max(rows - wrong_system - wrong_tools, 0)
    bits = []
    if wrong_system:
        bits.append(f"system turn differs from the capability's on {wrong_system:,} rows")
    if wrong_tools:
        bits.append(f"undeclared tool calls on {wrong_tools:,} rows")
    reason = "; ".join(bits) or "every transcript is the capability's own"
    return {"ok": rows_ok == rows and rows > 0, "rows": rows, "rows_ok": rows_ok, "reason": reason}


_SAMPLE = 500


def rank(project_id: Any, df: pd.DataFrame) -> list[dict[str, Any]]:
    """Every capability of the project scored against the rows, best first.
    Signals: bound capability ids, the system turn, tool names, input keys."""
    from overbae.models import Capability

    capabilities = list(
        Capability.objects.filter(project_id=project_id)
        .exclude(status=Capability.Status.DELETED)
        .order_by("name")
    )
    if not capabilities:
        return []
    sample = df.head(_SAMPLE)
    n = max(int(len(sample)), 1)
    bound = Counter(
        str(v)
        for v in (sample["capability_id"].tolist() if "capability_id" in sample else [])
        if not _missing(v)
    )
    systems: Counter[str] = Counter()
    tool_names: Counter[str] = Counter()
    input_keys: Counter[str] = Counter()
    if "messages" in sample.columns:
        for value in sample["messages"].tolist():
            value = _as_object(value)
            if not isinstance(value, list):
                continue
            first = value[0] if value and isinstance(value[0], dict) else {}
            if first.get("role") == "system":
                systems[str(first.get("content") or "").strip()] += 1
            for turn in value:
                for call in (turn.get("tool_calls") or []) if isinstance(turn, dict) else []:
                    name = (
                        (call.get("function") or {}).get("name") if isinstance(call, dict) else None
                    )
                    if name:
                        tool_names[str(name)] += 1
    if "system_prompt" in sample.columns:
        for value in sample["system_prompt"].tolist():
            if not _missing(value):
                systems[str(value).strip()] += 1
    if "input" in sample.columns:
        for value in sample["input"].tolist():
            value = _as_object(value)
            if isinstance(value, dict):
                input_keys.update(str(k) for k in value)
    out: list[dict[str, Any]] = []
    for capability in capabilities:
        score = 0.0
        reasons: list[str] = []
        by_id = bound.get(str(capability.id), 0) / n
        if by_id:
            score = max(score, by_id)
            reasons.append(f"{by_id:.0%} of rows bound to it")
        prompt = _system_prompt(capability)
        if prompt and systems:
            by_prompt = systems.get(prompt, 0) / n
            if by_prompt:
                score = max(score, by_prompt)
                reasons.append(f"system turn matches on {by_prompt:.0%}")
        tools = _tools(capability)
        if tools and tool_names:
            hits = sum(c for name, c in tool_names.items() if name in tools)
            by_tools = hits / max(sum(tool_names.values()), 1)
            if by_tools:
                score = max(score, 0.9 * by_tools)
                reasons.append(f"{by_tools:.0%} of tool calls are its tools")
        keys = _schema_keys(_schema(capability))
        if keys and input_keys:
            present = sum(1 for k in keys if input_keys.get(k, 0) >= n * 0.5)
            by_keys = present / len(keys)
            if by_keys:
                score = max(score, 0.8 * by_keys)
                reasons.append(f"{present} of {len(keys)} input keys present")
        out.append(
            {
                "capability_id": str(capability.id),
                "name": capability.name,
                "score": round(score, 3),
                "reason": "; ".join(reasons) or "no signal",
            }
        )
    out.sort(key=lambda item: (-item["score"], item["name"]))
    return out
