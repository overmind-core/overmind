import hashlib
import json
import math

from django.utils import timezone

from overbae.services.datasets.examples import decode, missing

VERSION = 2
FAMILY_LIMIT = 8
OPTION_LIMIT = 32
FIELD_LIMIT = 100


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Analytics:
    def __init__(self):
        self.rows = self.decisions = self.invalid = self.unlisted = self.unknown = 0
        self.fields = {}
        self.field_overflow = False
        self.input_rows = 0
        self.structured_input_rows = self.weighted_rows = 0
        self.families = {}

    def observe(self, decision, *, valid):
        self.rows += 1
        if not isinstance(decision, dict):
            return
        self.decisions += 1
        if not valid:
            self.invalid += 1
            return
        self.input_rows += 1
        state = decode(decision["state"])
        values = state if isinstance(state, dict) else {"state": decision["state"]}
        self.structured_input_rows += isinstance(state, dict)
        self.weighted_rows += decision.get("weight", 1.0) != 1.0
        for name, value in values.items():
            key = str(name)
            if key not in self.fields and len(self.fields) >= FIELD_LIMIT:
                self.field_overflow = True
                continue
            field = self.fields.setdefault(
                key, {"present": 0, "empty": 0, "first": None, "varies": False}
            )
            field["present"] += 1
            if missing(value):
                field["empty"] += 1
            else:
                signature = digest(value)
                if field["first"] is None:
                    field["first"] = signature
                field["varies"] |= signature != field["first"]
        semantics = decision.get("target_semantics") or "unknown"
        self.unknown += semantics == "unknown"
        options = decision["options"]
        key = digest([decision["question"], decision["kind"], options, semantics])
        if (
            len(options) > OPTION_LIMIT
            or key not in self.families
            and len(self.families) >= FAMILY_LIMIT
        ):
            self.unlisted += 1
            return
        family = self.families.setdefault(
            key,
            {
                "id": key,
                "question": decision["question"][:160],
                "labels": [label[:120] for label in options],
                "labels_truncated": any(len(label) > 120 for label in options),
                "semantics": semantics,
                "rows": 0,
                "weight_sum": 0.0,
                "counts": [0] * len(options) if semantics == "categorical_gold" else None,
                "target_mass": [0.0] * len(options) if "target_probabilities" in decision else None,
                "majority_baseline": None,
            },
        )
        family["rows"] += 1
        weight = decision.get("weight", 1.0)
        family["weight_sum"] += weight
        probabilities = decision.get("target_probabilities")
        if probabilities is not None:
            for index, probability in enumerate(probabilities):
                family["target_mass"][index] += weight * probability
            if family["counts"] is not None:
                family["counts"][probabilities.index(1)] += 1

    def report(self):
        families = [dict(family) for family in self.families.values()]
        gold = [family for family in families if family["counts"] is not None]
        overflow = False
        for family in families:
            if not math.isfinite(family["weight_sum"]):
                family["weight_sum"] = None
                family["target_mass"] = None
                overflow = True
        for family in gold:
            family["majority_baseline"] = max(family["counts"]) / family["rows"]
        imbalanced = [f["id"] for f in gold if min(f["counts"]) / f["rows"] < 0.05]
        rare = [f["id"] for f in gold if min(f["counts"]) < 20]
        absent = [f["id"] for f in gold if 0 in f["counts"]]
        minority_findings = []
        for family in gold:
            if family["id"] in imbalanced:
                count = min(family["counts"])
                label = family["labels"][family["counts"].index(count)]
                minority_findings.append(
                    f"“{label}” accounts for {count:,} of {family['rows']:,} rows ({count / family['rows']:.2%})."
                )
        missing_fields = []
        constants = []
        for name, field in self.fields.items():
            count = self.input_rows - field["present"] + field["empty"]
            if count:
                missing_fields.append({"field": name[:120], "rows": count})
            if field["first"] is not None and not field["varies"]:
                constants.append(name[:120])

        def check(identifier, title, status, summary, metrics=None):
            return {
                "id": identifier,
                "title": title,
                "status": status,
                "summary": summary,
                "metrics": metrics or {},
            }

        checks = [
            check(
                "targets.validity",
                "Target validity",
                "error" if self.invalid else "pass" if self.decisions else "not_applicable",
                f"{self.invalid:,} of {self.decisions:,} decision rows have invalid options or targets; they are excluded from class counts."
                if self.invalid
                else f"All {self.decisions:,} decision rows have valid options and targets."
                if self.decisions
                else "No decision targets were found in these rows.",
                {"invalid_rows": self.invalid},
            ),
            check(
                "targets.class_balance",
                "Class imbalance",
                "warning" if imbalanced else "pass" if gold else "not_applicable",
                " ".join(minority_findings)
                + " Overall accuracy can hide errors on the rare class; compare per-class precision and recall."
                if imbalanced
                else "Every observed class has at least 5% of the rows in its decision family."
                if gold
                else "Class balance needs declared observed labels; probability targets are not counted as labels.",
                {"threshold": 0.05, "families": imbalanced},
            ),
            check(
                "targets.class_coverage",
                "Rare or missing classes",
                "warning" if rare else "pass" if gold else "not_applicable",
                f"{len(rare):,} decision families have a class with fewer than 20 examples; {len(absent):,} have a class with no examples."
                if rare
                else "Every declared class has at least 20 examples."
                if gold
                else "No observed-label counts are available for this check.",
                {"minimum_count": 20, "rare_families": rare, "missing_class_families": absent},
            ),
            check(
                "targets.semantics",
                "Target encoding",
                "warning" if self.unknown else "pass" if self.input_rows else "not_checked",
                f"{self.unknown:,} rows do not declare whether targets are observed labels or probability estimates."
                if self.unknown
                else f"{self.input_rows:,} rows declare their target meaning. The source labels have not been independently verified."
                if self.input_rows
                else "No valid decision targets are available to assess.",
            ),
            check(
                "targets.weights",
                "Observation weights",
                "warning"
                if self.weighted_rows or overflow
                else "pass"
                if self.input_rows
                else "not_applicable",
                f"{self.weighted_rows:,} of {self.input_rows:,} valid rows use weights other than 1. Class counts stay unweighted."
                + (
                    " Weighted totals exceed the supported numeric range and are unavailable."
                    if overflow
                    else ""
                )
                if self.weighted_rows or overflow
                else f"All {self.input_rows:,} valid rows have weight 1; each contributes equally.",
                {"nonunit_weight_rows": self.weighted_rows, "aggregate_overflow": overflow},
            ),
            check(
                "inputs.missing",
                "Missing input values",
                "warning" if missing_fields else "pass" if self.input_rows else "not_checked",
                "Missing values: "
                + "; ".join(
                    f"{field['field']} ({field['rows']:,} rows)" for field in missing_fields[:5]
                )
                + (
                    f"; and {len(missing_fields) - 5} more fields."
                    if len(missing_fields) > 5
                    else "."
                )
                if missing_fields
                else f"All {self.input_rows:,} state texts are nonempty. Feature values inside the text were not checked."
                if self.input_rows and not self.structured_input_rows
                else f"No missing values detected in {len(self.fields):,} measured inputs across {self.input_rows:,} rows."
                if self.input_rows
                else "No valid decision inputs are available to assess.",
                {
                    "input_rows": self.input_rows,
                    "affected_fields": len(missing_fields),
                    "fields": missing_fields[:20],
                },
            ),
            check(
                "inputs.constant",
                "Constant input fields",
                "warning" if constants else "pass" if self.input_rows else "not_checked",
                "Unchanged across nonempty values: "
                + ", ".join(constants[:5])
                + (f"; and {len(constants) - 5} more fields." if len(constants) > 5 else ".")
                if constants
                else "State text varies between rows. Individual features inside the text were not checked."
                if self.input_rows and not self.structured_input_rows
                else f"No constant fields among {len(self.fields):,} measured inputs."
                if self.input_rows
                else "No valid decision inputs are available to assess.",
                {"affected_fields": len(constants), "fields": constants[:20]},
            ),
            check(
                "inputs.structured_fields",
                "Feature-level checks",
                "not_checked"
                if self.structured_input_rows < self.input_rows or not self.input_rows
                else "pass",
                f"{self.input_rows - self.structured_input_rows:,} rows store features as text. Missing or constant values inside that text were not checked."
                if self.structured_input_rows < self.input_rows
                else f"All {self.structured_input_rows:,} valid rows expose JSON fields for input checks."
                if self.input_rows
                else "No valid decision inputs are available to assess.",
                {
                    "structured_rows": self.structured_input_rows,
                    "whole_text_rows": self.input_rows - self.structured_input_rows,
                },
            ),
            check(
                "coverage.analytics",
                "Scan coverage",
                "warning" if self.unlisted or self.field_overflow else "pass",
                f"All {self.rows:,} rows scanned; {len(families):,} decision families summarized. "
                + (
                    f"{self.unlisted:,} rows fall outside the family detail limit; their class balance is not assessed. "
                    + (
                        "Only the first 100 input fields were assessed."
                        if self.field_overflow
                        else "All input fields were included."
                    )
                    if self.unlisted or self.field_overflow
                    else "No family or input-field detail limits were reached."
                ),
                {
                    "unlisted_family_rows": self.unlisted,
                    "fields_truncated": self.field_overflow,
                    "family_limit": FAMILY_LIMIT,
                    "option_limit": OPTION_LIMIT,
                    "field_limit": FIELD_LIMIT,
                },
            ),
            check(
                "leakage.cross_split",
                "Duplicates and split leakage",
                "not_checked",
                "No comparison with other dataset splits was run. Shared records or entities between training and evaluation have not been ruled out.",
            ),
            check(
                "inputs.availability",
                "Feature availability and outliers",
                "not_checked",
                "No prediction-time rules or valid field ranges were supplied. Future-only features and out-of-range values were not checked.",
            ),
            check(
                "evaluation.readiness",
                "Evaluation strategy",
                "not_checked",
                "No model was evaluated by this scan. Model quality and task-specific acceptance criteria remain unassessed.",
            ),
        ]
        return {
            "version": VERSION,
            "status": "completed",
            "measured_at": timezone.now().isoformat(),
            "rows_scanned": self.rows,
            "population": "full",
            "decision_rows": self.decisions,
            "invalid_decision_rows": self.invalid,
            "unlisted_family_rows": self.unlisted,
            "families": families,
            "checks": checks,
        }


def describe(cell):
    report = (cell.stats or {}).get("analytics") or {}
    current = report.get("version") == VERSION and report.get("fingerprint") == cell.fingerprint
    return {
        **(report if current else {}),
        "status": report.get("status", "unmeasured") if current else "unmeasured",
        "cell": str(cell.pk),
        "dataset": str(cell.dataset_id),
        "fingerprint": cell.fingerprint,
        "checks": report.get("checks", []) if current else [],
        "families": report.get("families", []) if current else [],
    }
