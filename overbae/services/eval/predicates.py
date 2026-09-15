"""Typed ``applies_when`` predicates: applicability is mechanical, the judge
never decides what applies. ``not`` over a list means "none of these hold".
"""

from __future__ import annotations

import json
import re
from typing import Any

from overbae.models.traces import is_tool_operation

_LEAVES = frozenset(
    {
        "expectation_declared",
        "checkpoint_reached",
        "context_equals",
        "context_present",
        "modality",
        "output_present",
        "tool_called",
    }
)
_COMBINATORS = frozenset({"all", "any", "not"})

RUNTIME_ITEM_PREFIX = "rt_"


class MalformedPredicateError(ValueError):
    pass


def _eval_leaf(op: str, arg: Any, runtime: dict[str, Any], trajectory: dict[str, Any]) -> bool:
    if op == "expectation_declared":
        if not isinstance(arg, str) or not arg:
            raise MalformedPredicateError("expectation_declared takes an id or kind string")
        return any(
            e.get("id") == arg or e.get("kind") == arg for e in runtime.get("expectations") or []
        )
    if op == "checkpoint_reached":
        if not isinstance(arg, str) or not arg:
            raise MalformedPredicateError("checkpoint_reached takes a name string")
        return any(c.get("name") == arg for c in runtime.get("checkpoints") or [])
    if op == "context_equals":
        if not isinstance(arg, dict) or "key" not in arg or "value" not in arg:
            raise MalformedPredicateError("context_equals takes {key, value}")
        context = runtime.get("context") or {}
        return str(arg["key"]) in context and context[str(arg["key"])] == arg["value"]
    if op == "context_present":
        if not isinstance(arg, str) or not arg:
            raise MalformedPredicateError("context_present takes a key string")
        return arg in (runtime.get("context") or {})
    if op == "modality":
        if not isinstance(arg, str) or not arg:
            raise MalformedPredicateError("modality takes a modality string")
        return (trajectory.get("modality") or "") == arg
    if op == "output_present":
        if arg is not True:
            raise MalformedPredicateError("output_present takes true")
        return _output_nonempty(str(trajectory.get("final_output") or ""))
    if op == "tool_called":
        if not isinstance(arg, str) or not arg:
            raise MalformedPredicateError("tool_called takes a tool name string")
        return arg.lower() in _called_tool_names(trajectory)
    raise MalformedPredicateError(f"unknown predicate {op!r}")


def _called_tool_names(trajectory: dict[str, Any]) -> set[str]:
    """Dispatcher-qualified names (``Tools.act:click``) also register under the
    bare suffix so predicates match the SDK vocabulary."""
    names: set[str] = set()

    def add(raw: Any) -> None:
        name = str(raw or "").strip().lower()
        if not name:
            return
        names.add(name)
        if ":" in name:
            names.add(name.rsplit(":", 1)[-1])

    stack = list(trajectory.get("span_tree") or [])
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if is_tool_operation(node.get("type")):
            add(node.get("tool") or node.get("name"))
        stack.extend(node.get("children") or [])
    for message in trajectory.get("messages") or []:
        for call in (message.get("tool_calls") or []) if isinstance(message, dict) else []:
            if isinstance(call, dict):
                add((call.get("function") or {}).get("name") or call.get("name"))
    return names


def _output_nonempty(text: str) -> bool:
    """An empty JSON container or ``null`` is the shape a refusal path emits."""
    text = text.strip()
    if not text or text.lower() in ("null", "none"):
        return False
    if text[:1] in "{[":
        try:
            return bool(json.loads(text))
        except (ValueError, TypeError):
            return True  # malformed JSON is still output, just broken
    return True


def _eval(pred: Any, runtime: dict[str, Any], trajectory: dict[str, Any]) -> bool:
    if not isinstance(pred, dict) or len(pred) != 1:
        raise MalformedPredicateError("predicate must be a single-key object")
    op, arg = next(iter(pred.items()))
    if op in _COMBINATORS:
        if not isinstance(arg, list) or not arg:
            raise MalformedPredicateError(f"{op!r} takes a non-empty list of predicates")
        results = [_eval(child, runtime, trajectory) for child in arg]
        if op == "all":
            return all(results)
        if op == "any":
            return any(results)
        return not any(results)
    if op in _LEAVES:
        return _eval_leaf(op, arg, runtime, trajectory)
    raise MalformedPredicateError(f"unknown predicate {op!r}")


def predicate_applies(
    pred: Any, runtime: dict[str, Any], trajectory: dict[str, Any]
) -> tuple[bool, str]:
    """A malformed predicate excludes the item, so a typo is visible."""
    try:
        applies = _eval(pred, runtime or {}, trajectory or {})
    except MalformedPredicateError as exc:
        return False, f"malformed applies_when: {exc}"
    if applies:
        return True, ""
    return False, f"applies_when not satisfied: {json.dumps(pred, default=str)}"


def filter_checklist(
    checklist: list[dict[str, Any]],
    runtime: dict[str, Any],
    trajectory: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    applicable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in checklist or []:
        pred = item.get("applies_when") if isinstance(item, dict) else None
        if not pred:
            applicable.append(item)
            continue
        applies, reason = predicate_applies(pred, runtime, trajectory)
        if applies:
            applicable.append(item)
        else:
            excluded.append({"item": item, "reason": reason})
    return applicable, excluded


def not_applicable_sub_verdicts(excluded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": str((entry.get("item") or {}).get("id") or ""),
            "verdict": None,
            "score": None,
            "outcome": "not_applicable",
            "reasoning": entry.get("reason") or "",
        }
        for entry in excluded
    ]


def _spec_text(spec: Any) -> str:
    return spec if isinstance(spec, str) else json.dumps(spec, default=str)


def _check_contains(spec: Any, output: str) -> tuple[bool | None, str]:
    needles = spec if isinstance(spec, list) else [spec]
    haystack = output.lower()
    missing = [str(n) for n in needles if str(n).lower() not in haystack]
    if missing:
        return False, f"output does not contain: {', '.join(missing)}"
    return True, "output contains the expected text"


def _check_regex(spec: Any, output: str) -> tuple[bool | None, str]:
    if not isinstance(spec, str):
        return None, "regex spec must be a string"
    try:
        pattern = re.compile(spec)
    except re.error as exc:
        return None, f"invalid regex: {exc}"
    if pattern.search(output):
        return True, f"regex {spec!r} matched"
    return False, f"regex {spec!r} did not match the output"


def _required_keys(spec: Any) -> list[str]:
    if isinstance(spec, list):
        return [str(k) for k in spec]
    if isinstance(spec, dict):
        required = spec.get("required")
        if isinstance(required, list):
            return [str(k) for k in required]
        properties = spec.get("properties")
        if isinstance(properties, dict):
            return [str(k) for k in properties]
    return []


def _check_schema(spec: Any, output: str) -> tuple[bool | None, str]:
    try:
        parsed = json.loads(output.strip())
    except (ValueError, TypeError):
        return False, "output is not valid JSON"
    keys = _required_keys(spec)
    if not keys:
        return True, "output is valid JSON"
    if not isinstance(parsed, dict):
        return False, "output JSON is not an object"
    missing = [k for k in keys if k not in parsed]
    if missing:
        return False, f"missing required key(s): {', '.join(missing)}"
    return True, "output carries all required keys"


_DETERMINISTIC_CHECKS = {
    "contains": _check_contains,
    "regex": _check_regex,
    "schema": _check_schema,
}


def expectation_prepass(
    expectations: list[dict[str, Any]], output: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """``(sub_verdicts, synthetic_checklist_items, gated_fail)``. Conversation-
    scoped expectations belong to conversation scoring and produce nothing here."""
    sub_verdicts: list[dict[str, Any]] = []
    synthetic: list[dict[str, Any]] = []
    gated_fail = False
    for exp in expectations or []:
        if exp.get("scope") == "conversation":
            continue
        item_id = f"{RUNTIME_ITEM_PREFIX}{exp.get('id')}"
        gate = bool(exp.get("gate"))
        kind = exp.get("kind")
        if kind == "constraint":
            synthetic.append(
                {"id": item_id, "q": _spec_text(exp.get("spec")), "weight": 1.0, "gate": gate}
            )
            continue
        check = _DETERMINISTIC_CHECKS.get(kind)
        if check is None:
            continue
        verdict, reasoning = check(exp.get("spec"), output or "")
        if verdict is False and gate:
            gated_fail = True
        sub_verdicts.append(
            {
                "id": item_id,
                "verdict": verdict,
                "score": None,
                "reasoning": reasoning,
                "gate": gate,
                "deterministic": True,
            }
        )
    return sub_verdicts, synthetic, gated_fail
