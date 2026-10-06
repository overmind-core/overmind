"""Derive a capability card from decorator-declared symbols.

``derive_card`` is deterministic (signatures, docstrings, prompts, tools).
``author_card`` fills semantic fields with one generative call
(``openai/gpt-5-mini`` via OpenRouter / ``call_llm``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

GENERATOR = "derive_card@v1"


def derive_card(symbols: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the structural half of a capability card from declared symbols."""
    entry = next(
        (s for s in symbols if s.get("role") == "capability"), symbols[0] if symbols else {}
    )
    tools = [s for s in symbols if s.get("role") == "tool"]
    llms = [s for s in symbols if s.get("role") == "llm"]

    input_schema: dict[str, str] = {}
    for param in (entry.get("signature") or {}).get("params") or []:
        if not isinstance(param, dict) or not param.get("name"):
            continue
        input_schema[str(param["name"])] = str(param.get("type") or "any")

    returns = str((entry.get("signature") or {}).get("returns") or "").strip()
    output_fields: dict[str, str] = {}
    if returns:
        output_fields["result"] = returns

    tool_spec = []
    for tool in tools:
        sig = tool.get("signature") or {}
        args = []
        for param in sig.get("params") or []:
            if not isinstance(param, dict):
                continue
            args.append(
                {
                    "name": str(param.get("name") or ""),
                    "type": str(param.get("type") or "any"),
                    "required": bool(param.get("required", True)),
                    "description": "",
                }
            )
        bare = str(tool.get("qualname") or "").rsplit(".", 1)[-1]
        tool_spec.append(
            {
                "name": bare,
                "purpose": str(tool.get("description") or tool.get("name") or bare),
                "args": ", ".join(a["name"] for a in args),
                "side_effect": "none",
                "returns": str(sig.get("returns") or ""),
                "arguments": args,
                "integration": "",
                "cluster": "",
                "provenance": [
                    f"{tool.get('file', '')}#L{tool.get('line_start', 0)}-L{tool.get('line_end', 0)}"
                ],
            }
        )

    anchors = []
    for sym in symbols:
        if not sym.get("qualname"):
            continue
        kind = (
            "entry_point"
            if sym.get("role") == "capability"
            else ("tool" if sym.get("role") == "tool" else "function")
        )
        anchors.append(
            {
                "qualname": sym["qualname"],
                "kind": kind,
                "file": f"{sym.get('file', '')}#L{sym.get('line_start', 0)}-L{sym.get('line_end', 0)}",
            }
        )

    trajectory_map = []
    tasks = [s for s in symbols if s.get("role") == "task" and s.get("task_key")]
    if tasks:
        for task in tasks:
            key = str(task["task_key"])
            reachable = [task["qualname"], *(task.get("calls") or [])]
            trajectory_map.append(
                {
                    "id": key,
                    "name": str(task.get("name") or key),
                    "mode": "",
                    "claim": "code_path",
                    "prompt_quote": "",
                    "routing": "",
                    "sequence": [
                        {
                            "step": str(task.get("description") or task.get("name") or key),
                            "kind": "agent_step",
                            "anchors": [
                                q for q in reachable if any(a["qualname"] == q for a in anchors)
                            ],
                            "input": "",
                            "action": "",
                            "output": "",
                            "may_use": [],
                        }
                    ],
                    "anchors": [q for q in reachable if any(a["qualname"] == q for a in anchors)],
                    "tools": [
                        str(t.get("qualname") or "").rsplit(".", 1)[-1]
                        for t in tools
                        if t.get("qualname") in reachable
                    ],
                    "terminal": {"kind": "emits_record", "description": returns or ""},
                    "divergences": [],
                    "provenance": [
                        f"{task.get('file', '')}#L{task.get('line_start', 0)}-L{task.get('line_end', 0)}"
                    ],
                }
            )
    elif entry.get("qualname"):
        reachable = [entry["qualname"], *(entry.get("calls") or [])]
        trajectory_map.append(
            {
                "id": "main",
                "name": "Main path",
                "mode": "",
                "claim": "code_path",
                "prompt_quote": "",
                "routing": "",
                "sequence": [
                    {
                        "step": str(entry.get("description") or entry.get("name") or "run"),
                        "kind": "agent_step",
                        "anchors": [
                            q for q in reachable if any(a["qualname"] == q for a in anchors)
                        ],
                        "input": "",
                        "action": "",
                        "output": "",
                        "may_use": [],
                    }
                ],
                "anchors": [q for q in reachable if any(a["qualname"] == q for a in anchors)],
                "tools": [str(t.get("qualname") or "").rsplit(".", 1)[-1] for t in tools],
                "terminal": {"kind": "emits_record", "description": returns or ""},
                "divergences": [],
                "provenance": [
                    f"{entry.get('file', '')}#L{entry.get('line_start', 0)}-L{entry.get('line_end', 0)}"
                ],
            }
        )

    system_prompt = ""
    for sym in [*llms, entry]:
        if sym.get("prompt_template"):
            system_prompt = str(sym["prompt_template"])
            break

    return {
        "task": str(entry.get("description") or entry.get("name") or "").strip(),
        "modality": "text",
        "domain": "",
        "input_schema": input_schema,
        "output_fields": output_fields,
        "expected_output": {
            "description": returns or "",
            "example": None,
            "quality_signals": [],
        },
        "tool_spec": tool_spec,
        "vocabulary": {},
        "success_criteria": [],
        "failure_modes": [],
        "output_schema": {},
        "constraints": [
            {
                "rule": str(e.get("spec") or e.get("kind") or ""),
                "type": "output_format",
                "params": {},
                "provenance": [],
            }
            for sym in symbols
            for e in (sym.get("expectations") or [])
            if isinstance(e, dict) and (e.get("spec") or e.get("kind"))
        ],
        "tool_protocol": [],
        "trajectory_map": trajectory_map,
        "anchors": anchors,
        "modes": [],
        "system_prompt": system_prompt,
        "provenance": {
            "paths": [
                f"{entry.get('file', '')}#L{entry.get('line_start', 0)}-L{entry.get('line_end', 0)}"
            ]
            if entry.get("file")
            else []
        },
        "_generator": GENERATOR,
    }


def author_card(
    derived: dict[str, Any], *, evidence: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Fill semantic card fields. Falls back to the derived card on any failure."""
    card = dict(derived)
    try:
        from overbae.core.llms import call_llm
    except Exception:
        logger.debug("author_card: llms unavailable", exc_info=True)
        return card

    symbols = (evidence or {}).get("symbols") or []
    prompt = (
        "You author a capability card for an agent improvement platform. "
        "Given the structural facts below, fill the semantic fields. "
        "Return ONLY a JSON object with keys: task, domain, modality, "
        "expected_output (object with description, example, quality_signals), "
        "success_criteria (string array), failure_modes (string array). "
        "Be concrete and grounded in the facts; invent nothing about tools "
        "or schemas that are not present.\n\n"
        f"STRUCTURAL CARD:\n{json.dumps(derived, default=str)[:8000]}\n\n"
        f"SYMBOLS:\n{json.dumps(symbols, default=str)[:4000]}\n"
    )
    try:
        raw, _meta = call_llm(
            prompt,
            model="gpt-5-mini",
            max_tokens=1500,
        )
    except Exception:
        logger.warning("author_card: generative fill failed", exc_info=True)
        return card

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()
    try:
        authored = json.loads(text)
    except (ValueError, TypeError):
        logger.warning("author_card: non-JSON response")
        return card
    if not isinstance(authored, dict):
        return card

    if isinstance(authored.get("task"), str) and authored["task"].strip():
        card["task"] = authored["task"].strip()
    if isinstance(authored.get("domain"), str):
        card["domain"] = authored["domain"].strip()
    if isinstance(authored.get("modality"), str) and authored["modality"].strip():
        card["modality"] = authored["modality"].strip()
    expected = authored.get("expected_output")
    if isinstance(expected, dict):
        card["expected_output"] = {
            "description": str(
                expected.get("description") or card["expected_output"].get("description") or ""
            ),
            "example": expected.get("example"),
            "quality_signals": [
                str(s) for s in (expected.get("quality_signals") or []) if str(s).strip()
            ],
        }
    if isinstance(authored.get("success_criteria"), list):
        card["success_criteria"] = [str(s) for s in authored["success_criteria"] if str(s).strip()]
    if isinstance(authored.get("failure_modes"), list):
        card["failure_modes"] = [str(s) for s in authored["failure_modes"] if str(s).strip()]
    card["_generator"] = GENERATOR
    return card
