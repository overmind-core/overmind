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


def _same_keys(a: Path, b: Path) -> bool:
    con = store.connect(a=a, b=b)
    try:
        return not con.execute(
            "SELECT 1 FROM (SELECT source_row FROM a EXCEPT SELECT source_row FROM b) "
            "UNION ALL SELECT 1 FROM (SELECT source_row FROM b EXCEPT SELECT source_row FROM a) "
            "LIMIT 1"
        ).fetchall()
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
    original = paths.cell_path(dataset.id, cell.id)
    if (
        set(results.columns) != {store.SOURCE_ROW, *names}
        or len(results) != store.row_count(original)
        or not _unique(measured)
        or not _same_keys(measured, original)
    ):
        raise ValueError(
            "Audit results must contain source_row and the named check columns, with every original row exactly once. Use null for unmeasured rows."
        )
    outcomes = []
    for check in checks:
        failed, unknown = [], []
        rows_failed = rows_unknown = 0
        for key, value in zip(
            results.values(store.SOURCE_ROW), results.values(check["name"]), strict=True
        ):
            if value is None:
                rows_unknown += 1
                if len(unknown) < _EXAMPLES:
                    unknown.append(key)
            elif type(value) is not bool:
                raise ValueError(
                    f"{check['name']} results must be booleans or null, not scores or strings."
                )
            elif not value:
                rows_failed += 1
                if len(failed) < _EXAMPLES:
                    failed.append(key)
        outcomes.append(
            {
                "name": check["name"],
                "evidence": check["evidence"][:2000],
                "result": "fail"
                if rows_failed
                else "unknown"
                if rows_unknown or not len(results)
                else "pass",
                "rows_checked": len(results) - rows_unknown,
                "rows_failed": rows_failed,
                "rows_unknown": rows_unknown,
                "failed_source_rows": failed,
                "unknown_source_rows": unknown,
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
