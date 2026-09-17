"""Capability card normalisation; every claim is anchored to a
real source ``path#Lstart-Lend``.
"""

from __future__ import annotations

from typing import Any

from overbae.services.tool_names import canonical_tool_name


def _provenance_paths(card: dict[str, Any]) -> list[str]:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return []
    paths = prov.get("paths")
    if not isinstance(paths, list):
        return []
    return [str(p).strip() for p in paths if str(p).strip()]


def normalize_modes(value: Any) -> list[dict[str, Any]]:
    """Tasks (analyze/fix/transform) stay under ONE capability rather than
    becoming separate top-level capabilities."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for mode in value:
        if isinstance(mode, dict) and (mode.get("name") or mode.get("entrypoint_fn")):
            entry: dict[str, Any] = {
                "name": str(mode.get("name") or "").strip(),
                "entrypoint_fn": str(mode.get("entrypoint_fn") or "").strip(),
                "source_path": str(mode.get("source_path") or "").strip(),
                "purpose": str(mode.get("purpose") or "").strip(),
                "routing": str(mode.get("routing") or "").strip(),
                "model": str(mode.get("model") or "").strip(),
                "output": str(mode.get("output") or "").strip(),
                "prompt": str(mode.get("prompt") or "").strip(),
                "prompt_excerpt": str(mode.get("prompt_excerpt") or "").strip(),
            }
            prompt_builder = mode.get("prompt_builder")
            if prompt_builder:
                entry["prompt_builder"] = str(prompt_builder).strip()
            out.append(entry)
        elif isinstance(mode, str) and mode.strip():
            out.append(
                {
                    "name": mode.strip(),
                    "entrypoint_fn": "",
                    "source_path": "",
                    "purpose": "",
                    "routing": "",
                    "model": "",
                    "output": "",
                    "prompt": "",
                    "prompt_excerpt": "",
                }
            )
    return out


TOOL_SIDE_EFFECTS: tuple[str, ...] = ("read", "write", "external", "none")


def _coerce_side_effect(value: Any) -> str:
    side_effect = str(value or "").strip().lower()
    return side_effect if side_effect in TOOL_SIDE_EFFECTS else "none"


def normalize_tool_arguments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for arg in value:
        if isinstance(arg, dict):
            name = str(arg.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "type": str(arg.get("type") or "").strip(),
                    "required": bool(arg.get("required")),
                    "description": str(arg.get("description") or "").strip(),
                }
            )
        elif isinstance(arg, str) and arg.strip():
            out.append({"name": arg.strip(), "type": "", "required": False, "description": ""})
    return out


def normalize_tool_spec(value: Any) -> list[dict[str, Any]]:
    """The ``args`` string is kept alongside ``arguments`` for older readers."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for tool in value:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "purpose": str(tool.get("purpose") or "").strip(),
                "args": str(tool.get("args") or "").strip(),
                "side_effect": _coerce_side_effect(tool.get("side_effect")),
                "returns": str(tool.get("returns") or "").strip(),
                "arguments": normalize_tool_arguments(tool.get("arguments")),
                "integration": str(tool.get("integration") or "").strip(),
                # Semantic cluster: groups similar tools in the flow UI and judge grounding.
                "cluster": str(tool.get("cluster") or "").strip(),
                "provenance": _coerce_paths(tool.get("provenance")),
            }
        )
    return out


def _coerce_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(p).strip() for p in value if str(p).strip()]


def _as_schema_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    if isinstance(value, list):
        out: dict[str, Any] = {}
        for item in value:
            if isinstance(item, dict) and item.get("name"):
                out[str(item["name"])] = item.get("description") or item.get("type") or ""
            elif isinstance(item, str) and item.strip():
                out[item.strip()] = ""
        return out
    if isinstance(value, str) and value.strip():
        return {"value": value.strip()}
    return {}


CONSTRAINT_TYPES: tuple[str, ...] = ("tool_discipline", "budget", "ordering", "output_format")

TOOL_PROTOCOL_KINDS: tuple[str, ...] = ("precondition", "ordering", "evidence")


def normalize_output_schema(value: Any) -> dict[str, Any]:
    """The literal validator extracted from source — the contract the code
    enforces, not the model's description of it."""
    if not isinstance(value, dict) or not value:
        return {"required_keys": [], "properties": {}, "provenance": []}
    required = value.get("required_keys")
    if not isinstance(required, list):
        required = []
    properties = value.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    return {
        "required_keys": [str(k).strip() for k in required if str(k).strip()],
        "properties": {str(k): v for k, v in properties.items()},
        "provenance": _coerce_paths(value.get("provenance")),
    }


def normalize_constraints(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            rule = item.strip()
            if not rule:
                continue
            out.append(
                {
                    "rule": rule,
                    "type": "output_format",
                    "params": {},
                    "provenance": [],
                }
            )
            continue
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "").strip()
        if not rule:
            continue
        ctype = str(item.get("type") or "").strip().lower()
        params = item.get("params")
        out.append(
            {
                "rule": rule,
                "type": ctype if ctype in CONSTRAINT_TYPES else "output_format",
                "params": dict(params) if isinstance(params, dict) else {},
                "provenance": _coerce_paths(item.get("provenance")),
            }
        )
    return out


TERMINAL_KINDS: tuple[str, ...] = ("emits_record", "returns_empty", "escalates", "error_exit")

# ``code_path``: control flow determines the route (AST-provable);
# ``declared_task``: the architecture names it (cited); ``decision_surface``: a
# model decides the route, so the entry records only the decision point.
TRAJECTORY_CLAIMS: tuple[str, ...] = ("code_path", "declared_task", "decision_surface")

ANCHOR_KINDS: tuple[str, ...] = (
    "entry_point",
    "workflow",
    "tool",
    "retrieval",
    "function",
)


def normalize_anchors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        qualname = str(item.get("qualname") or "").strip()
        if not qualname or qualname in seen:
            continue
        seen.add(qualname)
        kind = str(item.get("kind") or "").strip().lower()
        out.append(
            {
                "qualname": qualname,
                "kind": kind if kind in ANCHOR_KINDS else "function",
                "file": str(item.get("file") or "").strip(),
            }
        )
    return out


def _normalize_may_use(raw: Any, allowed_canon: set[str] | None) -> list[dict[str, str]]:
    """``allowed_canon`` drops slots naming tools the card never declared."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "").strip()
        if not tool:
            continue
        canon = canonical_tool_name(tool)
        if canon in seen or (allowed_canon is not None and canon not in allowed_canon):
            continue
        seen.add(canon)
        out.append({"tool": tool, "when": str(item.get("when") or "").strip()})
    return out


STEP_KINDS: tuple[str, ...] = ("agent_step", "model_invocation")


def _bare_step(label: str) -> dict[str, Any]:
    return {
        "step": label,
        "kind": "agent_step",
        "anchors": [],
        "input": "",
        "action": "",
        "output": "",
        "may_use": [],
    }


def normalize_trajectory_steps(
    raw_sequence: Any, allowed_canon: set[str] | None
) -> tuple[list[str], list[dict[str, Any]]]:
    """``(sequence labels, structured steps)`` from a path's raw ``sequence``.

    Every entry becomes a structured step — legacy milestone strings (stale
    stored cards, prompt-disobedient outputs) coerce to bare agent steps, so a
    non-empty sequence never yields ``steps == []``. A ``model_invocation``
    step keeps its In/Action/Out contract (``input`` / ``action`` /
    ``output``); the contract is blanked on agent steps.
    """
    if not isinstance(raw_sequence, list):
        return [], []
    steps: list[dict[str, Any]] = []
    for entry in raw_sequence:
        if isinstance(entry, dict):
            label = str(entry.get("step") or "").strip()
            if not label:
                continue
            kind = str(entry.get("kind") or "").strip().lower()
            kind = kind if kind in STEP_KINDS else "agent_step"
            is_model = kind == "model_invocation"
            anchors = entry.get("anchors")
            steps.append(
                {
                    "step": label,
                    "kind": kind,
                    "anchors": [str(a).strip() for a in anchors if str(a).strip()]
                    if isinstance(anchors, list)
                    else [],
                    "input": str(entry.get("input") or "").strip() if is_model else "",
                    "action": str(entry.get("action") or "").strip() if is_model else "",
                    "output": str(entry.get("output") or "").strip() if is_model else "",
                    "may_use": _normalize_may_use(entry.get("may_use"), allowed_canon),
                }
            )
        elif str(entry).strip():
            steps.append(_bare_step(str(entry).strip()))
    return [s["step"] for s in steps], steps


def normalize_trajectory_map(
    value: Any, tool_spec: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Deliberately uncapped: discovery is exhaustive, aggregation is the
    flow/registry's job. ``may_use`` tools are validated against ``tool_spec``
    and folded into the path's ``tools``."""
    if not isinstance(value, list):
        return []

    allowed_canon: set[str] | None = None
    if tool_spec is not None:
        allowed_canon = {
            canonical_tool_name(t.get("name"))
            for t in tool_spec
            if isinstance(t, dict) and str(t.get("name") or "").strip()
        }

    def _str_list(raw: Any) -> list[str]:
        if not isinstance(raw, list):
            return []
        return [str(s).strip() for s in raw if str(s).strip()]

    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path_id = str(item.get("id") or item.get("name") or "").strip()
        if not path_id:
            continue
        terminal = item.get("terminal")
        terminal = terminal if isinstance(terminal, dict) else {}
        kind = str(terminal.get("kind") or "").strip().lower()
        raw_sequence = item.get("sequence")
        raw_steps = item.get("steps")
        # An already-normalized path carries string labels in ``sequence`` and
        # the structured backbone in ``steps`` — renormalizing must not drop it.
        if (
            isinstance(raw_steps, list)
            and raw_steps
            and not (
                isinstance(raw_sequence, list) and any(isinstance(e, dict) for e in raw_sequence)
            )
        ):
            raw_sequence = raw_steps
        sequence, steps = normalize_trajectory_steps(raw_sequence, allowed_canon)
        tools = _str_list(item.get("tools"))
        seen_tools = {canonical_tool_name(t) for t in tools}
        for step in steps:
            for cap in step["may_use"]:
                canon = canonical_tool_name(cap["tool"])
                if canon not in seen_tools:
                    seen_tools.add(canon)
                    tools.append(cap["tool"])
        claim = str(item.get("claim") or "").strip().lower()
        out.append(
            {
                "id": path_id,
                "name": str(item.get("name") or path_id).strip(),
                "claim": claim if claim in TRAJECTORY_CLAIMS else "code_path",
                "prompt_quote": str(item.get("prompt_quote") or "").strip(),
                # Stamped by the chassis reachability check; absent pre-check.
                "verified": bool(item.get("verified")),
                "routing": str(item.get("routing") or "").strip(),
                "sequence": sequence,
                "steps": steps,
                "anchors": _str_list(item.get("anchors")),
                "tools": tools,
                "terminal": {
                    "kind": kind if kind in TERMINAL_KINDS else "emits_record",
                    "description": str(terminal.get("description") or "").strip(),
                },
                "divergences": _str_list(item.get("divergences")),
                "provenance": _coerce_paths(item.get("provenance")),
            }
        )
    ids = {p["id"] for p in out}
    for path in out:
        path["divergences"] = [d for d in path["divergences"] if d in ids and d != path["id"]]
    return out


def normalize_tool_protocol(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "").strip()
        if not rule:
            continue
        tools = item.get("tools")
        if not isinstance(tools, list):
            tools = []
        kind = str(item.get("kind") or "").strip().lower()
        out.append(
            {
                "rule": rule,
                "tools": [str(t).strip() for t in tools if str(t).strip()],
                "kind": kind if kind in TOOL_PROTOCOL_KINDS else "precondition",
                "params": dict(item.get("params")) if isinstance(item.get("params"), dict) else {},
                "provenance": _coerce_paths(item.get("provenance")),
            }
        )
    return out


def _normalize_expected_output(expected: Any) -> dict[str, Any]:
    if isinstance(expected, dict):
        return {
            "description": str(expected.get("description") or "").strip(),
            "example": expected.get("example"),
            "quality_signals": [
                str(s) for s in (expected.get("quality_signals") or []) if str(s).strip()
            ],
        }
    if isinstance(expected, str):
        return {"description": expected.strip(), "example": None, "quality_signals": []}
    return {"description": "", "example": None, "quality_signals": []}


def normalize_capability_card(card: dict[str, Any]) -> dict[str, Any]:
    tool_spec = normalize_tool_spec(card.get("tool_spec"))
    vocabulary = card.get("vocabulary")
    if not isinstance(vocabulary, dict):
        vocabulary = {}
    is_fallback = bool(card.get("_fallback"))
    normalized = {
        "task": str(card.get("task") or "").strip(),
        "modality": str(card.get("modality") or "").strip(),
        "domain": str(card.get("domain") or "").strip(),
        "input_schema": _as_schema_dict(card.get("input_schema")),
        "output_fields": _as_schema_dict(card.get("output_fields")),
        "expected_output": _normalize_expected_output(card.get("expected_output")),
        "tool_spec": tool_spec,
        "vocabulary": {str(k): v for k, v in vocabulary.items()},
        "success_criteria": [
            str(s) for s in (card.get("success_criteria") or []) if str(s).strip()
        ],
        "failure_modes": [str(s) for s in (card.get("failure_modes") or []) if str(s).strip()],
        "output_schema": normalize_output_schema(card.get("output_schema")),
        "constraints": normalize_constraints(card.get("constraints")),
        "tool_protocol": normalize_tool_protocol(card.get("tool_protocol")),
        "trajectory_map": normalize_trajectory_map(card.get("trajectory_map"), tool_spec),
        "anchors": normalize_anchors(card.get("anchors")),
        "modes": normalize_modes(card.get("modes")),
        "provenance": {"paths": _provenance_paths(card)},
        "_fallback": is_fallback,
    }
    known_anchors = {a["qualname"] for a in normalized["anchors"]}
    for path in normalized["trajectory_map"]:
        path["anchors"] = [q for q in path["anchors"] if q in known_anchors]
        for step in path["steps"]:
            step["anchors"] = [q for q in step["anchors"] if q in known_anchors]
    return normalized
