from __future__ import annotations

import copy
import json
import math

import pandas as pd


def missing(value) -> bool:
    return (
        value is None
        or value is pd.NA
        or (isinstance(value, float) and math.isnan(value))
        or (isinstance(value, str) and not value.strip())
    )


def decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def normalize_record(record: dict) -> dict:
    row = copy.deepcopy(record)
    for key in ("messages", "tools"):
        if key in row:
            row[key] = None if missing(row[key]) else decode(row[key])
    payload = decode(row.get("input"))
    if isinstance(payload, dict) and "messages" in payload:
        for key in ("messages", "tools"):
            if key in payload:
                payload[key] = None if missing(payload[key]) else decode(payload[key])
        row["input"] = payload
    return row


def instructions(record: dict) -> list:
    transcript = messages(record.get("messages")) or messages(record.get("input"))
    if transcript:
        return [
            {"role": turn["role"], "content": turn.get("content")}
            for turn in transcript
            if turn["role"] in {"system", "developer"}
        ]
    payload = decode(record.get("input"))
    values = payload if isinstance(payload, dict) else record
    return [
        {"role": "system", "content": values[key]}
        for key in ("system_prompt", "instruction")
        if not missing(values.get(key))
    ]


def messages(value) -> list[dict]:
    value = decode(value)
    if isinstance(value, dict):
        value = decode(value.get("messages"))
    if (
        isinstance(value, list)
        and value
        and all(isinstance(turn, dict) and turn.get("role") for turn in value)
    ):
        return value
    return []


def input_objects(record: dict) -> list[dict]:
    values = [decode(record.get("input")), decode(record.get("metadata")), record]
    transcript = messages(record.get("messages")) or messages(record.get("input"))
    values.extend(decode(turn.get("content")) for turn in transcript if turn["role"] == "user")
    return [value for value in values if isinstance(value, dict)]


def matches_reference(turn: dict, reference) -> bool:
    if turn.get("role") != "assistant" or missing(reference):
        return False
    response = turn if turn.get("tool_calls") else turn.get("content")
    return decode(response) == decode(reference)


def identifier_only(value) -> bool:
    transcript = messages(value)
    if transcript:
        payloads = [turn.get("content") for turn in transcript if turn["role"] == "user"]
        return (
            bool(payloads)
            and all(identifier_only(payload) for payload in payloads)
            and not any(turn["role"] == "tool" for turn in transcript)
        )
    value = decode(value)
    if not isinstance(value, dict) or not value:
        return False
    keys = set(value) - {"system_prompt", "instruction", "mode", "task_type"}
    return bool(keys) and all(key == "id" or key.endswith("_id") for key in keys)


def native_decision(record):
    decision = decode(record.get("decision"))
    if isinstance(decision, dict):
        return decision
    payload = decode(record.get("input"))
    if isinstance(payload, dict) and isinstance(payload.get("decision"), dict):
        reference = decode(record.get("expected_output"))
        if isinstance(reference, dict) and set(reference) == {"mean", "values"}:
            return {
                **payload["decision"],
                "target_mean": reference["mean"],
                "option_values": reference["values"],
                "target_semantics": "ordinal_mean",
            }
        return {
            **payload["decision"],
            "target_probabilities": reference.get("probabilities")
            if isinstance(reference, dict)
            else None,
        }
    if not {"state", "question", "kind", "options", "target"} <= record.keys():
        return None
    if not isinstance(record.get("kind"), str):
        return None
    target = decode(record["target"])
    options = decode(record["options"])
    if record["kind"] == "noul":
        if isinstance(target, list) and len(target) == 1 and type(target[0]) in {int, float}:
            options, target = ["No", "Yes"], [1 - target[0], target[0]]
        else:
            # Ambiguous binary wire data stays technically invalid, never silently reinterpreted.
            options = []
    return {
        "state": "" if record["state"] is None else record["state"],
        "question": record["question"],
        "kind": record["kind"],
        "options": options,
        "target_probabilities": target,
    }


MAPPING_FIELDS = {
    "messages",
    "tools",
    "input",
    "expected_output",
    "model_expected_output",
    "decision.state",
    "decision.question",
    "decision.kind",
    "decision.options",
    "decision.target_probabilities",
    "decision.target_mean",
    "decision.option_values",
    "decision.target_semantics",
    "decision.target_provenance",
    "decision.weight",
}

TEXT_MAPPING_FIELDS = {
    "decision.state",
    "decision.question",
    "decision.kind",
    "decision.target_semantics",
}


def validate_mapping(mapping, constants):
    if not isinstance(mapping, dict) or not isinstance(constants, dict):
        raise ValueError("Mappings and constants must be objects.")
    if set(mapping) & set(constants) or (set(mapping) | set(constants)) - MAPPING_FIELDS:
        raise ValueError(
            "Mappings use destination -> source path. Choose distinct destination fields from: "
            + ", ".join(sorted(MAPPING_FIELDS))
        )
    if any(
        not isinstance(path, str) or not path.strip() or len(path) > 300
        for path in mapping.values()
    ):
        raise ValueError("Each mapped source must be a nonempty column path.")
    if any(
        key
        not in {
            "decision.state",
            "decision.kind",
            "decision.options",
            "decision.option_values",
            "decision.target_semantics",
        }
        for key in constants
    ):
        raise ValueError(
            "Constants may declare decision state, kind, ordered options, scale or target meaning; never targets."
        )


def field_value(record, path, *, decode_result=True):
    if path in record:
        return decode(record[path]) if decode_result else record[path]
    value = record
    for part in path.split("."):
        value = decode(value)
        if not isinstance(value, dict) or part not in value:
            raise ValueError(f"Missing mapped field: {path}")
        value = value[part]
    return decode(value) if decode_result else value


def mapped_record(record, mapping, constants):
    row = copy.deepcopy(record)
    assignments = {
        **{
            target: field_value(record, source, decode_result=target not in TEXT_MAPPING_FIELDS)
            for target, source in mapping.items()
        },
        **constants,
    }
    for target, value in assignments.items():
        parts = target.split(".")
        parent = row
        for part in parts[:-1]:
            existing = decode(parent.get(part))
            if missing(existing):
                parent[part] = {}
            elif isinstance(existing, dict):
                parent[part] = existing
            else:
                raise ValueError(f"Mapping conflicts with existing {part}.")
            parent = parent[part]
        key = parts[-1]
        existing = parent.get(key)
        if target not in TEXT_MAPPING_FIELDS:
            existing = decode(existing)
        if key in parent and not missing(parent[key]) and existing != value:
            raise ValueError(f"Mapping conflicts with existing {target}.")
        parent[key] = value
    return row


def prepare_examples(
    frame: pd.DataFrame, intent: str, mapping=None, constants=None
) -> pd.DataFrame:
    if intent not in {"train", "eval"}:
        raise ValueError("Choose train or eval before preparing examples.")
    prepared = []
    mapping, constants = mapping or {}, constants or {}
    validate_mapping(mapping, constants)
    for record in frame.to_dict(orient="records"):
        row = normalize_record(mapped_record(record, mapping, constants))
        decision = native_decision(row)
        if decision is not None:
            row["decision"] = decision
            if intent == "eval":
                row["input"] = {
                    "decision": {
                        key: decision.get(key) for key in ("state", "question", "kind", "options")
                    }
                }
                row["expected_output"] = (
                    {"mean": decision["target_mean"], "values": decision.get("option_values")}
                    if "target_mean" in decision
                    else {"probabilities": decision.get("target_probabilities")}
                )
            prepared.append(row)
            continue
        transcript = messages(row.get("messages"))
        complete_transcript = bool(transcript)
        if not transcript:
            transcript = messages(row.get("input"))
            if (
                intent == "eval"
                and not missing(row.get("expected_output"))
                and not (transcript and matches_reference(transcript[-1], row["expected_output"]))
            ):
                prepared.append(row)
                continue
        if not transcript:
            prepared.append(row)
            continue
        if intent == "eval":
            prefix = transcript
            if transcript[-1]["role"] == "assistant":
                target = transcript[-1]
                response = target if target.get("tool_calls") else target.get("content") or target
                if missing(row.get("expected_output")):
                    row["expected_output"] = response
                elif decode(row["expected_output"]) != decode(response):
                    row["model_expected_output"] = response
                prefix = transcript[:-1]
            payload = {"messages": prefix}
            original = decode(row.get("input"))
            tools = row.get("tools")
            if missing(tools) and isinstance(original, dict):
                tools = original.get("tools")
            if not missing(tools):
                payload["tools"] = tools
            row["input"] = payload
            row.pop("messages", None)
            row.pop("tools", None)
        else:
            if (not complete_transcript or transcript[-1]["role"] != "assistant") and not missing(
                row.get("expected_output")
            ):
                target = row.get("model_expected_output")
                if missing(target):
                    target = row["expected_output"]
                content = (
                    target if isinstance(target, str) else json.dumps(target, ensure_ascii=False)
                )
                turn = (
                    target
                    if isinstance(target, dict) and target.get("role") == "assistant"
                    else {"role": "assistant", "content": content}
                )
                transcript = [*transcript, turn]
            row["messages"] = transcript
            original = decode(row.get("input"))
            if missing(row.get("tools")) and isinstance(original, dict) and original.get("tools"):
                row["tools"] = original["tools"]
            row.pop("input", None)
            row.pop("expected_output", None)
            row.pop("model_expected_output", None)
        prepared.append(row)
    return pd.DataFrame(prepared, index=frame.index)
