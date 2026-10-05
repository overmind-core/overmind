import hashlib
import json
import math
import sqlite3
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.models import DataPartitionMember, DataPartitionPlan, Dataset
from overbae.services.datasets import land, paths, rows, store, use
from overbae.services.datasets.examples import field_value, native_decision, prepare_native_record
from overbae.services.datasets.partition import contamination_keys, preserve_lineage

ROLES = ("train", "development", "calibration", "final")


def validate_recipe(recipe):
    if not isinstance(recipe, dict) or set(recipe) - {
        "seed",
        "fractions",
        "group_by",
        "stratify_by",
        "holdouts",
    }:
        raise InputValidationError("Unknown partition rule")
    fractions = recipe.get("fractions", {})
    if (
        not isinstance(fractions, dict)
        or not 2 <= len(fractions) <= 4
        or set(fractions) - set(ROLES)
    ):
        raise InputValidationError(
            "Choose two to four training, development, calibration or final roles"
        )
    if any(
        type(v) not in {int, float} or not math.isfinite(v) or not 0 < v < 1
        for v in fractions.values()
    ) or not math.isclose(sum(fractions.values()), 1, abs_tol=1e-9):
        raise InputValidationError("Positive partition fractions must sum to one")
    if type(recipe.get("seed")) is not int or not 0 <= recipe["seed"] <= 2**32 - 1:
        raise InputValidationError("Supply a seed between zero and 4294967295")
    groups = recipe.get("group_by", [])
    if (
        not isinstance(groups, list)
        or len(groups) > 20
        or any(not isinstance(x, str) or not x for x in groups)
    ):
        raise InputValidationError("Group fields must be a list of at most twenty names")
    strata = recipe.get("stratify_by")
    if strata is not None and (not isinstance(strata, str) or not strata):
        raise InputValidationError("Stratification requires a field name")
    holdouts = recipe.get("holdouts", [])
    if not isinstance(holdouts, list) or len(holdouts) > 20:
        raise InputValidationError("Supply at most twenty holdout rules")
    for rule in holdouts:
        if (
            not isinstance(rule, dict)
            or set(rule) != {"field", "values", "role"}
            or rule["role"] not in fractions
            or not isinstance(rule["field"], str)
            or not rule["field"]
            or not isinstance(rule["values"], list)
            or not rule["values"]
        ):
            raise InputValidationError(
                "A holdout requires a field, nonempty values and a selected role"
            )
    json.dumps(recipe, allow_nan=False)
    return {
        "seed": recipe["seed"],
        "fractions": {r: fractions[r] for r in ROLES if r in fractions},
        "group_by": sorted(set(groups)),
        "stratify_by": strata,
        "holdouts": holdouts,
    }


@transaction.atomic
def request_plan(project, *, name, request_key, source_cell, recipe):
    if source_cell.dataset.project_id != project.pk:
        raise InputValidationError("The source must belong to this project")
    recipe = validate_recipe(recipe)
    Dataset.objects.select_for_update().get(pk=source_cell.dataset_id)
    existing = DataPartitionPlan.objects.filter(project=project, request_key=request_key).first()
    if existing:
        if (
            existing.recipe != recipe
            or existing.source_cell_id != source_cell.pk
            or existing.name != name
        ):
            raise InputValidationError(
                "This request key already identifies a different partition plan"
            )
        return existing
    rows.verify(source_cell)
    if not source_cell.ran or not source_cell.rows:
        raise InputValidationError("Partitioning requires a nonempty readable version")
    use.freeze(source_cell)
    return DataPartitionPlan.objects.create(
        project=project,
        name=name,
        request_key=request_key,
        source_cell=source_cell,
        source_fingerprint=source_cell.fingerprint,
        recipe=recipe,
    )


def assign(cell, recipe, destination, *, progress=None):
    parents = []

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    with sqlite3.connect(destination / "groups.sqlite") as db:
        db.execute(
            "CREATE TABLE identities (kind TEXT, value TEXT, owner INTEGER, PRIMARY KEY(kind,value))"
        )
        db.execute("CREATE TABLE observations (row INTEGER PRIMARY KEY, stratum TEXT, forced TEXT)")
        for index, record in enumerate(store.iter_rows(rows.frame_path(cell))):
            if progress and index % 10000 == 0:
                progress("grouping", index, cell.rows)
            parents.append(index)
            for kind, value in contamination_keys(record, recipe["group_by"]):
                found = db.execute(
                    "SELECT owner FROM identities WHERE kind=? AND value=?", (kind, value)
                ).fetchone()
                if found:
                    a, b = root(index), root(found[0])
                    parents[max(a, b)] = min(a, b)
                else:
                    db.execute("INSERT INTO identities VALUES(?,?,?)", (kind, value, index))
            forced = {
                rule["role"]
                for rule in recipe["holdouts"]
                if field_value(record, rule["field"]) in rule["values"]
            }
            if len(forced) > 1:
                raise InputValidationError("Conflicting holdout roles apply to one observation")
            label = (
                json.dumps(field_value(record, recipe["stratify_by"]), sort_keys=True)
                if recipe["stratify_by"]
                else "all"
            )
            db.execute(
                "INSERT INTO observations VALUES(?,?,?)", (index, label, next(iter(forced), None))
            )
        if progress:
            progress("assigning", len(parents), cell.rows)
        components = defaultdict(lambda: {"count": 0, "strata": Counter(), "forced": set()})
        totals = Counter()
        for index, label, forced in db.execute(
            "SELECT row,stratum,forced FROM observations ORDER BY row"
        ):
            component = components[root(index)]
            component["count"] += 1
            component["strata"][label] += 1
            totals[label] += 1
            if forced:
                component["forced"].add(forced)
        fractions = recipe["fractions"]
        if len(components) < len(fractions):
            raise InputValidationError(
                "Too few independent content/group clusters for the selected roles"
            )
        counts, group_counts = Counter(), Counter()
        strata = {role: Counter() for role in fractions}
        assignments = {}
        ordered = sorted(
            components,
            key=lambda key: (
                not components[key]["forced"],
                hashlib.sha256(f"{recipe['seed']}:{key}".encode()).hexdigest(),
            ),
        )
        for key in ordered:
            component = components[key]
            if len(component["forced"]) > 1:
                raise InputValidationError("Related observations require conflicting holdout roles")

            def change(role, component=component):
                score = 0.0
                for label, size in component["strata"].items():
                    target = totals[label] * fractions[role]
                    before = strata[role][label] - target
                    score += ((before + size) ** 2 - before**2) / max(1, target)
                return score

            role = (
                next(iter(component["forced"]))
                if component["forced"]
                else min(fractions, key=change)
            )
            assignments[key] = role
            counts[role] += component["count"]
            group_counts[role] += 1
            strata[role].update(component["strata"])
        if any(not counts[role] for role in fractions):
            raise InputValidationError(
                "Grouping and holdout rules leave an empty role; revise the fractions or rules"
            )
        with (destination / "assignments.jsonl").open("w") as output:
            for index in range(len(parents)):
                output.write(
                    json.dumps(
                        {"row": index, "group": root(index), "role": assignments[root(index)]}
                    )
                    + "\n"
                )
        return {
            "source_rows": len(parents),
            "counts": dict(counts),
            "groups": dict(group_counts),
            "strata": {r: dict(s) for r, s in strata.items()},
            "duplicates_removed": 0,
            "overlap": {
                "content_and_declared_groups": 0,
                "near_duplicates": "not_checked",
                "pretraining": "unknown",
            },
        }


def prepared_member_rows(source, role):
    for line in source:
        record = json.loads(line)
        decision = native_decision(record)
        if role in {"calibration", "final"} and decision is not None:
            # This is a lossless native wire projection; interpretation remains Workshop-owned.
            yield prepare_native_record(record, decision, "eval")
        else:
            yield record


def build(plan_id):
    with transaction.atomic():
        plan = (
            DataPartitionPlan.objects.select_for_update()
            .select_related("source_cell__dataset", "project")
            .get(pk=plan_id)
        )
        if plan.state == "completed" or (
            plan.state == "running" and plan.updated_at > timezone.now() - timedelta(seconds=1260)
        ):
            return
        plan.state, plan.error = "running", ""
        plan.save()

    def progress(stage, completed, total, **extra):
        value = {
            "stage": stage,
            "completed": completed,
            "total": total,
            **extra,
            "updated_at": timezone.now().isoformat(),
        }
        DataPartitionPlan.objects.filter(pk=plan.pk, state="running").update(
            report={"progress": value}, updated_at=timezone.now()
        )
        return value

    try:
        progress("verifying", 0, plan.source_cell.rows)
        rows.verify(plan.source_cell)
        if plan.source_cell.fingerprint != plan.source_fingerprint:
            raise InputValidationError("The frozen partition source changed")
        directory = paths.media_root() / "data-partitions" / str(plan.id)
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            work = Path(temporary)

            report = assign(plan.source_cell, plan.recipe, work, progress=progress)
            progress("writing", 0, plan.source_cell.rows)
            streams = {
                role: (work / f"{role}.jsonl").open("w") for role in plan.recipe["fractions"]
            }
            try:
                with (work / "assignments.jsonl").open() as assignments:
                    for index, (record, line) in enumerate(
                        zip(
                            store.iter_rows(rows.frame_path(plan.source_cell)),
                            assignments,
                            strict=True,
                        )
                    ):
                        if index % 10000 == 0:
                            progress("writing", index, plan.source_cell.rows)
                        assignment = json.loads(line)
                        preserved = {
                            **record,
                            "_overmind_provenance": preserve_lineage(
                                record, group_by=plan.recipe["group_by"]
                            ),
                        }
                        preserved.setdefault("_overmind_provenance", {})["partition"] = {
                            "plan": str(plan.pk),
                            "role": assignment["role"],
                            "source_cell": str(plan.source_cell_id),
                            "source_row": assignment["row"],
                        }
                        streams[assignment["role"]].write(json.dumps(preserved) + "\n")
            finally:
                for stream in streams.values():
                    stream.close()
            for role in plan.recipe["fractions"]:
                progress("preparing_member", 0, report["counts"][role], role=role)
                existing = plan.members.select_related("cell__dataset").filter(role=role).first()
                if existing:
                    rows.verify(existing.cell)
                    continue
                with transaction.atomic():
                    dataset_id = uuid.uuid5(plan.pk, role)
                    dataset, _ = Dataset.objects.get_or_create(
                        pk=dataset_id,
                        defaults={
                            "project": plan.project,
                            "name": f"{plan.name} · {role}",
                            "brief": plan.source_cell.dataset.brief,
                            "intent": "eval"
                            if role in {"calibration", "final"}
                            else "train"
                            if plan.source_cell.dataset.intent in {"train", "eval"}
                            else plan.source_cell.dataset.intent,
                            "state": Dataset.State.LANDING,
                        },
                    )
                    with (work / f"{role}.jsonl").open() as source:
                        land.commit(
                            dataset,
                            land.Landing(
                                prepared_member_rows(source, role),
                                spec={
                                    "partition_plan": str(plan.pk),
                                    "role": role,
                                    "source_cell": str(plan.source_cell_id),
                                    "fingerprint": plan.source_fingerprint,
                                },
                            ),
                            infer_capability=False,
                        )
                    member, _ = DataPartitionMember.objects.get_or_create(
                        plan=plan, role=role, defaults={"cell": dataset.active_cell}
                    )
            assignment_path = directory / "assignments.jsonl"
            (work / "assignments.jsonl").replace(assignment_path)
            report["assignments_sha256"] = store.file_sha256(assignment_path)
            report["fingerprint"] = plan.source_fingerprint
            report["progress"] = {
                "stage": "completed",
                "completed": plan.source_cell.rows,
                "total": plan.source_cell.rows,
                "updated_at": timezone.now().isoformat(),
            }
        DataPartitionPlan.objects.filter(pk=plan.pk).update(
            state="completed", report=report, error="", updated_at=timezone.now()
        )
    except Exception as exc:
        DataPartitionPlan.objects.filter(pk=plan.pk).update(
            state="failed", error=str(exc)[:1000], updated_at=timezone.now()
        )


def describe(plan):
    return {
        "id": str(plan.pk),
        "project": str(plan.project_id),
        "name": plan.name,
        "source_cell": str(plan.source_cell_id),
        "recipe": plan.recipe,
        "report": plan.report,
        "state": plan.state,
        "error": plan.error,
        "members": [
            {"role": member.role, "cell": use.describe(member.cell)}
            for member in plan.members.select_related("cell__dataset")
        ],
    }


def retry(plan):
    if not DataPartitionPlan.objects.filter(pk=plan.pk, state="failed").update(
        state="queued", error=""
    ):
        raise InputValidationError("Only failed partition construction can be retried")
