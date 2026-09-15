"""Written deterministically to ``MEDIA_ROOT/codebase/<repo_id>/``; every claim
is anchored to a real source ``path#Lstart-Lend``.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from django.conf import settings

from overbae.services.artifact_model import Artifact, Provenance
from overbae.services.tool_names import canonical_tool_name

logger = logging.getLogger(__name__)

CARD_SCHEMA_VERSION = 1

# CodebaseSource copies only capability_card.json / io_schema.json into the workshop dir.
CAPABILITY_CARD_FILE = "capability_card.json"
IO_SCHEMA_FILE = "io_schema.json"
TOOL_SPEC_FILE = "tool_spec.json"
VOCABULARY_FILE = "vocabulary.json"
PROMPT_SPEC_FILE = "prompt_spec.json"
LLM_UTILITIES_FILE = "llm_utilities.json"
MANIFEST_FILE = "codebase_manifest.json"

# Matches ``path/to/file.py#L12-L48`` or ``...#L12``.
_PROVENANCE_RE = re.compile(r".+#L\d+(?:-L?\d+)?$")


def codebase_bundle_dir(repo_id: str) -> Path:
    return Path(settings.MEDIA_ROOT) / "codebase" / str(repo_id)


def _provenance_paths(card: dict[str, Any]) -> list[str]:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return []
    paths = prov.get("paths")
    if not isinstance(paths, list):
        return []
    return [str(p).strip() for p in paths if str(p).strip()]


def validate_capability_card(card: Any) -> list[str]:
    """Empty ⇒ valid. Strict enough to trigger one corrective turn, lenient
    enough not to loop."""
    if not isinstance(card, dict):
        return ["capability_card must be a JSON object"]

    errors: list[str] = []
    for key in ("task", "modality", "domain"):
        value = card.get(key)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"'{key}' must be a non-empty string")

    for key in ("input_schema", "output_fields"):
        value = card.get(key)
        if not isinstance(value, dict) or not value:
            errors.append(f"'{key}' must be a non-empty object mapping field -> description")

    expected = card.get("expected_output")
    if isinstance(expected, str):
        if not expected.strip():
            errors.append("'expected_output' must describe what a good output row looks like")
    elif isinstance(expected, dict):
        desc = expected.get("description")
        if not isinstance(desc, str) or not desc.strip():
            errors.append("'expected_output.description' must be a non-empty string")
    else:
        errors.append("'expected_output' must be a string or an object with a 'description'")

    paths = _provenance_paths(card)
    if not paths:
        errors.append("'provenance.paths' must name at least one source span")
    elif not any(_PROVENANCE_RE.match(p) for p in paths):
        errors.append("'provenance.paths' entries must look like 'path/to/file.py#L12-L48'")

    # Absent trajectory_map is fine (older cards, single-path agents); only a
    # malformed present one earns the corrective turn.
    anchors = card.get("anchors")
    if anchors is not None and not isinstance(anchors, list):
        errors.append("'anchors' must be a list of {qualname, kind, file} objects")

    trajectory_map = card.get("trajectory_map")
    if trajectory_map is not None and not isinstance(trajectory_map, list):
        errors.append("'trajectory_map' must be a list of path objects")
    for i, entry in enumerate(trajectory_map if isinstance(trajectory_map, list) else []):
        if (
            not isinstance(entry, dict)
            or not str(entry.get("id") or entry.get("name") or "").strip()
        ):
            errors.append(f"'trajectory_map[{i}]' must be an object with an 'id'")
            break
        terminal = entry.get("terminal") if isinstance(entry.get("terminal"), dict) else {}
        kind = str(terminal.get("kind") or "").strip().lower()
        if kind not in TERMINAL_KINDS:
            errors.append(
                f"'trajectory_map[{i}].terminal.kind' must be one of {', '.join(TERMINAL_KINDS)}"
            )
            break
        bad_step = next(
            (
                s
                for s in (entry.get("sequence") if isinstance(entry.get("sequence"), list) else [])
                if not isinstance(s, str)
                and not (isinstance(s, dict) and str(s.get("step") or "").strip())
            ),
            None,
        )
        if bad_step is not None:
            errors.append(
                f"'trajectory_map[{i}].sequence' entries must be milestone strings or "
                "backbone step objects with a non-empty 'step'"
            )
            break

    return errors


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


# Per-invocation cost driver.
CARDINALITIES: tuple[str, ...] = ("per_run", "per_row", "per_candidate", "unknown")


def _coerce_cardinality(value: Any) -> str:
    cardinality = str(value or "").strip().lower()
    return cardinality if cardinality in CARDINALITIES else "unknown"


def normalize_llm_utilities(value: Any) -> list[dict[str, Any]]:
    """Single-shot helpers are deliberately NOT promoted to top-level Capability
    rows — they stay in the manifest under their parent via ``called_by``."""
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for util in value:
        if not isinstance(util, dict):
            continue
        name = str(util.get("name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "purpose": str(util.get("purpose") or "").strip(),
                "called_by": str(util.get("called_by") or "").strip(),
                "source_path": str(util.get("source_path") or "").strip(),
                "entrypoint_fn": str(util.get("entrypoint_fn") or "").strip(),
                "model": str(util.get("model") or "").strip(),
                "io_contract": str(util.get("io_contract") or "").strip(),
                "cardinality": _coerce_cardinality(util.get("cardinality")),
                "prompt_excerpt": str(util.get("prompt_excerpt") or "").strip(),
                "structured_output": bool(util.get("structured_output")),
                "provenance": {"paths": _provenance_paths(util)},
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


def build_fallback_card(capability_item: dict[str, Any]) -> dict[str, Any]:
    """Fallback when no valid card exists after the corrective turn; marked
    ``_fallback`` so consumers can tell it was reconstructed."""
    name = (capability_item.get("name") or "capability").strip()
    description = (capability_item.get("description") or "").strip()
    capability_desc = capability_item.get("capability_description")
    capability_desc = capability_desc if isinstance(capability_desc, dict) else {}
    source_path = (capability_item.get("source_path") or "").strip()

    input_schema = _as_schema_dict(capability_desc.get("inputs")) or {
        "input": "Primary input the capability receives (reconstructed; schema not declared in repo)."
    }
    output_fields = _as_schema_dict(capability_desc.get("outputs")) or {
        "output": "Primary output the capability produces (reconstructed; schema not declared)."
    }

    tools_summary = (capability_item.get("tools_summary") or "").strip()
    tool_spec: list[dict[str, Any]] = []
    if tools_summary:
        for part in re.split(r"[\n;,]", tools_summary):
            part = part.strip()
            if part:
                tool_spec.append({"name": part[:80], "purpose": "", "args": ""})

    # Bare path, no line range: a fabricated ``#L1-L1`` would flow into the graph
    # as a real code_span anchor.
    provenance_paths = [source_path] if source_path else []

    return {
        "_fallback": True,
        "task": description or f"{name}: purpose not recovered from source.",
        "modality": "text",
        "domain": capability_desc.get("purpose") or "unknown",
        "input_schema": input_schema,
        "output_fields": output_fields,
        "expected_output": {
            "description": (
                "A row whose output fields are present, well-formed, and consistent with the "
                "input (reconstructed from discovery; exact semantics not declared in repo)."
            ),
            "example": None,
            "quality_signals": [],
        },
        "tool_spec": tool_spec,
        "vocabulary": {},
        "success_criteria": [],
        "failure_modes": [],
        "output_schema": normalize_output_schema(None),
        "constraints": [],
        "tool_protocol": [],
        "trajectory_map": [],
        "modes": normalize_modes(capability_item.get("modes")),
        "provenance": {"paths": provenance_paths},
    }


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


def card_summary_line(card: dict[str, Any]) -> str:
    expected = card.get("expected_output") or {}
    expected_desc = expected.get("description") if isinstance(expected, dict) else str(expected)
    expected_desc = (expected_desc or "").strip()
    task = (card.get("task") or "").strip()
    parts = [
        f"capability ({card.get('modality') or '?'}/{card.get('domain') or '?'}): {task}".strip(),
    ]
    if expected_desc:
        parts.append(f"good output = {expected_desc[:160]}")
    return " | ".join(p for p in parts if p)


def _io_schema_of(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "input_schema": card.get("input_schema") or {},
        "output_fields": card.get("output_fields") or {},
        "expected_output": card.get("expected_output") or {},
        "output_schema": normalize_output_schema(card.get("output_schema")),
    }


def _artifact_entry(
    *, slug: str, prov: Provenance, kind: str, suffix: str, summary: str, content: Any
) -> dict[str, Any]:
    art = Artifact(
        id=f"{slug}:{suffix}",
        kind=kind,
        path=f"{slug}/{suffix}.json",
        summary=summary,
        provenance=prov,
    )
    out = art.to_manifest_entry()
    out["capability_slug"] = slug
    out["content"] = content
    return out


def _artifact_entries(*, repo_id: str, capabilities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Content is embedded because the standalone code-context tool server has
    no ORM — only the copied ``codebase_manifest.json``."""
    entries: list[dict[str, Any]] = []
    source_id = f"codebase:{repo_id}"
    for capability in capabilities:
        slug = capability["slug"]
        card = capability["card"]
        prov = Provenance(
            source_id=source_id,
            produced_by="codebase_agent",
            spans=tuple(_provenance_paths(card)),
        )
        entries.append(
            _artifact_entry(
                slug=slug,
                prov=prov,
                kind="capability_card",
                suffix="capability_card",
                summary=card_summary_line(card),
                content=card,
            )
        )
        entries.append(
            _artifact_entry(
                slug=slug,
                prov=prov,
                kind="io_schema",
                suffix="io_schema",
                summary="input/output contract + expected_output",
                content=_io_schema_of(card),
            )
        )
        if card.get("tool_spec"):
            entries.append(
                _artifact_entry(
                    slug=slug,
                    prov=prov,
                    kind="tool_spec",
                    suffix="tool_spec",
                    summary="tools the capability calls",
                    content=card["tool_spec"],
                )
            )
        if card.get("vocabulary"):
            entries.append(
                _artifact_entry(
                    slug=slug,
                    prov=prov,
                    kind="vocabulary",
                    suffix="vocabulary",
                    summary="domain term glossary",
                    content=card["vocabulary"],
                )
            )
        prompt_spec = capability.get("prompt_spec")
        if prompt_spec:
            entries.append(
                _artifact_entry(
                    slug=slug,
                    prov=prov,
                    kind="prompt_spec",
                    suffix="prompt_spec",
                    summary="system prompt / policy excerpt",
                    content=prompt_spec,
                )
            )
    return entries


def _llm_utilities_entry(llm_utilities: list[dict[str, Any]]) -> dict[str, Any]:
    """A plain entry, not an :class:`Artifact`, so the ``llm_utility`` label
    survives without ``ARTIFACT_KINDS`` membership."""
    return {
        "id": "repo:llm_utilities",
        "kind": "llm_utility",
        "path": LLM_UTILITIES_FILE,
        "summary": f"{len(llm_utilities)} single-shot LLM utilit(y/ies) folded under parent capabilities",
        "schema_version": CARD_SCHEMA_VERSION,
        "content": llm_utilities,
    }


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def write_codebase_bundle(
    repo_id: str,
    capabilities: list[dict[str, Any]],
    *,
    head_sha: str = "",
    repo_full_name: str = "",
    llm_utilities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``capabilities``: ``{"slug", "name", "card", "prompt_spec"?}`` dicts; the
    FIRST is the primary, whose card becomes the top-level ``capability_card.json``."""
    utilities = normalize_llm_utilities(llm_utilities)
    if not capabilities:
        return {"dir": str(codebase_bundle_dir(repo_id)), "files": {}, "manifest": None}

    bundle_dir = codebase_bundle_dir(repo_id)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    primary = capabilities[0]["card"]
    files: dict[str, str] = {}

    primary_card_path = bundle_dir / CAPABILITY_CARD_FILE
    _write_json(primary_card_path, primary)
    files["capability_card"] = str(primary_card_path)

    io_path = bundle_dir / IO_SCHEMA_FILE
    _write_json(io_path, _io_schema_of(primary))
    files["io_schema"] = str(io_path)

    if primary.get("tool_spec"):
        tool_path = bundle_dir / TOOL_SPEC_FILE
        _write_json(tool_path, primary["tool_spec"])
        files["tool_spec"] = str(tool_path)
    if primary.get("vocabulary"):
        vocab_path = bundle_dir / VOCABULARY_FILE
        _write_json(vocab_path, primary["vocabulary"])
        files["vocabulary"] = str(vocab_path)
    if capabilities[0].get("prompt_spec"):
        prompt_path = bundle_dir / PROMPT_SPEC_FILE
        _write_json(prompt_path, capabilities[0]["prompt_spec"])
        files["prompt_spec"] = str(prompt_path)
    if utilities:
        util_path = bundle_dir / LLM_UTILITIES_FILE
        _write_json(util_path, utilities)
        files["llm_utilities"] = str(util_path)

    artifacts = _artifact_entries(repo_id=str(repo_id), capabilities=capabilities)
    if utilities:
        artifacts.append(_llm_utilities_entry(utilities))

    manifest = {
        "schema_version": CARD_SCHEMA_VERSION,
        "repo_id": str(repo_id),
        "repo_full_name": repo_full_name,
        "head_sha": head_sha,
        "primary_capability_slug": capabilities[0]["slug"],
        "capabilities": [
            {"slug": a["slug"], "name": a.get("name") or a["slug"], "card": a["card"]}
            for a in capabilities
        ],
        "llm_utilities": utilities,
        "artifacts": artifacts,
    }
    manifest_path = bundle_dir / MANIFEST_FILE
    _write_json(manifest_path, manifest)
    files["manifest"] = str(manifest_path)

    return {"dir": str(bundle_dir), "files": files, "manifest": manifest}


def load_codebase_bundle(repo_id: str, capability_slug: str | None = None) -> dict[str, Any] | None:
    """Selects the card matching ``capability_slug``, else the primary."""
    bundle_dir = codebase_bundle_dir(repo_id)
    manifest_path = bundle_dir / MANIFEST_FILE
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("codebase bundle manifest unreadable for repo %s", repo_id)
        return None

    # Manifests written before the capability rename are files on disk no
    # migration reaches — read their old key.
    capabilities = manifest.get("capabilities") or manifest.get("agents") or []
    selected = None
    if capability_slug:
        selected = next((a for a in capabilities if a.get("slug") == capability_slug), None)
    if selected is None and capabilities:
        selected = capabilities[0]
    if selected is None:
        return None

    card = selected.get("card") or {}
    return {
        "dir": str(bundle_dir),
        "repo_id": str(repo_id),
        "capability_slug": selected.get("slug"),
        "capability_name": selected.get("name"),
        "card": card,
        "io_schema": _io_schema_of(card),
        "tool_spec": card.get("tool_spec") or [],
        "vocabulary": card.get("vocabulary") or {},
        "modes": card.get("modes") or [],
        "llm_utilities": manifest.get("llm_utilities") or [],
        "manifest": manifest,
    }
