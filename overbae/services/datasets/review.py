from __future__ import annotations

import json
from collections import Counter

import pandas as pd
from django.db import transaction
from django.utils import timezone

from overbae.models import Cell
from overbae.services.datasets import paths, rows, store
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
    return {key: value for key, value in report.items() if not key.endswith("_examples")}


def same_frame(before: pd.DataFrame, after: pd.DataFrame) -> bool:
    try:
        pd.testing.assert_frame_equal(before, after, check_dtype=False, check_exact=True)
    except AssertionError:
        return False
    return True


def preserve_provenance(before: pd.DataFrame, after: pd.DataFrame, *, group_by=()) -> pd.DataFrame:
    if store.SOURCE_ROW not in before or store.SOURCE_ROW not in after:
        return after
    before = before.copy()
    before[PROVENANCE_COLUMN] = [preserve_lineage(row) for row in before.to_dict(orient="records")]
    indexed = before.drop_duplicates(store.SOURCE_ROW).set_index(store.SOURCE_ROW)
    for column in {
        PROVENANCE_COLUMN,
        "trace_id",
        "source_trace_id",
        "conversation_id",
        "human_reviewed",
        *group_by,
    }:
        if column not in indexed:
            continue
        inherited = after[store.SOURCE_ROW].map(indexed[column])
        after[column] = inherited.combine_first(after[column]) if column in after else inherited
    return after


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


def distribution(frame: pd.DataFrame) -> dict:
    return {
        column: dict(Counter(str(value) for value in frame[column].tolist()).most_common(50))
        for column in _COVERAGE_COLUMNS
        if column in frame.columns
    }


def impact(before: pd.DataFrame, after: pd.DataFrame) -> dict:
    tracked = (
        store.SOURCE_ROW in before
        and store.SOURCE_ROW in after
        and before[store.SOURCE_ROW].is_unique
        and after[store.SOURCE_ROW].is_unique
    )
    removed = before.iloc[:0]
    inputs = before.iloc[:0]
    evidence_removed = []
    prompt_changes = []
    if tracked:
        removed = before[~before[store.SOURCE_ROW].isin(after[store.SOURCE_ROW])]
        preview_ids = after.head(5)[store.SOURCE_ROW]
        preview_ids = preview_ids[preview_ids.isin(before[store.SOURCE_ROW])]
        inputs = before.set_index(store.SOURCE_ROW, drop=False).loc[preview_ids]
        originals = before.set_index(store.SOURCE_ROW).to_dict(orient="index")
        for row in after.to_dict(orient="records"):
            original = originals.get(row[store.SOURCE_ROW], {})
            original_prompt = instructions(original)
            if original_prompt and original_prompt != instructions(row):
                prompt_changes.append(row[store.SOURCE_ROW])
            context = messages(original.get("messages")) or original.get("input")
            if context and not identifier_only(context) and identifier_only(row.get("input")):
                evidence_removed.append(row[store.SOURCE_ROW])
    return {
        "rows_before": len(before),
        "rows_after": len(after),
        "rows_removed": len(removed) if tracked else None,
        "rows_added": int((~after[store.SOURCE_ROW].isin(before[store.SOURCE_ROW])).sum())
        if tracked
        else None,
        "identity_preserved": tracked,
        "input_evidence_removed": len(evidence_removed),
        "input_evidence_removed_examples": evidence_removed[:20],
        "instruction_changes": len(prompt_changes),
        "instruction_change_examples": prompt_changes[:20],
        "coverage_before": distribution(before),
        "coverage_after": distribution(after),
        "removed_examples": preview_examples(removed),
        "input_examples": preview_examples(inputs),
        "output_examples": preview_examples(after),
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
    if (cell.quality_report or {}).get("audit", {}).get("method") != "row_results":
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


def save_proposal(dataset, cell, previous, frame: pd.DataFrame, *, kind: str, note: str) -> dict:
    before = store.read_frame(paths.cell_path(dataset.id, previous.id))
    frame = preserve_provenance(
        before, frame, group_by=dataset.source_spec.get("split", {}).get("group_by", [])
    )
    path = paths.cell_path(dataset.id, cell.id)
    store.write_frame(path, frame)
    report = {
        "kind": kind,
        "reason": note,
        "input_fingerprint": previous.fingerprint,
        "output_fingerprint": store.file_sha256(path),
        "context_fingerprint": context_fingerprint(dataset.capability),
        "intent": dataset.intent,
        "status": "pending",
        **impact(before, frame),
    }
    Cell.objects.filter(pk=cell.pk).update(review=report, quality_report={})
    cell.review = report
    return report


def record_quality(dataset, cell, checks: list[dict], *, script: str) -> dict:
    rows.verify(cell)
    if not script.strip():
        raise ValueError("Provide an audit script that computes row-level results.")
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
    original = store.read_frame(paths.cell_path(dataset.id, cell.id))
    result = runner.run(
        script,
        paths.cell_path(dataset.id, cell.id),
        library_cache=paths.library_cache(dataset.project_id),
    )
    if result.frame is None:
        raise ValueError(result.error or "The audit produced no row results.")
    measured = result.frame
    if (
        set(measured.columns) != {store.SOURCE_ROW, *names}
        or len(measured) != len(original)
        or not measured[store.SOURCE_ROW].is_unique
        or set(measured[store.SOURCE_ROW]) != set(original[store.SOURCE_ROW])
    ):
        raise ValueError(
            "Audit results must contain source_row and the named check columns, with every original row exactly once. Use null for unmeasured rows."
        )
    outcomes = []
    for check in checks:
        values = measured[check["name"]]
        if not all(
            type(value) is bool
            or value is None
            or value is pd.NA
            or isinstance(value, float)
            and pd.isna(value)
            for value in values.tolist()
        ):
            raise ValueError(
                f"{check['name']} results must be booleans or null, not scores or strings."
            )
        failed = measured.loc[values.eq(False), store.SOURCE_ROW].tolist()
        unknown = measured.loc[values.isna(), store.SOURCE_ROW].tolist()
        outcomes.append(
            {
                "name": check["name"],
                "evidence": check["evidence"][:2000],
                "result": "fail" if failed else "unknown" if unknown or measured.empty else "pass",
                "rows_checked": len(measured) - len(unknown),
                "rows_failed": len(failed),
                "rows_unknown": len(unknown),
                "failed_source_rows": failed[:20],
                "unknown_source_rows": unknown[:20],
            }
        )
    rows.verify(cell)
    report = {
        "fingerprint": cell.fingerprint,
        "context_fingerprint": context_fingerprint(dataset.capability),
        "intent": public_intent(dataset.intent),
        "reviewer": "workshop_agent",
        "at": timezone.now().isoformat(),
        "checks": outcomes,
        "audit": {"method": "row_results", "script": script},
    }
    with transaction.atomic():
        current = Cell.objects.select_for_update().get(pk=cell.pk)
        if current.fingerprint != cell.fingerprint:
            raise ValueError("The version changed during the audit. Run the audit again.")
        Cell.objects.filter(pk=cell.pk).update(quality_report=report)
    cell.quality_report = report
    return report
