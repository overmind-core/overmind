from __future__ import annotations

import json
import sqlite3
import tempfile
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from overbae.services.datasets import store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.examples import (
    decode,
    identifier_only,
    instructions,
    messages,
    native_decision,
)
from overbae.services.datasets.partition import contamination_keys, preserve_lineage

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
        } or decode(row.get("expected_output")) != (
            {"mean": decision["target_mean"], "values": decision.get("option_values")}
            if "target_mean" in decision
            else {"probabilities": decision.get("target_probabilities")}
        )
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
        "_overmind_document_id",
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
def indexed_rows(path, *, parent_paths=None):
    with tempfile.TemporaryDirectory(prefix="review_") as directory:
        con = sqlite3.connect(Path(directory) / "rows.sqlite")
        con.execute("PRAGMA cache_size=-16384")
        con.execute(
            "CREATE TABLE original (identity TEXT PRIMARY KEY, body TEXT, seen INTEGER DEFAULT 0, cell TEXT, fingerprint TEXT)"
        )
        con.execute("CREATE TABLE selected (identity TEXT PRIMARY KEY)")
        tracked = True
        try:
            for parent in parent_paths or [path]:
                fingerprint = store.file_sha256(parent)
                for row in store.iter_rows(parent):
                    identity = row.get(store.SOURCE_ROW)
                    if identity is None:
                        tracked = False
                    count = con.execute(
                        "INSERT OR IGNORE INTO original(identity, body, cell, fingerprint) VALUES (?, ?, ?, ?)",
                        (json.dumps(identity), store.json_dumps(row), parent.stem, fingerprint),
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


def preserve_file_provenance(
    before: Path, after: Path, destination: Path, *, group_by=(), parent_paths=None
):
    columns = {c["name"] for c in store.read_manifest(before)}
    inherited = {
        "trace_id",
        "source_trace_id",
        "conversation_id",
        "human_reviewed",
        *group_by,
    } & columns
    if store.SOURCE_ROW not in columns:
        raise ValueError("The source has no record identities.")
    with indexed_rows(before, parent_paths=parent_paths) as (con, tracked):
        if not tracked:
            raise ValueError(
                "The source has ambiguous row identities. Select an earlier intact version."
            )
        largest = (
            con.execute("SELECT max(CAST(identity AS INTEGER)) FROM original").fetchone()[0] or 0
        )
        con.execute("CREATE TABLE assigned (identity INTEGER PRIMARY KEY)")

        def records():
            next_id = largest + 1
            for row in store.iter_rows(after):
                explicit = row.pop("_overmind_parent_rows", None)
                identity = row.get(store.SOURCE_ROW)
                parent_ids = explicit if explicit is not None else [identity]
                if not isinstance(parent_ids, list) or not parent_ids or len(parent_ids) > 10000:
                    raise ValueError(
                        "A merged row needs _overmind_parent_rows containing its contributing source_row identities."
                    )
                originals = []
                parents = []
                for parent in dict.fromkeys(parent_ids):
                    if isinstance(parent, bool) or not isinstance(parent, int) or parent < 0:
                        raise ValueError(
                            "Preserve source_row, or declare _overmind_parent_rows for a split or merge."
                        )
                    found = con.execute(
                        "SELECT body, cell, fingerprint FROM original WHERE identity=?",
                        (json.dumps(parent),),
                    ).fetchone()
                    if found is None:
                        raise ValueError("A parent row is not present in the bound source version.")
                    originals.append(json.loads(found[0]))
                    parents.append({"cell": found[1], "fingerprint": found[2], "row": parent})
                if (
                    explicit is not None
                    or con.execute(
                        "SELECT 1 FROM assigned WHERE identity=?", (identity,)
                    ).fetchone()
                ):
                    identity, next_id = next_id, next_id + 1
                con.execute("INSERT INTO assigned VALUES (?)", (identity,))
                row[store.SOURCE_ROW] = identity
                if (
                    (parent_paths is None or len(parent_paths) == 1)
                    and explicit is None
                    and len(originals) == 1
                    and row == originals[0]
                ):
                    yield row
                    continue
                keys = set().union(
                    *(contamination_keys(original, group_by) for original in originals)
                )
                provenance = (
                    preserve_lineage(originals[0], group_by=group_by)
                    if len(originals) == 1
                    else {"kind": "transform"}
                )
                row[PROVENANCE_COLUMN] = {
                    **provenance,
                    "parents": parents,
                    "source_content_keys": sorted(v for k, v in keys if k == "content"),
                    "source_group_keys": sorted([k, v] for k, v in keys if k != "content"),
                }
                for column in inherited:
                    values = [original.get(column) for original in originals]
                    if values[0] is not None and all(value == values[0] for value in values):
                        row[column] = values[0]
                yield row

        store.write_rows(destination, records())


def readiness(dataset, cell, *, context: str | None = None) -> dict:
    valid, reason = cell.fits(public_intent(dataset.intent))
    report = cell.quality_report or {}
    plan = cell.preparation_plan or {}
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


def group_columns(dataset, cell):
    return sorted(
        set(dataset.source_spec.get("split", {}).get("group_by", []))
        | {
            column
            for family in cell.preparation_plan.get("specification", {}).get("families", [])
            for column in family.get("group_columns", [])
        }
    )
