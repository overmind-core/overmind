"""Run-vs-run comparison + regression-trend aggregation.

Mirrors ``aggregateEvaluator``/``buildComparisonRows`` in
``frontend/src/components/evaluations/run-comparison.tsx``; the two must agree
number-for-number. Identity across runs is the evaluator NAME, so a re-authored
evaluator still lines up over time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Half a percentage point: smaller absolute deltas read as noise → "unchanged".
# Mirrors EPSILON in run-comparison.tsx so server + client agree.
_EPSILON = 0.005

IMPROVED = "improved"
REGRESSED = "regressed"
UNCHANGED = "unchanged"
ADDED = "added"
REMOVED = "removed"


@dataclass
class EvalAggregate:
    mean: float | None
    pass_rate: float | None
    n: int
    variant_count: int

    @property
    def primary(self) -> float | None:
        """Pass-rate is the fallback for boolean-only evaluators, which have no
        mean."""
        return self.mean if self.mean is not None else self.pass_rate

    @property
    def present(self) -> bool:
        return self.primary is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": self.mean,
            "pass_rate": self.pass_rate,
            "n": self.n,
            "variant_count": self.variant_count,
            "primary": self.primary,
        }


@dataclass
class RunTrust:
    trusted: bool
    degraded: int
    evaluator_errors: int
    errored: int
    not_applicable: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "trusted": self.trusted,
            "degraded": self.degraded,
            "evaluator_errors": self.evaluator_errors,
            "errored": self.errored,
            "not_applicable": self.not_applicable,
        }


@dataclass
class ComparisonRow:
    name: str
    current: dict[str, Any] | None
    baseline: dict[str, Any] | None
    delta: float | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "current": self.current,
            "baseline": self.baseline,
            "delta": self.delta,
            "status": self.status,
        }


def _mean(xs: list[float]) -> float | None:
    return (sum(xs) / len(xs)) if xs else None


def aggregate_evaluator(summary: dict[str, Any], metric_name: str) -> EvalAggregate:
    """Per-variant means are averaged so a multi-model run yields one number to
    track over time. A variant with no score is skipped, never counted zero."""
    means: list[float] = []
    passes: list[float] = []
    n = 0
    for variant in (summary.get("variants") or {}).values():
        cell = (variant.get("metrics") or {}).get(metric_name)
        if not cell:
            continue
        if cell.get("mean") is not None:
            means.append(cell["mean"])
        if cell.get("pass_rate") is not None:
            passes.append(cell["pass_rate"])
        n += cell.get("n") or 0
    return EvalAggregate(mean=_mean(means), pass_rate=_mean(passes), n=n, variant_count=len(means))


def overall_aggregate(summary: dict[str, Any]) -> EvalAggregate:
    """The run's headline number: a mean of every scored evaluator's aggregate.

    Gate-only evaluators are excluded. A conformance check carries no quality
    information — it passes on nearly every row — but averaging it in creates a
    floor where a task the model got wrong still banks credit for formatting its
    answer correctly. Gates stay visible per-evaluator and can still fail a row.
    """
    gates = set(summary.get("gate_metrics") or [])
    means: list[float] = []
    passes: list[float] = []
    n = 0
    for name in summary.get("metrics") or []:
        if name in gates:
            continue
        agg = aggregate_evaluator(summary, name)
        if agg.mean is not None:
            means.append(agg.mean)
        if agg.pass_rate is not None:
            passes.append(agg.pass_rate)
        n += agg.n
    return EvalAggregate(mean=_mean(means), pass_rate=_mean(passes), n=n, variant_count=len(means))


def classify(
    current: EvalAggregate | None, baseline: EvalAggregate | None
) -> tuple[float | None, str]:
    cur = current.primary if current else None
    base = baseline.primary if baseline else None
    cur_has = cur is not None
    base_has = base is not None
    if cur_has and not base_has:
        return None, ADDED
    if not cur_has and base_has:
        return None, REMOVED
    if not cur_has and not base_has:
        return None, UNCHANGED
    delta = float(cur) - float(base)  # type: ignore[arg-type]
    if delta > _EPSILON:
        return delta, IMPROVED
    if delta < -_EPSILON:
        return delta, REGRESSED
    return delta, UNCHANGED


def run_trust(summary: dict[str, Any]) -> RunTrust:
    """Trustworthy iff nothing degraded or errored. N/A is benign — surfaced,
    but not a trust hit."""
    degraded = (summary.get("trust") or {}).get("degraded") or 0
    error_counts = summary.get("error_counts") or {}
    evaluator_errors = error_counts.get("evaluator_errors") or 0
    errored = error_counts.get("errored") or 0
    not_applicable = (summary.get("applicability") or {}).get("total") or 0
    return RunTrust(
        trusted=degraded == 0 and evaluator_errors == 0 and errored == 0,
        degraded=degraded,
        evaluator_errors=evaluator_errors,
        errored=errored,
        not_applicable=not_applicable,
    )


# Attention-ordered: regressions and lost coverage first, then gains, then noise.
_STATUS_ORDER = {REGRESSED: 0, REMOVED: 1, ADDED: 2, IMPROVED: 3, UNCHANGED: 4}


def build_comparison_rows(current: dict[str, Any], baseline: dict[str, Any]) -> list[ComparisonRow]:
    names = set(current.get("metrics") or []) | set(baseline.get("metrics") or [])
    rows: list[ComparisonRow] = []
    for name in names:
        cur = aggregate_evaluator(current, name)
        base = aggregate_evaluator(baseline, name)
        if not cur.present and not base.present:
            continue
        delta, status = classify(cur, base)
        rows.append(
            ComparisonRow(
                name=name,
                current=cur.to_dict() if cur.present else None,
                baseline=base.to_dict() if base.present else None,
                delta=delta,
                status=status,
            )
        )
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r.status, 5), -abs(r.delta or 0.0)))
    return rows


def _one_variant(summary: dict[str, Any], variant_id: str) -> dict[str, Any]:
    variant = (summary.get("variants") or {}).get(variant_id) or {}
    return {
        "metrics": list((variant.get("metrics") or {}).keys()),
        "variants": {variant_id: variant},
        "gate_metrics": summary.get("gate_metrics") or [],
    }


def compare_variant_to_baseline(summary: dict[str, Any]) -> dict[str, Any]:
    """Each variant against the baseline variant.

    ``compare_runs`` averages every variant in a summary into one number, so a
    multi-model run cannot use it as a gate.
    """
    baseline_id = summary.get("baseline_variant_id")
    variants = summary.get("variants") or {}
    if not baseline_id or baseline_id not in variants:
        return {"baseline_variant_id": baseline_id, "variants": []}
    baseline = _one_variant(summary, baseline_id)
    rows = []
    for variant_id, variant in variants.items():
        if variant_id == baseline_id:
            continue
        compared = compare_runs(_one_variant(summary, variant_id), baseline)
        rows.append(
            {
                "variant_id": variant_id,
                "label": variant.get("label") or variant_id,
                "overall": compared["overall"],
            }
        )
    return {"baseline_variant_id": baseline_id, "variants": rows}


def compare_runs(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    cur_overall = overall_aggregate(current)
    base_overall = overall_aggregate(baseline)
    delta, status = classify(cur_overall, base_overall)
    na_by_evaluator = (current.get("applicability") or {}).get("by_evaluator") or {}
    return {
        "rows": [r.to_dict() for r in build_comparison_rows(current, baseline)],
        "overall": {
            "current": cur_overall.to_dict() if cur_overall.present else None,
            "baseline": base_overall.to_dict() if base_overall.present else None,
            "delta": delta,
            "status": status,
        },
        "trust": {
            "current": run_trust(current).to_dict(),
            "baseline": run_trust(baseline).to_dict(),
        },
        "not_applicable_by_evaluator": na_by_evaluator,
    }
