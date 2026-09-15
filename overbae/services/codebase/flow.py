from __future__ import annotations

from typing import Any

from overbae.services.codebase.artifacts import (
    normalize_modes,
    normalize_tool_spec,
    normalize_trajectory_map,
)

# Input-schema cues that mean the capability consumes uploaded files/documents.
_FILE_CUES = (
    "file",
    "files",
    "document",
    "documents",
    "pdf",
    "upload",
    "attachment",
    "image",
    "audio",
    "video",
    "csv",
    "jsonl",
)


def _as_str_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _expected_output(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "description": str(value.get("description") or "").strip(),
            "example": value.get("example"),
            "quality_signals": _str_list(value.get("quality_signals")),
        }
    if isinstance(value, str) and value.strip():
        return {"description": value.strip(), "example": None, "quality_signals": []}
    return {"description": "", "example": None, "quality_signals": []}


def _takes_files(modality: str, input_schema: dict[str, Any]) -> bool:
    haystacks = [modality.lower()]
    for key, val in input_schema.items():
        haystacks.append(str(key).lower())
        haystacks.append(str(val).lower())
    blob = " ".join(haystacks)
    return any(cue in blob for cue in _FILE_CUES)


def tool_names_from_config(config: Any) -> list[str]:
    """Tool names from ``Capability.tool_config``, tolerant of every stored shape:
    ``{"expected_tools": [...]}``, ``{"tools": [...]}``, or a bare list of names,
    ``{name}``, or OpenAI ``{function: {name}}`` defs."""
    if isinstance(config, dict):
        entries = config.get("expected_tools") or config.get("tools") or []
    elif isinstance(config, list):
        entries = config
    else:
        entries = []
    names: list[str] = []
    for entry in entries:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            fn = entry.get("function") if isinstance(entry.get("function"), dict) else entry
            name = fn.get("name") or ""
        else:
            continue
        name = str(name).strip()
        if name and name not in names:
            names.append(name)
    return names


def _capability_card(capability: Any) -> dict[str, Any]:
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    card = meta.get("capability_card")
    return card if isinstance(card, dict) else {}


def capability_tool_names(capability: Any) -> list[str]:
    """Declared tool names for fit checks; card ``tool_spec`` wins over ``tool_config``."""
    from_card = [
        tool["name"]
        for tool in normalize_tool_spec(_capability_card(capability).get("tool_spec"))
        if tool.get("name")
    ]
    if from_card:
        return from_card
    return tool_names_from_config(getattr(capability, "tool_config", None))


def capability_input_keys(capability: Any) -> list[str]:
    """Declared input keys for fit checks, from the card or the capture schema."""
    card = _capability_card(capability)
    schema = _as_str_dict(card.get("input_schema")) or _as_str_dict(
        getattr(capability, "input_schema", None)
    )
    if schema:
        return list(schema.keys())
    keys = getattr(capability, "dataset_input_keys", None) or []
    out: list[str] = []
    for key in keys:
        name = str(key).strip()
        if name and name not in out:
            out.append(name)
    return out


def build_capability_flow(capability: Any) -> dict[str, Any]:
    """Always non-null, even with no card."""
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    card = _capability_card(capability)
    has_card = bool(card)
    is_fallback = bool(card.get("_fallback"))

    modes = normalize_modes(card.get("modes") or meta.get("modes"))

    input_schema = _as_str_dict(card.get("input_schema")) or _as_str_dict(capability.input_schema)
    output_fields = _as_str_dict(card.get("output_fields")) or _as_str_dict(
        capability.output_fields
    )

    modality = str(card.get("modality") or "").strip()
    domain = str(card.get("domain") or "").strip()
    task = str(card.get("task") or capability.description or "").strip()

    llm_utilities: list[dict[str, Any]] = []

    if has_card and not is_fallback:
        source = "capability_card"
    elif is_fallback:
        source = "fallback_card"
    else:
        source = "capability_fields"

    tool_spec = normalize_tool_spec(card.get("tool_spec"))
    return {
        "has_card": has_card and not is_fallback,
        "is_fallback": is_fallback,
        "source": source,
        "task": task,
        "modality": modality,
        "domain": domain,
        "model": str(capability.model or "").strip(),
        "system_prompt": str(meta.get("system_prompt") or "").strip(),
        "system_prompt_excerpt": str(meta.get("system_prompt_excerpt") or "").strip(),
        "takes_files": _takes_files(modality, input_schema),
        "input_schema": input_schema,
        "output_fields": output_fields,
        "expected_output": _expected_output(card.get("expected_output")),
        "tool_spec": tool_spec,
        "modes": modes,
        "llm_utilities": llm_utilities,
        "vocabulary": _as_str_dict(card.get("vocabulary")),
        "success_criteria": _str_list(card.get("success_criteria")),
        "failure_modes": _str_list(card.get("failure_modes")),
        "trajectory_map": normalize_trajectory_map(card.get("trajectory_map"), tool_spec),
        "provenance_paths": _provenance_paths(card),
    }


def _provenance_paths(card: dict[str, Any]) -> list[str]:
    prov = card.get("provenance")
    if not isinstance(prov, dict):
        return []
    return _str_list(prov.get("paths"))
