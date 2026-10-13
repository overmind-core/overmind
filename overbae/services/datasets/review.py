from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from contextlib import closing
from itertools import islice
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa

from overbae.services.datasets import store
from overbae.services.datasets.partition import preserve_lineage
from sandbox import tables
from sandbox.examples import identifier_only, instructions, messages

PROVENANCE_COLUMN = "_overmind_provenance"
_COVERAGE_COLUMNS = (
    "label",
    "class",
    "intent",
    "language",
    "behaviour",
    "behaviour_key",
    "capability_id",
    "mode",
    "worker_mode",
    "task_type",
)


_IDENTITY = ("trace_id", "source_trace_id", "conversation_id", "human_reviewed")


def finalize(before: Path, output: Path, path: Path, *, group_by=()) -> None:
    """``output`` with lineage and identity carried over from ``before`` by ``source_row``."""
    out_kinds = {c["name"]: c["type"] for c in store.read_manifest(output)}
    before_kinds = {c["name"]: c["type"] for c in store.read_manifest(before)}
    if store.SOURCE_ROW not in before_kinds or store.SOURCE_ROW not in out_kinds:
        with tables.TableWriter(path, store.read_manifest(output)) as writer:
            for batch in tables.batches(output):
                writer.write_table(pa.Table.from_batches([batch]))
        return
    carried = [
        c
        for c in dict.fromkeys([PROVENANCE_COLUMN, *_IDENTITY, *group_by])
        if c != store.SOURCE_ROW and (c == PROVENANCE_COLUMN or c in before_kinds)
    ]
    source_kinds = {**before_kinds, PROVENANCE_COLUMN: "json"}
    kinds = {
        column: tables.union_kind({source_kinds[column], out_kinds[column]})
        if column in out_kinds
        else source_kinds[column]
        for column in carried
    }
    manifest = [
        {"name": name, "type": kinds.get(name, kind)} for name, kind in out_kinds.items()
    ] + [{"name": c, "type": kinds[c]} for c in carried if c not in out_kinds]
    with tempfile.TemporaryDirectory(prefix="overmind-lineage-") as scratch:
        lineage = Path(scratch) / "lineage.parquet"

        def inherit(_position, record):
            if record.get(store.SOURCE_ROW) is None:
                return {}
            return {
                c: store.json_dumps(preserve_lineage(record))
                if c == PROVENANCE_COLUMN
                else record.get(c)
                for c in carried
            }

        _derive(
            before,
            lineage,
            {**{c: source_kinds[c] for c in carried}, PROVENANCE_COLUMN: "string"},
            inherit,
        )
        with (
            closing(store.aligned(lineage, output, carried)) as found,
            tables.TableWriter(path, manifest) as writer,
        ):
            for batch in tables.batches(output):
                inherited = list(islice(found, batch.num_rows))
                arrays = []
                for spec in manifest:
                    name, kind = spec["name"], spec["type"]
                    if name not in kinds:
                        arrays.append(batch.column(name))
                        continue
                    own = (
                        batch.column(name).to_pylist()
                        if name in out_kinds
                        else [None] * batch.num_rows
                    )
                    own_kind = out_kinds.get(name, kind)
                    merged = []
                    for theirs, mine in zip(inherited, own, strict=True):
                        if theirs is not None and theirs[name] is not None:
                            lineage_text = name == PROVENANCE_COLUMN and kind == "json"
                            merged.append(
                                theirs[name] if lineage_text else tables.coerce(theirs[name], kind)
                            )
                        elif mine is None or kind == own_kind:
                            merged.append(mine)
                        else:
                            merged.append(tables.coerce(store.value(mine, own_kind), kind))
                    arrays.append(pa.array(merged, type=tables.ARROW_TYPES[kind]))
                writer.write_table(pa.Table.from_arrays(arrays, schema=writer.schema))


def _derive(source: Path, path: Path, kinds: dict[str, str], derive, columns=None) -> None:
    """``source_row`` and ``derive(position, record)`` for each row of ``source``."""
    frame = store.Frame(source)
    manifest = [{"name": store.SOURCE_ROW, "type": frame.kinds[store.SOURCE_ROW]}] + [
        {"name": name, "type": kind} for name, kind in kinds.items()
    ]
    with tables.TableWriter(path, manifest) as writer:
        writer.write_rows(
            {store.SOURCE_ROW: record.get(store.SOURCE_ROW), **derive(position, record)}
            for position, record in enumerate(frame.records(columns))
        )


def preview_examples(frame: pd.DataFrame) -> list:
    def clip(value):
        if isinstance(value, str):
            return value if len(value) <= 2000 else value[:2000] + "…[preview truncated]"
        if isinstance(value, list):
            return [clip(v) for v in value[:20]]
        if isinstance(value, dict):
            return {k: clip(v) for k, v in list(value.items())[:30]}
        return value

    return clip(json.loads(frame.head(5).to_json(orient="records")))


def distribution(path: Path) -> dict:
    frame = store.Frame(path)
    return {
        column: dict(Counter(str(value) for value in frame.values(column)).most_common(50))
        for column in _COVERAGE_COLUMNS
        if column in frame
    }


def _unique(path: Path) -> bool:
    if store.SOURCE_ROW not in store.Frame(path):
        return False
    con = store.connect(t=path)
    try:
        return con.execute(
            "SELECT count(source_row) = count(DISTINCT source_row) "
            "AND count(*) - count(source_row) <= 1 FROM t"
        ).fetchone()[0]
    finally:
        con.close()


_PROMPT_COLUMNS = [store.SOURCE_ROW, "messages", "input", "system_prompt", "instruction"]
_EXAMPLES = 20


def _digest(value: Any) -> str:
    return hashlib.sha256(store.json_dumps(value).encode()).hexdigest()


def _original(position: int, record: dict) -> dict:
    prompt = instructions(record)
    context = messages(record.get("messages")) or record.get("input")
    return {
        "position": position,
        "prompt": _digest(prompt) if prompt else None,
        "evidence": bool(context) and not identifier_only(context),
    }


def impact(before: Path, after: Path) -> dict:
    tracked = _unique(before) and _unique(after)
    removed: list[int] = []
    inputs: list[int] = []
    evidence_removed: list = []
    prompt_changes: list = []
    rows_removed = rows_added = None
    evidence_count = prompt_count = 0
    if tracked:
        with closing(store.aligned(after, before, [])) as kept:
            missing = (i for i, row in enumerate(kept) if row is None)
            removed = list(islice(missing, 5))
            rows_removed = len(removed) + sum(1 for _ in missing)
        rows_added = 0
        with tempfile.TemporaryDirectory(prefix="overmind-impact-") as scratch:
            digests = Path(scratch) / "digests.parquet"
            kinds = {"position": "integer", "prompt": "string", "evidence": "boolean"}
            _derive(before, digests, kinds, _original, _PROMPT_COLUMNS)
            with closing(store.aligned(digests, after, list(kinds))) as originals:
                rows = store.Frame(after).records(_PROMPT_COLUMNS)
                for n, (original, row) in enumerate(zip(originals, rows, strict=True)):
                    if original is None:
                        rows_added += 1
                        continue
                    if n < 5:
                        inputs.append(original["position"])
                    if original["prompt"] is not None and original["prompt"] != _digest(
                        instructions(row)
                    ):
                        prompt_count += 1
                        if len(prompt_changes) < _EXAMPLES:
                            prompt_changes.append(row[store.SOURCE_ROW])
                    if original["evidence"] and identifier_only(row.get("input")):
                        evidence_count += 1
                        if len(evidence_removed) < _EXAMPLES:
                            evidence_removed.append(row[store.SOURCE_ROW])
    return {
        "rows_before": store.row_count(before),
        "rows_after": store.row_count(after),
        "rows_removed": rows_removed,
        "rows_added": rows_added,
        "identity_preserved": tracked,
        "input_evidence_removed": evidence_count,
        "input_evidence_removed_examples": evidence_removed,
        "instruction_changes": prompt_count,
        "instruction_change_examples": prompt_changes,
        "coverage_before": distribution(before),
        "coverage_after": distribution(after),
        "removed_examples": preview_examples(store.frame_rows(before, removed)),
        "input_examples": preview_examples(store.frame_rows(before, inputs)),
        "output_examples": preview_examples(store.frame_rows(after, range(5))),
    }


def warnings(dataset, cell, *, capability=None) -> list[str]:
    if capability is not None and dataset.capability_id != capability.id:
        return [f"This dataset is not attached to {capability.name}."]
    return []
