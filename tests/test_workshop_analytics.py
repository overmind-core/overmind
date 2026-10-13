import pytest

from overbae.models import Cell
from overbae.services.datasets.analytics import VERSION, describe
from overbae.services.datasets.profile import profile_records


def decision(target, *, options=None, **extra):
    return {
        "decision": {
            "state": '{"amount": 10, "merchant": null}',
            "question": "Fraud?",
            "kind": "choice",
            "options": options or ["No", "Yes"],
            "target_probabilities": target,
            "target_semantics": "categorical_gold",
            **extra,
        }
    }


def test_full_population_finds_rare_class_outside_preview():
    records = [decision([1, 0]) for _ in range(1000)] + [decision([0, 1])]
    report = profile_records(records)["analytics"]
    family = report["families"][0]
    assert family["counts"] == [1000, 1]
    assert family["majority_baseline"] == pytest.approx(1000 / 1001)
    assert report["rows_scanned"] == 1001
    checks = {item["id"]: item for item in report["checks"]}
    assert checks["targets.class_balance"]["status"] == "warning"
    assert "1 of 1,001 rows (0.10%)" in checks["targets.class_balance"]["summary"]
    assert checks["targets.class_coverage"]["status"] == "warning"
    assert checks["inputs.missing"]["status"] == "warning"
    assert checks["inputs.constant"]["status"] == "warning"
    assert checks["leakage.cross_split"]["status"] == "not_checked"


def test_soft_targets_weights_invalid_rows_and_option_order_are_not_collapsed():
    report = profile_records(
        [
            decision([0.5, 0.5], target_semantics="posterior", weight=3),
            decision([1, 0]),
            decision([1, 0], options=["Yes", "No"]),
            decision([float("nan"), 0]),
        ]
    )["analytics"]
    assert len(report["families"]) == 3
    soft = next(f for f in report["families"] if f["semantics"] == "posterior")
    assert soft["counts"] is None
    assert soft["majority_baseline"] is None
    assert soft["target_mass"] == [1.5, 1.5]
    assert report["invalid_decision_rows"] == 1
    assert next(c for c in report["checks"] if c["id"] == "targets.validity")["status"] == "error"
    overflow = profile_records([decision([1, 0], weight=1e308)] * 2)["analytics"]
    assert overflow["families"][0]["weight_sum"] is None
    assert (
        next(c for c in overflow["checks"] if c["id"] == "targets.weights")["status"] == "warning"
    )


def test_untyped_empty_and_many_families_do_not_claim_full_quality_coverage():
    for records in ([], [{"label": 0}], [decision([1, 0], question=str(i)) for i in range(40)]):
        report = profile_records(records)["analytics"]
        assert report["status"] == "completed"
        if len(records) == 40:
            assert report["unlisted_family_rows"] > 0
        else:
            assert report["families"] == []


def test_null_semantics_and_stale_fingerprints_are_not_reported_as_known():
    report = profile_records([decision([1, 0], target_semantics=None)])["analytics"]
    assert report["families"][0]["semantics"] == "unknown"
    assert report["families"][0]["counts"] is None
    assert (
        next(c for c in report["checks"] if c["id"] == "targets.semantics")["status"] == "warning"
    )
    cell = Cell(fingerprint="current", stats={"analytics": {**report, "fingerprint": "old"}})
    assert describe(cell)["status"] == "unmeasured"
    assert describe(cell)["checks"] == []
    cell.stats["analytics"]["fingerprint"] = "current"
    assert describe(cell)["status"] == "completed"
    cell.stats["analytics"]["version"] = VERSION - 1
    assert describe(cell)["status"] == "unmeasured"
