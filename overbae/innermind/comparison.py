"""Baseline against candidate: sibling runs of one group, paired row by row."""

from __future__ import annotations

from statistics import mean

from overbae.models import EvalRun, EvalScore

# Half a percentage point: smaller deltas read as noise.
EPSILON = 0.005

IMPROVED = "improved"
REGRESSED = "regressed"
UNCHANGED = "unchanged"
ADDED = "added"
REMOVED = "removed"
# Attention order: regressions and lost coverage first, then gains, then noise.
STATUS_ORDER = {REGRESSED: 0, REMOVED: 1, ADDED: 2, IMPROVED: 3, UNCHANGED: 4}


def headline(summary: dict | None) -> float | None:
    return (summary or {}).get("score")


def classify(baseline: float | None, candidate: float | None) -> tuple[float | None, str]:
    if candidate is not None and baseline is None:
        return None, ADDED
    if candidate is None and baseline is not None:
        return None, REMOVED
    if candidate is None and baseline is None:
        return None, UNCHANGED
    delta = candidate - baseline
    if delta > EPSILON:
        return delta, IMPROVED
    if delta < -EPSILON:
        return delta, REGRESSED
    return delta, UNCHANGED


def latest_scores(run: EvalRun) -> dict[str, EvalScore]:
    """The newest score per row; a re-scored row appends rather than replaces."""
    latest: dict[str, EvalScore] = {}
    for score in run.scores.order_by("source_id", "-created_at"):
        latest.setdefault(score.source_id, score)
    return latest


def compare(baseline: EvalRun, candidate: EvalRun) -> dict:
    """Scores only rows both runs scored, so a row one run skipped never moves the delta."""
    base_rows, cand_rows = latest_scores(baseline), latest_scores(candidate)
    paired = sorted(set(base_rows) & set(cand_rows))
    overall_base = [base_rows[k].score for k in paired if base_rows[k].score is not None]
    overall_cand = [cand_rows[k].score for k in paired if cand_rows[k].score is not None]

    wins = losses = ties = 0
    for key in paired:
        b, c = base_rows[key].score, cand_rows[key].score
        if b is None or c is None:
            continue
        if c - b > EPSILON:
            wins += 1
        elif b - c > EPSILON:
            losses += 1
        else:
            ties += 1

    names = {name for key in paired for name in (*base_rows[key].metrics, *cand_rows[key].metrics)}
    metrics = []
    for name in names:
        b_vals = [
            m["score"]
            for k in paired
            if (m := base_rows[k].metrics.get(name)) and m.get("score") is not None
        ]
        c_vals = [
            m["score"]
            for k in paired
            if (m := cand_rows[k].metrics.get(name)) and m.get("score") is not None
        ]
        b_mean = mean(b_vals) if b_vals else None
        c_mean = mean(c_vals) if c_vals else None
        delta, status = classify(b_mean, c_mean)
        metrics.append(
            {
                "name": name,
                "baseline": b_mean,
                "candidate": c_mean,
                "delta": delta,
                "status": status,
            }
        )
    metrics.sort(key=lambda row: (STATUS_ORDER[row["status"]], -abs(row["delta"] or 0.0)))

    b_overall = mean(overall_base) if overall_base else None
    c_overall = mean(overall_cand) if overall_cand else None
    delta, status = classify(b_overall, c_overall)
    return {
        "baseline_run_id": str(baseline.pk),
        "candidate_run_id": str(candidate.pk),
        "baseline_label": baseline.label,
        "candidate_label": candidate.label,
        "overall": {
            "baseline": b_overall,
            "candidate": c_overall,
            "delta": delta,
            "status": status,
        },
        "paired": len(paired),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "metrics": metrics,
        "trust": {
            "baseline": (baseline.summary or {}).get("trust"),
            "candidate": (candidate.summary or {}).get("trust"),
        },
    }


def baseline_of(group) -> EvalRun | None:
    return (
        EvalRun.objects.filter(group=group, is_baseline=True, status=EvalRun.Status.COMPLETED)
        .order_by("-created_at")
        .first()
    )


def baseline_comparison(run: EvalRun) -> list[EvalRun]:
    """Stamps ``summary.baseline_comparison`` on every completed candidate this run's
    completion pairs: all of them when it is the baseline, else itself. Returns them."""
    baseline = baseline_of(run.group)
    if baseline is None:
        return []
    candidates = EvalRun.objects.filter(
        group=run.group, is_baseline=False, status=EvalRun.Status.COMPLETED
    ).select_related("model_ref")
    if not run.is_baseline:
        candidates = candidates.filter(pk=run.pk)
    stamped = []
    for candidate in candidates:
        summary = {**(candidate.summary or {}), "baseline_comparison": compare(baseline, candidate)}
        EvalRun.objects.filter(pk=candidate.pk).update(summary=summary)
        candidate.summary = summary
        stamped.append(candidate)
    return stamped
