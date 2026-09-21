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


def prepare_examples(frame: pd.DataFrame, intent: str) -> pd.DataFrame:
    if intent not in {"train", "eval"}:
        raise ValueError("Choose train or eval before preparing examples.")
    prepared = []
    for record in frame.to_dict(orient="records"):
        row = normalize_record(record)
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
