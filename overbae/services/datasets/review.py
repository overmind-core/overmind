from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from itertools import zip_longest
from pathlib import Path

import pandas as pd
from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import paths, preparation, rows, store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.examples import (
    decode,
    identifier_only,
    instructions,
    messages,
    native_decision,
)
from overbae.services.datasets.notebook import runner
from overbae.services.datasets.partition import preserve_lineage

PROVENANCE_COLUMN = "_overmind_provenance"
_COVERAGE_COLUMNS = (
    "source",
    "kind",
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


def decision_changed(original, row):
    decision = native_decision(original)
    if decision is None:
        return False
    if decision != native_decision(row):
        return True
    payload = decode(row.get("input"))
    if isinstance(payload, dict) and "decision" in payload:
        return payload["decision"] != {
            key: decision.get(key) for key in ("state", "question", "kind", "options")
        } or decode(row.get("expected_output")) != {
            "probabilities": decision.get("target_probabilities")
        }
    return False


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
    before[PROVENANCE_COLUMN] = [
        preserve_lineage(row, group_by=group_by) for row in before.to_dict(orient="records")
    ]
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
    decision_changes = 0
    if tracked:
        removed = before[~before[store.SOURCE_ROW].isin(after[store.SOURCE_ROW])]
        preview_ids = after.head(5)[store.SOURCE_ROW]
        preview_ids = preview_ids[preview_ids.isin(before[store.SOURCE_ROW])]
        inputs = before.set_index(store.SOURCE_ROW, drop=False).loc[preview_ids]
        originals = before.set_index(store.SOURCE_ROW).to_dict(orient="index")
        for row in after.to_dict(orient="records"):
            original = originals.get(row[store.SOURCE_ROW], {})
            decision_changes += decision_changed(original, row)
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
        "decision_changes": decision_changes,
        "decision_rows_removed": sum(
            native_decision(row) is not None for row in removed.to_dict(orient="records")
        ),
        "coverage_before": distribution(before),
        "coverage_after": distribution(after),
        "removed_examples": preview_examples(removed),
        "input_examples": preview_examples(inputs),
        "output_examples": preview_examples(after),
    }


def same_files(before: Path, after: Path) -> bool:
    if [c["name"] for c in store.read_manifest(before)] != [
        c["name"] for c in store.read_manifest(after)
    ]:
        return False
    return all(a == b for a, b in zip_longest(store.iter_rows(before), store.iter_rows(after)))


def distribution_file(path: Path) -> dict:
    columns = {c["name"] for c in store.read_manifest(path)}
    result = {}
    with store.connect(t=path) as con:
        for column in _COVERAGE_COLUMNS:
            if column in columns:
                result[column] = dict(
                    con.execute(
                        f"SELECT coalesce(CAST(\"{column}\" AS VARCHAR), 'None'), count(*) "
                        f"FROM t GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 50"
                    ).fetchall()
                )
    return result


@contextmanager
def indexed_rows(path):
    with tempfile.TemporaryDirectory(prefix="review_") as directory:
        con = sqlite3.connect(Path(directory) / "rows.sqlite")
        con.execute("PRAGMA cache_size=-16384")
        con.execute(
            "CREATE TABLE original (identity TEXT PRIMARY KEY, body TEXT, seen INTEGER DEFAULT 0)"
        )
        con.execute("CREATE TABLE selected (identity TEXT PRIMARY KEY)")
        tracked = True
        try:
            for row in store.iter_rows(path):
                identity = row.get(store.SOURCE_ROW)
                if identity is None:
                    tracked = False
                count = con.execute(
                    "INSERT OR IGNORE INTO original(identity, body) VALUES (?, ?)",
                    (json.dumps(identity), store.json_dumps(row)),
                ).rowcount
                tracked = tracked and bool(count)
            con.commit()
            yield con, tracked
        finally:
            con.close()


def impact_files(before: Path, after: Path) -> dict:
    columns = {c["name"] for c in store.read_manifest(after)}
    evidence = prompts = added = decision_changes = decision_removed = 0
    evidence_examples, prompt_examples, inputs = [], [], []
    output = store.head(after, 5)
    with indexed_rows(before) as (con, tracked):
        tracked = tracked and store.SOURCE_ROW in columns
        for i, row in enumerate(store.iter_rows(after)):
            identity = json.dumps(row.get(store.SOURCE_ROW))
            unique = con.execute("INSERT OR IGNORE INTO selected VALUES (?)", (identity,)).rowcount
            tracked = tracked and bool(unique) and row.get(store.SOURCE_ROW) is not None
            found = con.execute(
                "SELECT body FROM original WHERE identity=?", (identity,)
            ).fetchone()
            original = json.loads(found[0]) if found else {}
            decision_changes += decision_changed(original, row)
            if found:
                con.execute("UPDATE original SET seen=1 WHERE identity=?", (identity,))
                if i < 5:
                    inputs.append(original)
            else:
                added += 1
            prompt = instructions(original)
            if prompt and prompt != instructions(row):
                prompts += 1
                if len(prompt_examples) < 20:
                    prompt_examples.append(row.get(store.SOURCE_ROW))
            context = messages(original.get("messages")) or original.get("input")
            if context and not identifier_only(context) and identifier_only(row.get("input")):
                evidence += 1
                if len(evidence_examples) < 20:
                    evidence_examples.append(row.get(store.SOURCE_ROW))
        removed = con.execute("SELECT count(*) FROM original WHERE seen=0").fetchone()[0]
        for (body,) in con.execute("SELECT body FROM original WHERE seen=0"):
            decision_removed += native_decision(json.loads(body)) is not None
        removed_examples = [
            json.loads(r[0]) for r in con.execute("SELECT body FROM original WHERE seen=0 LIMIT 5")
        ]
    return {
        "rows_before": store.row_count(before),
        "rows_after": store.row_count(after),
        "rows_removed": removed if tracked else None,
        "rows_added": added if tracked else None,
        "identity_preserved": tracked,
        "input_evidence_removed": evidence if tracked else 0,
        "input_evidence_removed_examples": evidence_examples if tracked else [],
        "instruction_changes": prompts if tracked else 0,
        "decision_changes": decision_changes if tracked else 0,
        "decision_rows_removed": decision_removed if tracked else 0,
        "instruction_change_examples": prompt_examples if tracked else [],
        "coverage_before": distribution_file(before),
        "coverage_after": distribution_file(after),
        "removed_examples": preview_examples(pd.DataFrame(removed_examples)) if tracked else [],
        "input_examples": preview_examples(pd.DataFrame(inputs)) if tracked else [],
        "output_examples": preview_examples(pd.DataFrame(output)),
    }


def preserve_file_provenance(before: Path, after: Path, destination: Path, *, group_by=()):
    columns = {c["name"] for c in store.read_manifest(before)}
    inherited = {
        PROVENANCE_COLUMN,
        "trace_id",
        "source_trace_id",
        "conversation_id",
        "human_reviewed",
        *group_by,
    } & columns
    if store.SOURCE_ROW not in columns or store.SOURCE_ROW not in {
        c["name"] for c in store.read_manifest(after)
    }:
        store.write_rows(destination, store.iter_rows(after), store.read_manifest(after))
        return
    with indexed_rows(before) as (con, _tracked):

        def records():
            for row in store.iter_rows(after):
                found = con.execute(
                    "SELECT body FROM original WHERE identity=?",
                    (json.dumps(row[store.SOURCE_ROW]),),
                ).fetchone()
                if found:
                    original = json.loads(found[0])
                    original[PROVENANCE_COLUMN] = preserve_lineage(original, group_by=group_by)
                    for column in inherited | {PROVENANCE_COLUMN}:
                        if original.get(column) is not None:
                            row[column] = original[column]
                yield row

        store.write_rows(destination, records())


def requires_approval(changes: dict, *, allow_exclusions: bool = False) -> bool:
    return bool(
        (changes["rows_removed"] and not allow_exclusions)
        or changes["rows_added"]
        or not changes["identity_preserved"]
        or changes["input_evidence_removed"]
        or changes["instruction_changes"]
        or changes.get("decision_changes", 0)
        or changes.get("decision_rows_removed", 0)
    )


def readiness(dataset, cell, *, context: str | None = None) -> dict:
    valid, reason = cell.fits(public_intent(dataset.intent))
    report = cell.quality_report or {}
    plan = preparation.for_cell(dataset, cell)
    reviewed = bool(
        report.get("fingerprint") == cell.fingerprint
        and report.get("context_fingerprint")
        == (context or context_fingerprint(dataset.capability))
        and report.get("intent") == public_intent(dataset.intent)
        and report.get("checks")
        and report.get("plan_id") == plan.get("id")
    )
    blockers = (
        quality_blockers(cell, plan=plan) if reviewed else ["No current workshop quality review."]
    )
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
        "assessment": assessment(plan, report if reviewed else {}, valid),
    }


def assessment(plan, report, valid):
    measured = {check["name"]: check for check in report.get("checks", [])}
    result = {}
    for category in ("technical", "preservation", "coverage", "semantic"):
        checks = [
            check
            for check in plan.get("specification", {}).get("checks", [])
            if check["category"] == category
        ]
        states = [
            measured.get(check["name"], {}).get("result", "unknown")
            if check["method"] != "unmeasured"
            else "unknown"
            for check in checks
        ]
        if category == "technical":
            states.append("pass" if valid else "fail")
        result[category] = (
            "fail"
            if "fail" in states
            else "pass"
            if states and all(s == "pass" for s in states)
            else "partial"
            if "pass" in states
            else "unknown"
        )
    return result


def quality_blockers(cell, *, plan=None) -> list[str]:
    checks = (cell.quality_report or {}).get("checks") or []
    indexed = {check["name"]: check for check in checks}
    blockers = []
    if (cell.quality_report or {}).get("audit", {}).get("method") not in {
        "row_results",
        "semantic_decisions",
    }:
        blockers.append("Quality claims have no executed row-level audit.")
    required = {
        check["name"]: check["question"]
        for check in (plan or {}).get("specification", {}).get("checks", [])
    }
    if not required:
        blockers.append("No preparation plan defines the scope of this review.")
    for name in required:
        label = name.replace("_", " ").capitalize()
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
        if check["name"] not in required and check["result"] != "pass"
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
    preserve_file_provenance(
        before, output, path, group_by=preparation.group_columns(dataset, cell)
    )
    report = {
        "kind": kind,
        "reason": note,
        "input_fingerprint": previous.fingerprint,
        "output_fingerprint": store.file_sha256(path),
        "context_fingerprint": context_fingerprint(dataset.capability),
        "intent": dataset.intent,
        "status": "pending",
        **impact_files(before, path),
    }
    Cell.objects.filter(pk=cell.pk).update(review=report, quality_report={})
    cell.review = report
    return report


def file_quality_outcomes(original: Path, measured: Path, checks: list[dict]) -> list[dict]:
    names = {c["name"] for c in checks}
    columns = {c["name"]: c["type"] for c in store.read_manifest(measured)}
    count = store.row_count(original)
    error = "Audit results must contain source_row and the named check columns, with every original row exactly once. Use null for unmeasured rows."
    if set(columns) != {store.SOURCE_ROW, *names} or store.row_count(measured) != count:
        raise ValueError(error)
    outcomes = []
    with store.connect(original=original, measured=measured) as con:
        covered = con.execute("SELECT count(DISTINCT source_row) FROM measured").fetchone()[0]
        missing = con.execute(
            "SELECT count(*) FROM original ANTI JOIN measured USING (source_row)"
        ).fetchone()[0]
        if covered != count or missing:
            raise ValueError(error)
        for check in checks:
            name = check["name"]
            quoted = '"' + name.replace('"', '""') + '"'
            unknown = con.execute(
                f"SELECT count(*) FROM measured WHERE {quoted} IS NULL"
            ).fetchone()[0]
            if columns[name] != "boolean" and unknown != count:
                raise ValueError(f"{name} results must be booleans or null, not scores or strings.")
            failed = con.execute(
                f"SELECT count(*) FROM measured WHERE {quoted} = false"
            ).fetchone()[0]
            failed_ids = [
                r[0]
                for r in con.execute(
                    f"SELECT source_row FROM measured WHERE {quoted} = false LIMIT 20"
                ).fetchall()
            ]
            unknown_ids = [
                r[0]
                for r in con.execute(
                    f"SELECT source_row FROM measured WHERE {quoted} IS NULL LIMIT 20"
                ).fetchall()
            ]
            outcomes.append(
                {
                    "name": name,
                    "evidence": check["evidence"][:2000],
                    "result": "fail" if failed else "unknown" if unknown or not count else "pass",
                    "rows_checked": count - unknown,
                    "rows_failed": failed,
                    "rows_unknown": unknown,
                    "failed_source_rows": failed_ids,
                    "unknown_source_rows": unknown_ids,
                }
            )
    return outcomes


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
    measured: pd.DataFrame | Path,
    *,
    audit: dict,
    reviewer: str,
    context: str,
    semantic_audit: dict | None = None,
    expected_semantic_audit: dict | None = None,
    original: pd.DataFrame | None = None,
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
    if isinstance(measured, Path):
        outcomes = file_quality_outcomes(paths.cell_path(dataset.id, cell.id), measured, checks)
    else:
        if original is None:
            original = store.read_frame(paths.cell_path(dataset.id, cell.id))
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
                    "result": "fail"
                    if failed
                    else "unknown"
                    if unknown or measured.empty
                    else "pass",
                    "rows_checked": len(measured) - len(unknown),
                    "rows_failed": len(failed),
                    "rows_unknown": len(unknown),
                    "failed_source_rows": failed[:20],
                    "unknown_source_rows": unknown[:20],
                }
            )
    rows.verify(cell)
    plan = preparation.for_cell(dataset, cell)
    declared = {check["name"]: check for check in plan.get("specification", {}).get("checks", [])}
    for outcome in outcomes:
        definition = declared.get(outcome["name"])
        if definition:
            if definition["method"] == "unmeasured" and outcome["rows_checked"]:
                raise ValueError(
                    "An unmeasured plan check must remain null. Revise the plan before measuring it."
                )
            if (
                definition["method"] == "semantic"
                and outcome["rows_checked"]
                and audit["method"] != "semantic_decisions"
            ):
                raise ValueError(
                    "Semantic plan checks require check_semantic_quality; use null for unmeasured rows."
                )
            outcome.update(category=definition["category"], method=definition["method"])
    report = {
        "plan_id": plan.get("id"),
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
            or preparation.for_cell(current_dataset, current).get("id") != plan.get("id")
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
            and previous.get("plan_id") == report["plan_id"]
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
