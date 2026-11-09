import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing

from overbae.core.errors import InputValidationError
from overbae.services.datasets.examples import field_value
from overbae.services.datasets.partition import GROUP_ALIASES, contamination_keys

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
            "Choose two to four role keys from: train, development, calibration, final"
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


def assign(records, total_rows, recipe, destination, *, progress=None):
    parents = []
    unresolved_groups = set(recipe["group_by"])

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    with closing(sqlite3.connect(destination / "groups.sqlite")) as db, db:
        db.execute(
            "CREATE TABLE identities (kind TEXT, value TEXT, owner INTEGER, PRIMARY KEY(kind,value))"
        )
        db.execute("CREATE TABLE observations (row INTEGER PRIMARY KEY, stratum TEXT, forced TEXT)")
        for index, record in enumerate(records):
            if progress and index % 10000 == 0:
                progress("grouping", index, total_rows)
            parents.append(index)
            keys = contamination_keys(record, recipe["group_by"])
            if unresolved_groups:
                group_kinds = {kind for kind, _ in keys if kind != "content"}
                unresolved_groups = {
                    field
                    for field in unresolved_groups
                    if GROUP_ALIASES.get(field, field) not in group_kinds
                }
            for field in tuple(unresolved_groups):
                try:
                    field_value(record, field)
                except ValueError:
                    continue
                unresolved_groups.remove(field)
            for kind, value in keys:
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
        if unresolved_groups:
            raise InputValidationError(
                "Unknown partition group fields: " + ", ".join(sorted(unresolved_groups))
            )
        if progress:
            progress("assigning", len(parents), total_rows)
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


def verify(records, recipe, assignment_file):
    counts, groups, strata, seen = Counter(), defaultdict(set), defaultdict(Counter), {}
    with assignment_file.open() as stream:
        for index, (row, line) in enumerate(zip(records, stream, strict=True)):
            assignment = json.loads(line)
            role = assignment["role"]
            if assignment["row"] != index or role not in recipe["fractions"]:
                raise InputValidationError(
                    "Partition assignments must cover source positions exactly once"
                )
            for kind, value in contamination_keys(row, recipe["group_by"]):
                key = (kind, value)
                if key in seen and seen[key] != role:
                    raise InputValidationError(
                        "Partition leaks content or declared groups between roles"
                    )
                seen[key] = role
            for rule in recipe["holdouts"]:
                if field_value(row, rule["field"]) in rule["values"] and role != rule["role"]:
                    raise InputValidationError("Partition violates a holdout rule")
            counts[role] += 1
            groups[role].add(assignment["group"])
            label = (
                json.dumps(field_value(row, recipe["stratify_by"]), sort_keys=True)
                if recipe["stratify_by"]
                else "all"
            )
            strata[role][label] += 1
    if any(not counts[role] for role in recipe["fractions"]):
        raise InputValidationError("Partition leaves an empty role")
    return {
        "source_rows": sum(counts.values()),
        "counts": dict(counts),
        "groups": {role: len(values) for role, values in groups.items()},
        "strata": {role: dict(values) for role, values in strata.items()},
        "duplicates_removed": 0,
        "overlap": {
            "content_and_declared_groups": 0,
            "near_duplicates": "not_checked",
            "pretraining": "unknown",
        },
    }
