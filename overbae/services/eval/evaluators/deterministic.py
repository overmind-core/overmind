"""Deterministic (no-LLM) evaluator family. ``Evaluator.config["check"]`` names
a key of :data:`CHECKS`."""

from __future__ import annotations

import json
import re
from typing import Any

from overbae.services.eval.evaluators import base
from overbae.services.eval.evaluators.base import (
    OUTCOME_ABSTAINED,
    OUTCOME_SCORED,
    EvalUnit,
    ResolvedVariable,
    ScoreDraft,
)
from overbae.services.tool_names import canonical_tool_name

# Checks reading the output text; the rest read the structured graph.
_OUTPUT_CHECKS = frozenset(
    {
        "exact_match",
        "contains",
        "regex",
        "json_schema_valid",
        "goal_state_match",
        "canonical_fields",
        "schema_field_conformance",
        "trajectory_terminals",
        "field_present",
        "reference_field_compare",
    }
)


def evaluate(unit: EvalUnit, evaluator, ctx: dict[str, Any]) -> list[ScoreDraft]:
    config = evaluator.config or {}
    check = config.get("check", "exact_match")
    fn = CHECKS.get(check)
    if fn is None:
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type="numeric",
                value=None,
                reasoning=f"Unknown check '{check}'",
            )
        ]

    output_rv = _resolve_output(unit, config)
    # Abstain rather than fabricate a 0.0 fail.
    if check in _OUTPUT_CHECKS and _has_output_target(config) and output_rv.strategy == "absent":
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type="boolean",
                value=None,
                outcome=OUTCOME_ABSTAINED,
                reasoning=(
                    "Insufficient evidence: the targeted output field "
                    f"({config.get('output_field') or config.get('output_jsonpath')}) "
                    "resolved to empty, so there is nothing to check."
                ),
                scope=evaluator.scope,
                sub_scores=[_resolution_subscore({"output": output_rv})],
            )
        ]

    # A check returns ``(value, reasoning, data_type[, extra_sub_scores])``.
    result = fn(unit, config, output_rv.value) if check in _OUTPUT_CHECKS else fn(unit, config)
    value, reasoning, dtype = result[:3]
    extra_sub_scores = list(result[3]) if len(result) > 3 else []
    # A graded check like F1 is never thresholded.
    passed = None
    if dtype == "boolean" and value is not None:
        threshold = evaluator.pass_threshold if evaluator.pass_threshold is not None else 1.0
        passed = value >= threshold
    return [
        ScoreDraft(
            name=evaluator.name,
            data_type=dtype,
            value=value,
            passed=passed,
            outcome=OUTCOME_SCORED if value is not None else OUTCOME_ABSTAINED,
            reasoning=reasoning,
            scope=evaluator.scope,
            sub_scores=[_resolution_subscore({"output": output_rv})] + extra_sub_scores,
        )
    ]


def _has_output_target(config: dict) -> bool:
    return bool(config.get("output_field") or config.get("output_jsonpath"))


def _resolve_output(unit: EvalUnit, config: dict) -> ResolvedVariable:
    field = config.get("output_field")
    jsonpath = config.get("output_jsonpath")
    if field:
        return base.resolve_one(unit, field)
    if jsonpath:
        return base.resolve_one(unit, "output", source="final_output", jsonpath=jsonpath)
    return base.resolve_one(unit, "output", source="output")


def _resolution_subscore(resolved: dict[str, ResolvedVariable]) -> dict[str, Any]:
    return {
        "_resolution": {k: {"strategy": v.strategy, "shape": v.shape} for k, v in resolved.items()}
    }


def _reference_text(unit: EvalUnit, config: dict) -> str:
    if "reference" in config:
        return str(config["reference"])
    if config.get("reference_field"):
        return base.resolve_one(unit, config["reference_field"]).value
    if config.get("reference_jsonpath"):
        return base.resolve_one(
            unit, "reference", source="reference", jsonpath=config["reference_jsonpath"]
        ).value
    exp = unit.expected
    if isinstance(exp, str):
        return exp
    if isinstance(exp, dict):
        return exp.get("final_output") or exp.get("output") or json.dumps(exp, default=str)
    return "" if exp is None else json.dumps(exp, default=str)


def _exact_match(unit, config, output):
    out = output.strip()
    ref = _reference_text(unit, config).strip()
    if config.get("case_insensitive"):
        out, ref = out.lower(), ref.lower()
    ok = out == ref
    return (1.0 if ok else 0.0, "exact match" if ok else "outputs differ", "boolean")


def _contains(unit, config, output):
    needle = str(config.get("reference") or _reference_text(unit, config))
    if config.get("case_insensitive"):
        output, needle = output.lower(), needle.lower()
    ok = needle in output
    return (1.0 if ok else 0.0, "substring present" if ok else "substring missing", "boolean")


def _regex(unit, config, output):
    pattern = config.get("pattern", "")
    try:
        ok = bool(re.search(pattern, output))
    except re.error as exc:
        return (None, f"bad regex: {exc}", "boolean")
    return (1.0 if ok else 0.0, "pattern matched" if ok else "pattern not found", "boolean")


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)

_UNSET = object()


def _extract_balanced_json(text: str) -> str | None:
    """A brace inside a string literal cannot unbalance the scan."""
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _parse_json_lenient(out: str) -> Any:
    """Direct parse, then the fenced block, then the first balanced object;
    :data:`_UNSET` when nothing parses."""
    text = (out or "").strip()
    if not text:
        return _UNSET
    candidates = [text]
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.append(fence.group(1).strip())
    balanced = _extract_balanced_json(text)
    if balanced:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return _UNSET


def _key_present(parsed: Any, key: str) -> bool:
    """Tolerates one envelope level."""
    if not isinstance(parsed, dict):
        return False
    if key in parsed:
        return True
    for value in parsed.values():
        if isinstance(value, dict) and key in value:
            return True
        if isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items and all(key in item for item in dict_items):
                return True
    return False


def _is_empty_deliverable(output: str) -> bool:
    """An empty container / null is a rejection decision; a flatly blank output
    is a broken record, not a decision."""
    text = (output or "").strip()
    if not text:
        return False
    if text.lower() in ("null", "none"):
        return True
    parsed = _parse_json_lenient(text)
    return parsed is None or (isinstance(parsed, (list, dict)) and not parsed)


def _json_schema_valid(unit, config, output):
    if _is_empty_deliverable(output):
        return (
            None,
            "no output record to validate: an explicit empty output is a "
            "rejection terminal, not a schema violation; abstaining",
            "boolean",
        )
    parsed = _parse_json_lenient(output)
    required = [str(k) for k in (config.get("required_keys") or [])]
    if parsed is _UNSET:
        # Naming every key keeps the graph's violates edges lit.
        extra = [{"_violated_fields": list(required)}] if required else []
        return (0.0, "output is not valid JSON", "boolean", extra)
    missing = [k for k in required if not _key_present(parsed, k)]
    if not missing:
        return (1.0, "valid JSON with required keys", "boolean")
    # A key the graded surface cannot carry (dropped by the harness by design) abstains.
    off_surface = _off_surface_keys(unit, parsed, required, missing)
    enforced = [k for k in missing if k not in off_surface]
    if not enforced:
        return (
            None,
            "abstained: required key(s) "
            f"{off_surface} are absent from the graded surface (wrong-layer "
            "contract); no enforceable contract key is missing",
            "boolean",
            [{"_abstained_fields": off_surface}],
        )
    sub_scores = [{"_violated_fields": enforced}]
    if off_surface:
        sub_scores.append({"_abstained_fields": off_surface})
    return (0.0, f"missing keys: {enforced}", "boolean", sub_scores)


def _reference_object(unit) -> dict | None:
    exp = unit.expected
    if isinstance(exp, str):
        parsed = _parse_json_lenient(exp)
        return parsed if isinstance(parsed, dict) else None
    if isinstance(exp, dict):
        for key in ("final_output", "output"):
            inner = exp.get(key)
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, str):
                parsed = _parse_json_lenient(inner)
                if isinstance(parsed, dict):
                    return parsed
        return exp
    return None


def _off_surface_keys(unit, parsed, required: list[str], missing: list[str]) -> list[str]:
    """Without a reference, a FLAT record mixing contract keys with undeclared
    harness markers is a surface mismatch, not a model omission; requiring a
    top-level contract key keeps a wrapper envelope from tripping the probe."""
    reference = _reference_object(unit)
    if reference is not None:
        return [k for k in missing if not _key_present(reference, k)]
    if isinstance(parsed, dict):
        has_top_contract_key = any(k in parsed for k in required)
        has_undeclared_top_key = any(k not in required for k in parsed)
        if has_top_contract_key and has_undeclared_top_key:
            return list(missing)
    return []


def _called_tools(unit: EvalUnit) -> list[dict[str, Any]]:
    return ((unit.structured or {}).get("tool_graph") or {}).get("nodes", [])


def _expected_tools(unit: EvalUnit, config: dict) -> list[Any]:
    if "expected_tools" in config:
        return config["expected_tools"]
    exp = unit.expected
    if isinstance(exp, dict) and "tools" in exp:
        return exp["tools"]
    return []


def _tool_selection(unit, config):
    called = [n.get("tool") for n in _called_tools(unit)]
    expected = [t if isinstance(t, str) else t.get("name") for t in _expected_tools(unit, config)]
    called_set, expected_set = set(called), set(expected)
    if not expected_set:
        return (None, "no expected tools provided", "numeric")
    if not called:
        # A 0 would conflate "no tool calls" with "the wrong tool calls".
        return (None, "no tool calls in trajectory; abstaining (empty evidence)", "numeric")
    tp = len(called_set & expected_set)
    precision = tp / len(called_set) if called_set else 0.0
    if "expected_tools" in config:
        # Config-sourced tools are a VOCABULARY, not a per-task expectation, so
        # recall is meaningless.
        unknown = sorted(called_set - expected_set)
        detail = f"; out-of-vocabulary: {', '.join(unknown)}" if unknown else ""
        return (
            precision,
            f"{tp}/{len(called_set)} called tool(s) in declared vocabulary{detail}",
            "numeric",
        )
    recall = tp / len(expected_set) if expected_set else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return (f1, f"tool selection F1={f1:.2f} (P={precision:.2f} R={recall:.2f})", "numeric")


def _tool_args_match(unit, config):
    called = _called_tools(unit)
    expected = _expected_tools(unit, config)
    if not expected:
        return (None, "no expected tool args provided", "numeric")
    matched = 0
    for exp in expected:
        name = exp.get("name") if isinstance(exp, dict) else exp
        exp_args = exp.get("arguments", {}) if isinstance(exp, dict) else {}
        for n in called:
            if n.get("tool") == name and _args_match(
                n.get("arguments", {}), exp_args, config.get("tool_args_match_mode", "exact")
            ):
                matched += 1
                break
    frac = matched / len(expected)
    return (frac, f"{matched}/{len(expected)} tool calls matched expected args", "numeric")


def _args_match(actual: dict, expected: dict, mode: str) -> bool:
    if mode == "ignore":
        return True
    if not isinstance(actual, dict) or not isinstance(expected, dict):
        return actual == expected
    if mode == "subset":
        return all(actual.get(k) == v for k, v in expected.items())
    if mode == "superset":
        return all(expected.get(k) == v for k, v in actual.items())
    return actual == expected


# Constraint ``id`` is ``card_compiler.stable_ref_id(rule)`` — sha1[:12] of
# the rule text, matching the compiled snapshot.

_POSITION_RE = re.compile(r"\b(last|first)\b", re.I)
_SITUATIONAL_RE = re.compile(r"\b(before|after|when|if|unless|whenever)\b", re.I)


def _tool_round_count(unit: EvalUnit) -> int | None:
    """Assistant turns that issued tool calls. None when the trajectory has no
    messages — call count is not a substitute."""
    messages = (unit.trajectory or {}).get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    return sum(
        1
        for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("tool_calls")
    )


def _rule_position(entry: dict, params: dict) -> str:
    pos = str(params.get("position") or "").lower()
    if pos in ("last", "first"):
        return pos
    m = _POSITION_RE.search(str(entry.get("rule") or ""))
    return m.group(1).lower() if m else ""


def _situational_rule(entry: dict, params: dict) -> bool:
    if params.get("required") is False:
        return True
    if params.get("required") is True:
        return False
    return bool(_SITUATIONAL_RE.search(str(entry.get("rule") or "")))


_MISSING = object()


def _json_object(value: Any) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = _parse_json_lenient(value)
        return parsed if isinstance(parsed, dict) else None
    return None


def _observable_record(unit: EvalUnit, output: str) -> dict | None:
    """Top-level keys of parsed output over parsed user input. None when neither
    is a JSON object, so a situational trigger cannot be evaluated from prose."""
    record: dict[str, Any] = {}
    found = False
    messages = (unit.trajectory or {}).get("messages") or []
    user = next(
        (m.get("content") for m in messages if isinstance(m, dict) and m.get("role") == "user"),
        None,
    )
    inp = _json_object(user)
    if inp:
        record.update(inp)
        found = True
    out = _json_object(output)
    if out:
        record.update(out)
        found = True
    return record if found else None


def _record_get(record: dict[str, Any], key: str) -> Any:
    if key in record:
        return record[key]
    for value in record.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return _MISSING


def _value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _trigger_holds(params: dict, record: dict | None) -> bool | None:
    """True = the situation applies (must-call). False = it does not. None =
    cannot tell, so the rule stays unverifiable."""
    pair = params.get("when_fields_differ")
    if isinstance(pair, list) and len(pair) == 2:
        left, right = str(pair[0]).strip(), str(pair[1]).strip()
        if not left or not right:
            return None
        if record is None:
            return None
        a, b = _record_get(record, left), _record_get(record, right)
        if a is _MISSING or b is _MISSING:
            return False
        if not _value_present(a) or not _value_present(b):
            return False
        return _fold(a) != _fold(b)
    field = str(params.get("when_field") or "").strip()
    if not field:
        return None
    if record is None:
        return None
    value = _record_get(record, field)
    if value is _MISSING:
        return False
    values = params.get("when_values")
    if isinstance(values, list) and values:
        return _fold(value) in {_fold(v) for v in values}
    return _value_present(value)


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NULL_STRINGS = frozenset({"none", "null"})


def _call_arguments(node: dict) -> dict | None:
    raw = node.get("arguments")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = _parse_json_lenient(raw)
        return parsed if isinstance(parsed, dict) else None
    return None


def _is_null_string(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower() in _NULL_STRINGS


def _argument_error(value: Any, spec: dict) -> str | None:
    """Why this value fails the stamped schema, or None if it holds."""
    kind = str(spec.get("kind") or "")
    nullable = bool(spec.get("nullable"))
    if value is None:
        return None if nullable else "is null"
    if _is_null_string(value):
        return "encoded null as a string"
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "not a number"
        return None
    if kind == "boolean":
        if not isinstance(value, bool):
            return "not a boolean"
        return None
    if kind == "date":
        if not (isinstance(value, str) and _ISO_DATE_RE.fullmatch(value.strip())):
            return "not an ISO date"
        return None
    if kind == "enum":
        allowed = spec.get("enum") or []
        if _fold(value) not in {_fold(v) for v in allowed}:
            return "not in the declared enum"
        return None
    return None


def _check_tool_arguments(params: dict, calls: list[dict]) -> tuple[str, str]:
    tool = canonical_tool_name(str(params.get("tool") or "").strip())
    schema = [a for a in (params.get("arguments") or []) if isinstance(a, dict) and a.get("name")]
    if not tool or not schema:
        return ("abstain", "no argument schema declared")
    matching = [n for n in calls if canonical_tool_name(n.get("tool")) == tool]
    if not matching:
        return ("abstain", f"{tool} was not called")
    problems: list[str] = []
    for node in matching:
        args = _call_arguments(node)
        if args is None:
            problems.append(f"{tool}: arguments are not a JSON object")
            continue
        for spec in schema:
            name = str(spec["name"])
            required = bool(spec.get("required"))
            if name not in args:
                if required:
                    problems.append(f"{tool}.{name} missing")
                continue
            err = _argument_error(args[name], spec)
            if err:
                problems.append(f"{tool}.{name} {err}")
    if problems:
        return ("fail", "; ".join(problems[:6]))
    return ("pass", f"{tool} arguments matched the declared schema")


def _check_constraint(
    entry: dict,
    called: list[str],
    declared: set[str],
    output: str,
    *,
    n_rounds: int | None = None,
    record: dict | None = None,
    schema: dict | None = None,
    calls: list[dict] | None = None,
) -> tuple[str, str]:
    """``(pass | fail | abstain, reason)``."""
    ctype = str(entry.get("type") or "")
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    if ctype in ("ordering", "precondition"):
        sequence = [canonical_tool_name(t) for t in entry.get("tools") or []]
        position = _rule_position(entry, params)
        if position in ("last", "first"):
            raw_tool = sequence[0] if sequence else str(params.get("tool") or "").strip()
            tool = canonical_tool_name(raw_tool) if raw_tool else ""
            if not tool:
                return ("abstain", "no tool declared for position check")
            if not called:
                return ("abstain", "no tool calls in trajectory")
            got = called[0] if position == "first" else called[-1]
            ok = got == tool
            return (
                "pass" if ok else "fail",
                f"{position} call was {got}, expected {tool}",
            )
        if len(sequence) >= 2:
            positions = [called.index(t) for t in sequence if t in called]
            if len(positions) < 2:
                return ("abstain", "fewer than two of the sequenced tools were called")
            if positions == sorted(positions):
                return ("pass", "declared call order held")
            return ("fail", f"declared call order violated: {' -> '.join(sequence)}")
        if len(sequence) == 1:
            if params.get("required") is False:
                return ("abstain", "card marks this rule not required")
            if not params.get("when_field") and not params.get("when_fields_differ") and schema:
                from overbae.services.eval.card_compiler import (  # noqa: PLC0415 — cycle through specs
                    situational_trigger_params,
                )

                inferred = situational_trigger_params(str(entry.get("rule") or ""), schema)
                if inferred:
                    params = {**params, **inferred}
            if (
                params.get("when_field")
                or params.get("when_fields_differ")
                or _situational_rule(entry, params)
            ):
                holds = _trigger_holds(params, record)
                tool = sequence[0]
                if holds is True:
                    if tool in called:
                        return ("pass", f"{tool} was called (trigger held)")
                    return ("fail", f"{tool} was not called (trigger held)")
                if holds is False:
                    return ("abstain", "situational trigger did not apply")
                return ("abstain", "situational rule; trigger is not mechanically checkable")
            tool = sequence[0]
            if tool in called:
                return ("pass", f"{tool} was called")
            return ("fail", f"{tool} was not called")
        return ("abstain", "no tool sequence declared to check")
    if ctype == "budget":
        call_limit = params.get("max_calls", params.get("max_tool_calls"))
        if isinstance(call_limit, (int, float)):
            if not called:
                return ("abstain", "no tool calls in trajectory")
            ok = len(called) <= call_limit
            return (
                "pass" if ok else "fail",
                f"{len(called)} tool call(s) vs budget {call_limit}",
            )
        round_limit = params.get("max_rounds")
        if isinstance(round_limit, (int, float)):
            if n_rounds is None:
                return ("abstain", "no tool-round count on this trajectory")
            if n_rounds == 0:
                return ("abstain", "no tool-calling rounds in trajectory")
            ok = n_rounds <= round_limit
            return (
                "pass" if ok else "fail",
                f"{n_rounds} tool round(s) vs budget {round_limit}",
            )
        return ("abstain", "no numeric max_calls or max_rounds budget param")
    if ctype == "tool_discipline":
        raw_tool = str(params.get("tool") or "").strip()
        if raw_tool:
            target = canonical_tool_name(raw_tool)
            limit = params.get("max_calls", params.get("max_tool_calls"))
            n = sum(1 for t in called if t == target)
            if isinstance(limit, (int, float)):
                expected = int(limit)
                if re.search(r"\bexactly\b", str(entry.get("rule") or ""), re.I):
                    ok = n == expected
                    return (
                        "pass" if ok else "fail",
                        f"{target} called {n} time(s), expected {expected}",
                    )
                if n > expected:
                    return ("fail", f"{target} called {n} time(s) vs max {expected}")
                if n == 0:
                    return ("fail", f"{target} was not called")
                return ("pass", f"{target} called {n} time(s) vs max {expected}")
            if n:
                return ("pass", f"{target} was called")
            return ("fail", f"{target} was not called")
        if not declared:
            return ("abstain", "card declares no tool vocabulary")
        if not called:
            return ("abstain", "no tool calls in trajectory")
        undeclared = sorted({t for t in called if t not in declared})
        if undeclared:
            return ("fail", f"undeclared tool(s) called: {', '.join(undeclared)}")
        return ("pass", "only declared tools called")
    if ctype == "output_format":
        if params.get("fence_output") is False:
            if not (output or "").strip():
                return ("abstain", "no output to check for fences")
            fenced = (output or "").lstrip().startswith("```")
            return ("fail" if fenced else "pass", "output must not be markdown-fenced")
        if str(params.get("format") or "").lower() == "json":
            if not (output or "").strip():
                return ("abstain", "no output to parse")
            ok = _parse_json_lenient(output) is not _UNSET
            return ("pass" if ok else "fail", "output must parse as JSON")
        return ("abstain", "no mechanically checkable format param")
    if ctype == "tool_arguments":
        return _check_tool_arguments(params, calls or [])
    return ("abstain", f"constraint type {ctype!r} not mechanically checkable")


def _card_constraints(unit, config):
    entries = [e for e in config.get("constraints") or [] if isinstance(e, dict)]
    if not entries:
        return (None, "no card constraints configured", "numeric")
    declared = {canonical_tool_name(t) for t in config.get("declared_tools") or []}
    calls = _called_tools(unit)
    called = [canonical_tool_name(n.get("tool")) for n in calls]
    output = base.resolve_one(unit, "output", source="output").value
    n_rounds = _tool_round_count(unit)
    record = _observable_record(unit, output)
    from overbae.services.eval.card_compiler import (
        schema_field_map,  # noqa: PLC0415 — cycle through specs
    )

    schema = schema_field_map(unit.contract_card())
    subs: list[dict[str, Any]] = []
    passed_n = 0
    checked = 0
    for entry in entries:
        verdict, reason = _check_constraint(
            entry,
            called,
            declared,
            output,
            n_rounds=n_rounds,
            record=record,
            schema=schema,
            calls=calls,
        )
        cid = str(entry.get("id") or "")
        # Boolean verdict for _item_signal; nested string for violates_constraint.
        subs.append(
            {
                "id": cid,
                "verdict": {"pass": True, "fail": False}.get(verdict),
                "_constraint": {
                    "id": cid,
                    "type": str(entry.get("type") or ""),
                    "rule": str(entry.get("rule") or "")[:200],
                    "verdict": verdict,
                    "reason": reason,
                },
            }
        )
        if verdict != "abstain":
            checked += 1
            passed_n += verdict == "pass"
    abstained = len(entries) - checked
    if checked == 0:
        return (None, "no constraint was mechanically checkable for this trace", "numeric", subs)
    reasoning = f"{passed_n}/{checked} checkable constraint(s) held ({abstained} abstained)"
    return (passed_n / checked, reasoning, "numeric", subs)


_TYPE_CHECKS = {
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _field_values(parsed: Any, key: str) -> list[Any]:
    """Mirrors the envelope tolerance of :func:`_key_present`."""
    if isinstance(parsed, list):
        dict_items = [item for item in parsed if isinstance(item, dict)]
        if dict_items and all(key in item for item in dict_items):
            return [item[key] for item in dict_items]
        return []
    if not isinstance(parsed, dict):
        return []
    if key in parsed:
        return [parsed[key]]
    for value in parsed.values():
        if isinstance(value, dict) and key in value:
            return [value[key]]
        if isinstance(value, list):
            dict_items = [item for item in value if isinstance(item, dict)]
            if dict_items and all(key in item for item in dict_items):
                return [item[key] for item in dict_items]
    return []


def _conformance_violation(value: Any, rules: dict) -> str | None:
    if value is None:
        return None if rules.get("nullable") else "null but not declared nullable"
    expected_type = rules.get("type")
    type_check = _TYPE_CHECKS.get(expected_type or "")
    if type_check and not type_check(value):
        return f"expected {expected_type}, got {type(value).__name__}"
    enum = rules.get("enum")
    if enum and isinstance(value, str):
        allowed = {str(e).strip().lower() for e in enum}
        if value.strip().lower() not in allowed:
            return f"value {value!r} not in enum {sorted(allowed)}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = rules.get("min"), rules.get("max")
        if lo is not None and value < lo:
            return f"value {value} below minimum {lo}"
        if hi is not None and value > hi:
            return f"value {value} above maximum {hi}"
    pattern = rules.get("pattern")
    if pattern and isinstance(value, str):
        try:
            if not re.search(pattern, value):
                return f"value {value!r} does not match pattern {pattern!r}"
        except re.error:
            return None
    return None


def _schema_field_conformance(unit, config, output):
    """Presence is ``json_schema_valid``'s job; an absent field is skipped, not failed."""
    fields = config.get("fields") or {}
    if not fields:
        return (None, "no field rules configured", "numeric")
    if not (output or "").strip():
        return (None, "no output to validate; abstaining", "numeric")
    parsed = _parse_json_lenient(output)
    if parsed is _UNSET:
        return (
            0.0,
            "output is not parseable JSON",
            "numeric",
            [{"_violated_fields": sorted(str(k) for k in fields)}],
        )
    checked = 0
    conforming = 0
    violated: list[str] = []
    subs: list[dict[str, Any]] = []
    for name, rules in fields.items():
        if not isinstance(rules, dict):
            continue
        values = _field_values(parsed, str(name))
        if not values:
            subs.append({"_field_conformance": {"field": str(name), "verdict": "absent"}})
            continue
        checked += 1
        reason = next(
            (r for v in values if (r := _conformance_violation(v, rules)) is not None), None
        )
        if reason is None:
            conforming += 1
            subs.append({"_field_conformance": {"field": str(name), "verdict": "pass"}})
        else:
            violated.append(str(name))
            subs.append(
                {"_field_conformance": {"field": str(name), "verdict": "fail", "reason": reason}}
            )
    if checked == 0:
        return (None, "no declared field present in the output to validate", "numeric", subs)
    if violated:
        subs.append({"_violated_fields": violated})
    reasoning = f"{conforming}/{checked} present field(s) conform to their declared rules"
    return (conforming / checked, reasoning, "numeric", subs)


def _field_present(unit, config, output):
    field = str(config.get("field") or "").strip()
    if not field:
        return (None, "no field configured", "boolean")
    if _is_empty_deliverable(output):
        return (None, "explicit empty output is a rejection terminal; abstaining", "boolean")
    parsed = _parse_json_lenient(output)
    if parsed is _UNSET:
        return (0.0, "output is not valid JSON", "boolean", [{"_violated_fields": [field]}])
    if _key_present(parsed, field):
        return (1.0, f"field {field!r} present", "boolean")
    return (0.0, f"field {field!r} missing", "boolean", [{"_violated_fields": [field]}])


def _values_compare(out_val: Any, ref_val: Any, rules: dict) -> tuple[bool, str]:
    tolerance = rules.get("tolerance") or {}
    if (
        isinstance(out_val, (int, float))
        and isinstance(ref_val, (int, float))
        and not isinstance(out_val, bool)
        and not isinstance(ref_val, bool)
    ):
        abs_tol = float(tolerance.get("abs") or 0.0)
        rel_tol = float(tolerance.get("rel") or 0.0)
        delta = abs(float(out_val) - float(ref_val))
        limit = max(abs_tol, rel_tol * abs(float(ref_val)))
        ok = delta <= limit
        return ok, f"|{out_val} - {ref_val}| = {delta:g} vs tolerance {limit:g}"
    out_s, ref_s = str(out_val).strip(), str(ref_val).strip()
    ok = out_s.lower() == ref_s.lower()
    return ok, "values match" if ok else f"output {out_s[:80]!r} != reference {ref_s[:80]!r}"


def _reference_field_compare(unit, config, output):
    """Fields absent from the reference abstain."""
    fields = config.get("fields") or {}
    if not fields:
        return (None, "no fields configured", "numeric")
    reference = _reference_object(unit)
    if reference is None:
        return (None, "no structured reference on this sample; abstaining", "numeric")
    if _is_empty_deliverable(output):
        return (None, "explicit empty output is a rejection terminal; abstaining", "numeric")
    parsed = _parse_json_lenient(output)
    if parsed is _UNSET:
        return (
            0.0,
            "output is not parseable JSON",
            "numeric",
            [{"_violated_fields": sorted(str(k) for k in fields)}],
        )
    checked = 0
    matched = 0
    violated: list[str] = []
    subs: list[dict[str, Any]] = []
    for name, rules in fields.items():
        rules = rules if isinstance(rules, dict) else {}
        ref_values = _field_values(reference, str(name))
        if not ref_values:
            subs.append({"_field_compare": {"field": str(name), "verdict": "no_reference"}})
            continue
        out_values = _field_values(parsed, str(name))
        checked += 1
        if not out_values:
            violated.append(str(name))
            subs.append({"_field_compare": {"field": str(name), "verdict": "missing"}})
            continue
        ok, reason = _values_compare(out_values[0], ref_values[0], rules)
        if ok:
            matched += 1
            subs.append({"_field_compare": {"field": str(name), "verdict": "pass"}})
        else:
            violated.append(str(name))
            subs.append(
                {"_field_compare": {"field": str(name), "verdict": "fail", "reason": reason}}
            )
    if checked == 0:
        return (None, "no configured field present in the reference; abstaining", "numeric", subs)
    if violated:
        subs.append({"_violated_fields": violated})
    reasoning = f"{matched}/{checked} required field(s) match the reference"
    return (matched / checked, reasoning, "numeric", subs)


def _metadata_metric(field: str):
    def check(unit, config):
        meta = (unit.trajectory or {}).get("metadata", {})
        val = meta.get(field)
        if val is None:
            return (None, f"no {field} in metadata", "numeric")
        return (float(val), f"{field}={val}", "numeric")

    return check


def _turn_count(unit, config):
    n = (unit.structured or {}).get("num_turns")
    if n is None:
        n = len((unit.trajectory or {}).get("messages", []))
    return (float(n), f"turns={n}", "numeric")


_NUMBER_TOLERANCE = 0.01


def _fold(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value if value is not None else "").lower()).strip()


def _string_key(value: Any) -> str:
    return str(value).strip().rstrip(".,;:").strip().casefold()


def _canonical_equal(kind: str, got: Any, expected: Any) -> bool:
    """Compare on canonical form, never on string identity.

    Strict equality is not a safe default: over one 48-row run it failed a
    correct ``4500`` against a reference ``4500.0`` twelve times — enough to
    invert which of two models ranked higher.
    """
    if kind == "boolean":
        return bool(got) == bool(expected)
    if kind == "number":
        try:
            return abs(float(got) - float(expected)) <= _NUMBER_TOLERANCE
        except (TypeError, ValueError):
            return False
    if kind == "string":
        # Interior punctuation is significant (INV-123 ≠ INV123); a trailing
        # period, comma, semicolon or colon is not.
        if got is None or expected is None:
            return got is None and expected is None
        return _string_key(got) == _string_key(expected)
    return _fold(got) == _fold(expected)


def _canonical_fields(unit, config, output):
    fields = [
        f
        for f in (config.get("fields") or [])
        if isinstance(f, dict) and f.get("name") and f.get("kind")
    ]
    if not fields:
        return (None, "abstained: no canonical fields configured", "numeric")
    parsed = _parse_json_lenient(output)
    if not isinstance(parsed, dict):
        return (None, "abstained: output is not a JSON object", "numeric")
    reference = _reference_object(unit)
    if reference is None:
        return (None, "abstained: no reference object to compare against", "numeric")

    verdicts: list[dict[str, Any]] = []
    violated: list[str] = []
    for field in fields:
        name, kind = str(field["name"]), str(field["kind"])
        got, expected = parsed.get(name), reference.get(name)
        # A null in the reference is an assertion that the field has no value,
        # not an absence of opinion. Do not soften it by looking for the model's
        # value in the source: on a paid receipt the total does appear in the
        # text, and is still the wrong answer for "amount due".
        ok = got is None if expected is None else _canonical_equal(kind, got, expected)
        verdicts.append({"id": name, "verdict": ok, "field": name})
        if not ok:
            violated.append(name)

    extra: list[dict[str, Any]] = list(verdicts)
    if violated:
        extra.append({"_violated_fields": violated})
    return (
        (len(fields) - len(violated)) / len(fields),
        "all canonical fields match" if not violated else f"mismatched: {', '.join(violated)}",
        "numeric",
        extra,
    )


def _goal_state_match(unit, config, output):
    out = output.lower()
    markers = [str(m).lower() for m in (config.get("goal_markers") or [])]
    if not markers:
        return (None, "no goal markers provided", "boolean")
    hit = sum(1 for m in markers if m in out)
    ok = hit == len(markers)
    return (1.0 if ok else 0.0, f"{hit}/{len(markers)} goal markers present", "boolean")


CHECKS = {
    "canonical_fields": _canonical_fields,
    "exact_match": _exact_match,
    "contains": _contains,
    "regex": _regex,
    "json_schema_valid": _json_schema_valid,
    "tool_selection": _tool_selection,
    "tool_args_match": _tool_args_match,
    "goal_state_match": _goal_state_match,
    "card_constraints": _card_constraints,
    "schema_field_conformance": _schema_field_conformance,
    "field_present": _field_present,
    "reference_field_compare": _reference_field_compare,
    "latency": _metadata_metric("latency_ms"),
    "cost": _metadata_metric("cost"),
    "turn_count": _turn_count,
}
