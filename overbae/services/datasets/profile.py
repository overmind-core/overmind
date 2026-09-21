from __future__ import annotations

import hashlib
import json
from collections import Counter

from overbae.services.datasets.examples import (
    decode,
    input_objects,
    instructions,
    messages,
    missing,
)

_GROUP_LIMIT = 16
_EXAMPLE_LIMIT = 8
_TASK_FIELDS = ("mode", "worker_mode", "task_type", "behaviour_key", "capability_id")


def _dump(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "…[truncated; query this row]"


def _shape(value, depth=0):
    value = decode(value)
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict) and depth < 2:
        return {key: _shape(value[key], depth + 1) for key in sorted(value)[:16]}
    if isinstance(value, list):
        return {"type": "array", "item": _shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def _example(record: dict, transcript: list) -> str:
    if not transcript:
        return _clip(_dump({k: v for k, v in record.items() if k != "_overmind_provenance"}), 1200)
    return _clip(
        _dump(
            [
                {
                    "role": turn["role"],
                    "value": _clip(_dump(turn.get("tool_calls") or turn.get("content")), 300),
                }
                for turn in transcript[:6]
            ]
        ),
        1200,
    )


def profile_records(records) -> dict:
    groups = {}
    scanned = other = 0
    totals = Counter()
    for index, record in enumerate(records):
        scanned += 1
        transcript = messages(record.get("messages")) or messages(record.get("input"))
        prompt = instructions(record)
        objects = input_objects(record)
        labels = {
            key: _clip(str(value[key]), 120)
            for value in reversed(objects)
            for key in _TASK_FIELDS
            if not missing(value.get(key))
        }
        payload = decode(record.get("input"))
        raw_tools = record.get("tools")
        if missing(raw_tools) and isinstance(payload, dict):
            raw_tools = payload.get("tools")
        tools = decode(raw_tools)
        target = record.get("model_expected_output")
        if missing(target):
            target = record.get("expected_output")
        if missing(target) and transcript and transcript[-1]["role"] == "assistant":
            target = transcript[-1].get("tool_calls") or transcript[-1].get("content")
        if missing(target):
            target = record.get("output", record.get("response", record.get("completion")))
        output_shape = _shape(target)
        input_shape = (
            [_shape(turn.get("content")) for turn in transcript if turn["role"] == "user"][:4]
            if transcript
            else _shape(payload)
        )
        key = hashlib.sha256(
            _dump([prompt, labels, input_shape, output_shape, tools]).encode()
        ).hexdigest()
        totals["with_messages"] += bool(transcript)
        totals["with_tools"] += not missing(raw_tools) and raw_tools != []
        totals["json_encoded_tools"] += isinstance(raw_tools, str)
        totals["with_recorded_tool_results"] += any(t["role"] == "tool" for t in transcript)
        totals["without_target"] += missing(target)
        if key not in groups:
            # Bottom-k hashes keep complete counts for retained families without favouring file order.
            if len(groups) == _GROUP_LIMIT:
                largest = max(groups)
                if key > largest:
                    other += 1
                    continue
                other += groups.pop(largest)["rows"]
            groups[key] = {
                "family": key[:12],
                "rows": 0,
                "task_labels": labels,
                "instructions": _clip(_dump(prompt), 500),
                "input_shape": _clip(_dump(input_shape), 600),
                "output_shape": _clip(_dump(output_shape), 600),
                "recorded_tool_result_rows": 0,
                "tools": _clip(
                    _dump(
                        [
                            tool.get("function", {}).get("name")
                            for tool in tools
                            if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
                        ]
                    )
                    if isinstance(tools, list)
                    else type(tools).__name__,
                    400,
                ),
                "example_row": record.get("source_row", index),
                "example": _example(record, transcript),
            }
        groups[key]["rows"] += 1
        groups[key]["recorded_tool_result_rows"] += any(t["role"] == "tool" for t in transcript)
    families = list(groups.values())
    for family in families[_EXAMPLE_LIMIT:]:
        family["example"] = "Query example_row for full values."
    return {
        "rows_scanned": scanned,
        "families": families,
        "unlisted_family_rows": other,
        "families_truncated": bool(other),
        "counts": dict(totals),
        "scope": "All rows counted; structural families and clipped examples, not a semantic audit.",
    }
