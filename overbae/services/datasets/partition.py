from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from contextlib import suppress

from overbae.services.datasets.examples import input_objects

_IDENTITY = {"source_row", "trace_id", "source_trace_id", "conversation_id", "_overmind_provenance"}
_OUTPUT = {"output", "expected_output", "answer", "response", "completion", "label", "score"}


def _canonical(value) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = " ".join(value.split())
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def content_key(row: dict, *, include_output: bool = False) -> str:
    if include_output:
        value = {
            k: _canonical(v)
            for k, v in row.items()
            if k not in _IDENTITY and v is not None and not (isinstance(v, float) and math.isnan(v))
        }
        return hashlib.sha256(_canonical(value).encode()).hexdigest()
    source = row.get("input")
    if isinstance(source, str):
        with suppress(ValueError):
            source = json.loads(source)
    messages = row.get("messages") or (source.get("messages") if isinstance(source, dict) else None)
    if isinstance(messages, str):
        with suppress(ValueError):
            messages = json.loads(messages)
    if isinstance(messages, list):
        turns = [m for m in messages if isinstance(m, dict) and m.get("role") != "system"]
        if turns and turns[-1].get("role") == "assistant":
            turns = turns[:-1]
        value = (
            turns[0].get("content") if len(turns) == 1 and turns[0].get("role") == "user" else turns
        )
    else:
        value = next(
            (
                row[k]
                for k in ("input", "question", "prompt", "instruction")
                if row.get(k) is not None
            ),
            None,
        )
        if value is None:
            value = {k: v for k, v in row.items() if k not in _IDENTITY | _OUTPUT}
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def present(value) -> bool:
    return (
        value is not None and value != "" and not (isinstance(value, float) and math.isnan(value))
    )


def contamination_keys(row: dict, group_by=()) -> set[tuple[str, str]]:
    keys = {("content", content_key(row))}
    for column in {"trace_id", "source_trace_id", "conversation_id", *group_by}:
        if present(row.get(column)):
            kind = "trace_id" if column == "source_trace_id" else column
            keys.add((kind, _canonical(row[column])))
    for value in input_objects(row):
        for column in ("packet_id", "onboarding_packet_id", "case_id", "example_id"):
            if present(value.get(column)) and isinstance(value[column], (str, int)):
                kind = "packet_id" if column == "onboarding_packet_id" else column
                keys.add((kind, _canonical(value[column])))
    provenance = row.get("_overmind_provenance")
    if isinstance(provenance, str):
        with suppress(ValueError):
            provenance = json.loads(provenance)
    if isinstance(provenance, dict):
        for prefix in ("seed", "source"):
            for key in provenance.get(f"{prefix}_content_keys", []):
                keys.add(("content", str(key)))
            for kind, value in provenance.get(f"{prefix}_group_keys", []):
                keys.add((str(kind), str(value)))
    return keys


def preserve_lineage(row: dict) -> dict:
    provenance = row.get("_overmind_provenance")
    if isinstance(provenance, str):
        with suppress(ValueError):
            provenance = json.loads(provenance)
    keys = contamination_keys(row)
    return {
        **(provenance if isinstance(provenance, dict) else {}),
        "source_content_keys": sorted(value for kind, value in keys if kind == "content"),
        "source_group_keys": sorted([kind, value] for kind, value in keys if kind != "content"),
    }


def split_rows(
    rows: list[dict],
    *,
    eval_percent: int,
    position: str,
    group_by=(),
    stratify_by=None,
    deduplicate=True,
):
    columns = set().union(*(row.keys() for row in rows)) if rows else set()
    requested = set(group_by) | ({stratify_by} if stratify_by else set())
    if requested - columns:
        raise ValueError("Unknown split columns: " + ", ".join(sorted(requested - columns)))
    if not 1 <= eval_percent <= 99 or position not in {"head", "tail", "random"}:
        raise ValueError("Choose a 1–99% split and head, tail or random position.")
    unique = []
    seen = set()
    for row in rows:
        key = content_key(row, include_output=True)
        if deduplicate and key in seen:
            continue
        seen.add(key)
        unique.append(row)
    n = len(unique)
    if n < 2:
        raise ValueError("Two rows with distinct content are needed to split.")
    parents = list(range(n))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners = {}
    groups = set(group_by) | ({"conversation_id"} & columns) | ({"trace_id"} & columns)
    for index, row in enumerate(unique):
        keys = contamination_keys(row, groups)
        for key in keys:
            if key in owners:
                parents[root(index)] = root(owners[key])
            else:
                owners[key] = index
    components = defaultdict(list)
    for index in range(n):
        components[root(index)].append(index)
    ordered = list(components.values())
    if len(ordered) < 2:
        raise ValueError(
            "All rows belong to one content/group cluster; an independent holdout cannot be created."
        )
    if position == "tail":
        ordered.reverse()
    elif position == "random":
        random.Random(42).shuffle(ordered)
    target = min(max((n * eval_percent + 50) // 100, 1), n - 1)
    totals = (
        Counter(_canonical(row.get(stratify_by)) for row in unique) if stratify_by else Counter()
    )
    held = set()
    labels = Counter()
    for component in ordered:
        if len(held) + len(component) >= n:
            continue
        counts = (
            Counter(_canonical(unique[i].get(stratify_by)) for i in component)
            if stratify_by
            else Counter()
        )
        size_before = abs(len(held) - target) / n
        size_after = abs(len(held) + len(component) - target) / n
        if stratify_by:
            size_before += sum(
                abs(labels[k] - count * eval_percent / 100) / count for k, count in totals.items()
            )
            size_after += sum(
                abs(labels[k] + counts[k] - count * eval_percent / 100) / count
                for k, count in totals.items()
            )
        if size_after < size_before or not held:
            held.update(component)
            labels.update(counts)
    train = [row for i, row in enumerate(unique) if i not in held]
    evaluation = [row for i, row in enumerate(unique) if i in held]
    overlap = {content_key(r) for r in train} & {content_key(r) for r in evaluation}
    report = {
        "source_rows": len(rows),
        "duplicates_removed": len(rows) - n,
        "train_rows": len(train),
        "eval_rows": len(evaluation),
        "target_eval_rows": target,
        "group_by": sorted(groups),
        "stratify_by": stratify_by,
        "content_overlap": len(overlap),
        "group_overlap": 0,
        "basis": "normalised exact input content and group identity",
        "near_duplicate_check": "not_checked",
        "strata": {
            key: {"total": count, "eval": labels[key], "train": count - labels[key]}
            for key, count in totals.items()
        },
    }
    return train, evaluation, report
