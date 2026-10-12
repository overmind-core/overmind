from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import paths, rows, store, tables
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.examples import identifier_only, instructions, messages
from overbae.services.datasets.notebook import runner
from overbae.services.datasets.partition import preserve_lineage

PROVENANCE_COLUMN = "_overmind_provenance"
REQUIRED_CHECKS = {
    "task_alignment": "Task alignment",
    "input_evidence": "Input evidence",
    "answer_support": "Answer support",
    "output_schema": "Output schema",
}
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


def summary(report: dict) -> dict:
    result = {key: value for key, value in report.items() if not key.endswith("_examples")}
    if result.get("semantic_audit"):
        audit = result["semantic_audit"]
        result["semantic_audit"] = {
            "method": audit["method"],
            "contract": audit["contract"],
            "definitions": audit["definitions"],
            "processed_rows": len(audit.get("results", {})),
            "batches": len(audit.get("batches", [])),
        }
    return result


def _changed(a: Any, b: Any, kind_a: str, kind_b: str) -> bool:
    if a == b and kind_a == kind_b:
        return False
    return store.value(a, kind_a) != store.value(b, kind_b)


def same(before: Path, after: Path) -> bool:
    left, right = store.read_manifest(before), store.read_manifest(after)
    if [c["name"] for c in left] != [c["name"] for c in right]:
        return False
    if store.row_count(before) != store.row_count(after):
        return False
    kinds = [(a["type"], b["type"]) for a, b in zip(left, right, strict=True)]
    for a, b in zip(store.raw_rows(before), store.raw_rows(after), strict=True):
        for (kind_a, kind_b), x, y in zip(kinds, a.values(), b.values(), strict=True):
            if _changed(x, y, kind_a, kind_b):
                return False
    return True


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
    carried = list(
        dict.fromkeys(
            [PROVENANCE_COLUMN, *(c for c in (*_IDENTITY, *group_by) if c in before_kinds)]
        )
    )
    source_kinds = {**before_kinds, PROVENANCE_COLUMN: "json"}
    inherited: dict[Any, tuple] = {}
    for record in store.records(before):
        key = record.get(store.SOURCE_ROW)
        if key is None or key in inherited:
            continue
        inherited[key] = tuple(
            store.json_dumps(preserve_lineage(record))
            if column == PROVENANCE_COLUMN
            else record.get(column)
            for column in carried
        )
    kinds = {
        column: tables.union_kind({source_kinds[column], out_kinds[column]})
        if column in out_kinds
        else source_kinds[column]
        for column in carried
    }
    manifest = [
        {"name": name, "type": kinds.get(name, kind)} for name, kind in out_kinds.items()
    ] + [{"name": c, "type": kinds[c]} for c in carried if c not in out_kinds]
    index = {column: i for i, column in enumerate(carried)}
    with tables.TableWriter(path, manifest) as writer:
        for batch in tables.batches(output):
            keys = batch.column(store.SOURCE_ROW).to_pylist()
            found = [inherited.get(key) for key in keys]
            arrays = []
            for spec in manifest:
                name, kind = spec["name"], spec["type"]
                if name not in index:
                    arrays.append(batch.column(name))
                    continue
                i = index[name]
                own = batch.column(name).to_pylist() if name in out_kinds else [None] * len(keys)
                own_kind = out_kinds.get(name, kind)
                merged = []
                for values, mine in zip(found, own, strict=True):
                    theirs = values[i] if values is not None else None
                    if theirs is not None:
                        lineage = name == PROVENANCE_COLUMN and kind == "json"
                        merged.append(theirs if lineage else tables.coerce(theirs, kind))
                    elif mine is None or kind == own_kind:
                        merged.append(mine)
                    else:
                        merged.append(tables.coerce(store.value(mine, own_kind), kind))
                arrays.append(pa.array(merged, type=tables.ARROW_TYPES[kind]))
            writer.write_table(pa.Table.from_arrays(arrays, schema=writer.schema))


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


def _source_rows(path: Path) -> list | None:
    frame = store.Frame(path)
    return list(frame.values(store.SOURCE_ROW)) if store.SOURCE_ROW in frame else None


def _unique(keys: list) -> bool:
    filled = [k for k in keys if k is not None]
    return len(set(filled)) == len(filled) and len(keys) - len(filled) <= 1


_PROMPT_COLUMNS = [store.SOURCE_ROW, "messages", "input", "system_prompt", "instruction"]


def _digest(value: Any) -> str:
    return hashlib.sha256(store.json_dumps(value).encode()).hexdigest()


def impact(before: Path, after: Path) -> dict:
    before_ids, after_ids = _source_rows(before), _source_rows(after)
    tracked = (
        before_ids is not None
        and after_ids is not None
        and _unique(before_ids)
        and _unique(after_ids)
    )
    removed: list[int] = []
    inputs: list[int] = []
    evidence_removed = []
    prompt_changes = []
    rows_added = None
    if tracked:
        kept = set(after_ids)
        known = set(before_ids)
        removed = [i for i, key in enumerate(before_ids) if key not in kept]
        rows_added = sum(1 for key in after_ids if key not in known)
        position = {key: i for i, key in enumerate(before_ids)}
        inputs = [position[key] for key in after_ids[:5] if key in known]
        originals = {}
        for record in store.Frame(before).records(_PROMPT_COLUMNS):
            prompt = instructions(record)
            context = messages(record.get("messages")) or record.get("input")
            originals[record.get(store.SOURCE_ROW)] = (
                _digest(prompt) if prompt else None,
                bool(context) and not identifier_only(context),
            )
        for row in store.Frame(after).records(_PROMPT_COLUMNS):
            original = originals.get(row.get(store.SOURCE_ROW))
            if original is None:
                continue
            prompt, evidence = original
            if prompt is not None and prompt != _digest(instructions(row)):
                prompt_changes.append(row[store.SOURCE_ROW])
            if evidence and identifier_only(row.get("input")):
                evidence_removed.append(row[store.SOURCE_ROW])
    return {
        "rows_before": store.row_count(before),
        "rows_after": store.row_count(after),
        "rows_removed": len(removed) if tracked else None,
        "rows_added": rows_added,
        "identity_preserved": tracked,
        "input_evidence_removed": len(evidence_removed),
        "input_evidence_removed_examples": evidence_removed[:20],
        "instruction_changes": len(prompt_changes),
        "instruction_change_examples": prompt_changes[:20],
        "coverage_before": distribution(before),
        "coverage_after": distribution(after),
        "removed_examples": preview_examples(store.frame_rows(before, removed[:5])),
        "input_examples": preview_examples(store.frame_rows(before, inputs)),
        "output_examples": preview_examples(store.frame_rows(after, range(5))),
    }


def requires_approval(changes: dict, *, allow_exclusions: bool = False) -> bool:
    return bool(
        (changes["rows_removed"] and not allow_exclusions)
        or changes["rows_added"]
        or not changes["identity_preserved"]
        or changes["input_evidence_removed"]
        or changes["instruction_changes"]
    )


def readiness(dataset, cell, *, context: str | None = None) -> dict:
    valid, reason = cell.fits(public_intent(dataset.intent))
    report = cell.quality_report or {}
    reviewed = bool(
        report.get("fingerprint") == cell.fingerprint
        and report.get("context_fingerprint")
        == (context or context_fingerprint(dataset.capability))
        and report.get("intent") == public_intent(dataset.intent)
        and report.get("checks")
    )
    blockers = quality_blockers(cell) if reviewed else ["No current workshop quality review."]
    blockers.extend(
        (cell.intent_report.get(public_intent(dataset.intent)) or {}).get("warnings", [])
    )
    contract = cell.capability_report or {}
    if contract and not contract.get("ok"):
        blockers.append(contract.get("reason") or "Rows do not match the capability.")
    return {
        "format_valid": valid,
        "format_reason": reason,
        "quality_reviewed": reviewed,
        "quality_passed": reviewed and not blockers,
        "quality_reason": "; ".join(blockers),
        "training_configuration": "not_checked",
    }


def quality_blockers(cell) -> list[str]:
    checks = (cell.quality_report or {}).get("checks") or []
    indexed = {check["name"]: check for check in checks}
    blockers = []
    if (cell.quality_report or {}).get("audit", {}).get("method") not in {
        "row_results",
        "semantic_decisions",
    }:
        blockers.append("Quality claims have no executed row-level audit.")
    for name, label in REQUIRED_CHECKS.items():
        check = indexed.get(name)
        if check is None:
            blockers.append(f"{label}: not reviewed")
        elif check.get("result") != "pass":
            blockers.append(f"{label}: {check.get('result', 'unknown')}")
        elif check.get("rows_checked") != cell.rows:
            blockers.append(f"{label}: not reviewed across all {cell.rows} rows")
    blockers.extend(
        f"{check['name']}: {check['result']}"
        for check in checks
        if check["name"] not in REQUIRED_CHECKS and check["result"] == "fail"
    )
    return blockers


def warnings(dataset, cell, *, capability=None) -> list[str]:
    status = readiness(dataset, cell)
    findings = [status["quality_reason"]] if status["quality_reason"] else []
    if capability is not None and dataset.capability_id != capability.id:
        findings.append(f"This dataset has not been reviewed for {capability.name}.")
    return findings


def save_proposal(dataset, cell, previous, output: Path, *, kind: str, note: str) -> dict:
    before = paths.cell_path(dataset.id, previous.id)
    path = paths.cell_path(dataset.id, cell.id)
    finalize(
        before, output, path, group_by=dataset.source_spec.get("split", {}).get("group_by", [])
    )
    report = {
        "kind": kind,
        "reason": note,
        "input_fingerprint": previous.fingerprint,
        "output_fingerprint": store.file_sha256(path),
        "context_fingerprint": context_fingerprint(dataset.capability),
        "intent": dataset.intent,
        "status": "pending",
        **impact(before, path),
    }
    Cell.objects.filter(pk=cell.pk).update(review=report, quality_report={})
    cell.review = report
    return report


def record_quality(dataset, cell, checks: list[dict], *, script: str) -> dict:
    rows.verify(cell)
    if not script.strip():
        raise ValueError("Provide an audit script that computes row-level results.")
    context = context_fingerprint(dataset.capability)
    result = runner.run(
        script,
        paths.cell_path(dataset.id, cell.id),
        library_cache=paths.library_cache(dataset.project_id),
    )
    if result.path is None:
        raise ValueError(result.error or "The audit produced no row results.")
    return record_quality_results(
        dataset,
        cell,
        checks,
        result.path,
        audit={"method": "row_results", "script": script},
        reviewer="workshop_agent",
        context=context,
    )


def record_quality_results(
    dataset,
    cell,
    checks: list[dict],
    measured: Path,
    *,
    audit: dict,
    reviewer: str,
    context: str,
    semantic_audit: dict | None = None,
    expected_semantic_audit: dict | None = None,
) -> dict:
    rows.verify(cell)
    if not checks or len(checks) > 30:
        raise ValueError("Provide between 1 and 30 measured quality checks.")
    names = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("Each check needs a name and evidence.")
        if not all(
            isinstance(check.get(key), str) and check[key].strip() for key in ("name", "evidence")
        ):
            raise ValueError("Each check needs a name and measured evidence.")
        name = check["name"]
        if name in names:
            raise ValueError("Each quality check must have a unique name.")
        if name == store.SOURCE_ROW or len(name) > 200:
            raise ValueError("Check names must be at most 200 characters and not source_row.")
        names.add(name)
    results = store.Frame(measured)
    keys = list(results.values(store.SOURCE_ROW)) if store.SOURCE_ROW in results else []
    originals = _source_rows(paths.cell_path(dataset.id, cell.id)) or []
    if (
        set(results.columns) != {store.SOURCE_ROW, *names}
        or len(keys) != len(originals)
        or not _unique(keys)
        or set(keys) != set(originals)
    ):
        raise ValueError(
            "Audit results must contain source_row and the named check columns, with every original row exactly once. Use null for unmeasured rows."
        )
    outcomes = []
    for check in checks:
        values = list(results.values(check["name"]))
        if not all(type(value) is bool or value is None for value in values):
            raise ValueError(
                f"{check['name']} results must be booleans or null, not scores or strings."
            )
        failed = [key for key, value in zip(keys, values, strict=True) if value is False]
        unknown = [key for key, value in zip(keys, values, strict=True) if value is None]
        outcomes.append(
            {
                "name": check["name"],
                "evidence": check["evidence"][:2000],
                "result": "fail" if failed else "unknown" if unknown or not keys else "pass",
                "rows_checked": len(keys) - len(unknown),
                "rows_failed": len(failed),
                "rows_unknown": len(unknown),
                "failed_source_rows": failed[:20],
                "unknown_source_rows": unknown[:20],
            }
        )
    rows.verify(cell)
    report = {
        "fingerprint": cell.fingerprint,
        "context_fingerprint": context,
        "intent": public_intent(dataset.intent),
        "reviewer": reviewer,
        "at": timezone.now().isoformat(),
        "checks": outcomes,
        "audit": audit,
        **({"semantic_audit": semantic_audit} if semantic_audit is not None else {}),
    }
    with transaction.atomic():
        current_dataset = (
            Dataset.objects.select_for_update(of=("self",))
            .select_related("capability")
            .get(pk=dataset.pk)
        )
        current = Cell.objects.select_for_update().get(pk=cell.pk)
        if (
            current.fingerprint != cell.fingerprint
            or current_dataset.intent != dataset.intent
            or current_dataset.capability_id != dataset.capability_id
            or context_fingerprint(current_dataset.capability) != context
        ):
            raise ValueError("The version changed during the audit. Run the audit again.")
        previous = current.quality_report or {}
        if semantic_audit is not None and previous.get("semantic_audit") != expected_semantic_audit:
            raise ValueError(
                "The semantic audit changed during this batch. Resume the audit again."
            )
        if (
            previous.get("fingerprint") == cell.fingerprint
            and previous.get("context_fingerprint") == context
            and previous.get("intent") == report["intent"]
        ):
            report["checks"] = [
                c for c in previous.get("checks", []) if c["name"] not in names
            ] + outcomes
            report["audits"] = {**previous.get("audits", {}), **dict.fromkeys(names, audit)}
            if semantic_audit is None and previous.get("semantic_audit"):
                report["semantic_audit"] = previous["semantic_audit"]
        else:
            report["audits"] = dict.fromkeys(names, audit)
        Cell.objects.filter(pk=cell.pk).update(quality_report=report)
    cell.quality_report = report
    return report
