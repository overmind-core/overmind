from __future__ import annotations

import math
import random
from collections import defaultdict
from typing import Any

_BOOTSTRAP_ROUNDS = 200
_REGRESSION_EPS = 1e-9


def rollup(
    scores: list[dict[str, Any]],
    *,
    baseline_variant_id: str | None = None,
) -> dict[str, Any]:
    """``scores`` items: ``{variant_id, variant_label, name, value, passed,
    data_type, scope, outcome}``.

    Only ``outcome == "scored"`` rows feed means and pass-rates, so abstained,
    not-applicable, skipped and errored rows never dilute a real result.
    ``outcome`` defaults to ``"scored"`` when absent.
    """
    # variant -> metric -> list of (value, passed, outcome, group_key)
    agg: dict[str, dict[str, list[tuple[float | None, bool | None, str, str | None]]]] = (
        defaultdict(lambda: defaultdict(list))
    )
    labels: dict[str, str] = {}
    metrics: set[str] = set()
    for s in scores:
        vid = s.get("variant_id") or "__none__"
        labels[vid] = s.get("variant_label", vid)
        name = s.get("name", "")
        metrics.add(name)
        agg[vid][name].append(
            (s.get("value"), s.get("passed"), s.get("outcome", "scored"), s.get("sample_id"))
        )

    # A statistical evaluator reports one dataset-scope row, so len(pairs) == 1
    # even for N samples; the real sample count arrives as n_override.
    n_overrides: dict[str, dict[str, int]] = defaultdict(dict)
    for s in scores:
        if s.get("n_override") is not None:
            vid = s.get("variant_id") or "__none__"
            name = s.get("name", "")
            n_overrides[vid][name] = int(s["n_override"])

    variants: dict[str, Any] = {}
    for vid, by_metric in agg.items():
        metric_rows: dict[str, Any] = {}
        for name, pairs in by_metric.items():
            scored = [(v, g) for v, _, o, g in pairs if v is not None and o == "scored"]
            values = [v for v, _ in scored]
            passes = [p for _, p, o, _ in pairs if p is not None and o == "scored"]
            mean = (sum(values) / len(values)) if values else None
            # A single value has no spread — an unset CI makes `_ci_separable`
            # report False instead of calling an n=1 difference significant.
            ci_low, ci_high = _bootstrap_ci(scored) if len(values) >= 2 else (None, None)
            metric_rows[name] = {
                "mean": mean,
                "n": n_overrides.get(vid, {}).get(name, len(pairs)),
                "pass_rate": (sum(1 for p in passes if p) / len(passes)) if passes else None,
                "ci_low": ci_low,
                "ci_high": ci_high,
            }
        variants[vid] = {"label": labels.get(vid, vid), "metrics": metric_rows}

    # ``separable`` is a CI-overlap test between baseline and variant means —
    # enough to gray out statistically inseparable deltas in the UI.
    if baseline_variant_id and baseline_variant_id in variants:
        base_metrics = variants[baseline_variant_id]["metrics"]
        for vid, row in variants.items():
            if vid == baseline_variant_id:
                continue
            for name, cell in row["metrics"].items():
                base = base_metrics.get(name, {})
                base_mean = base.get("mean")
                if base_mean is not None and cell.get("mean") is not None:
                    delta = cell["mean"] - base_mean
                    cell["delta"] = delta
                    cell["regression"] = delta < -_REGRESSION_EPS
                    cell["separable"] = _ci_separable(cell, base)

    return {
        "metrics": sorted(metrics),
        "variants": variants,
        "baseline_variant_id": baseline_variant_id,
    }


def _bootstrap_ci(
    scored: list[tuple[float, str | None]],
    *,
    rounds: int = _BOOTSTRAP_ROUNDS,
    seed: int = 13,
) -> tuple[float, float]:
    """95% bootstrap CI on the mean of ``[(value, group_key)]``. Rows sharing a
    ``group_key`` are resampled together so the CI reflects conversation-level,
    not turn-level, independence. A null group key is its own group."""
    grouped: dict[str, list[float]] = defaultdict(list)
    for i, (value, group) in enumerate(scored):
        grouped[group if group is not None else f"__row_{i}"].append(value)
    keys = list(grouped.keys())
    n = len(keys)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(rounds):
        pool: list[float] = []
        for _ in range(n):
            pool.extend(grouped[keys[rng.randrange(n)]])
        if pool:
            means.append(sum(pool) / len(pool))
    if not means:
        return 0.0, 0.0
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(len(means) - 1, int(0.975 * len(means)))]
    return round(lo, 4), round(hi, 4)


def _ci_separable(cell: dict[str, Any], base: dict[str, Any]) -> bool:
    for key in ("ci_low", "ci_high"):
        if cell.get(key) is None or base.get(key) is None:
            return False
    return cell["ci_low"] > base["ci_high"] or cell["ci_high"] < base["ci_low"]


def bradley_terry(
    pairwise: list[dict[str, Any]],
    *,
    rounds: int = _BOOTSTRAP_ROUNDS,
    seed: int = 7,
) -> dict[str, Any]:
    """``pairwise`` items: ``{a, b, winner}`` where ``winner in {a, b, "tie"}``.
    Ratings are on an Elo-like scale (1000 mean) with a 95% bootstrap CI."""
    competitors = sorted({c for m in pairwise for c in (m["a"], m["b"])})
    if len(competitors) < 2:
        return {"ratings": {}, "ranking": competitors}

    point = _fit_bt(pairwise, competitors)

    rng = random.Random(seed)
    samples: dict[str, list[float]] = {c: [] for c in competitors}
    n = len(pairwise)
    for _ in range(rounds):
        resampled = [pairwise[rng.randrange(n)] for _ in range(n)]
        fit = _fit_bt(resampled, competitors)
        for c in competitors:
            samples[c].append(fit[c])

    ratings: dict[str, Any] = {}
    for c in competitors:
        vals = sorted(samples[c])
        lo = vals[int(0.025 * len(vals))]
        hi = vals[min(len(vals) - 1, int(0.975 * len(vals)))]
        ratings[c] = {"rating": round(point[c], 1), "ci_low": round(lo, 1), "ci_high": round(hi, 1)}

    ranking = sorted(competitors, key=lambda c: point[c], reverse=True)
    return {"ratings": ratings, "ranking": ranking}


def _fit_bt(
    pairwise: list[dict[str, Any]], competitors: list[str], iters: int = 100
) -> dict[str, float]:
    """MM-algorithm MLE for Bradley-Terry, returned on an Elo-like scale."""
    # A tie counts as half a win to each side.
    wins: dict[str, float] = defaultdict(float)
    games: dict[tuple[str, str], float] = defaultdict(float)
    for m in pairwise:
        a, b, w = m["a"], m["b"], m.get("winner")
        games[(a, b)] += 1
        games[(b, a)] += 1
        if w == a:
            wins[a] += 1
        elif w == b:
            wins[b] += 1
        else:
            wins[a] += 0.5
            wins[b] += 0.5

    strength = dict.fromkeys(competitors, 1.0)
    for _ in range(iters):
        new = {}
        for c in competitors:
            denom = 0.0
            for o in competitors:
                if o == c:
                    continue
                n_co = games.get((c, o), 0.0)
                if n_co:
                    denom += n_co / (strength[c] + strength[o])
            new[c] = (wins.get(c, 0.0) + 1e-6) / (denom + 1e-9)
        # Normalize to keep the geometric mean at 1 (identifiability).
        geo = math.exp(sum(math.log(max(v, 1e-12)) for v in new.values()) / len(new))
        strength = {c: v / geo for c, v in new.items()}

    # Map log-strength to an Elo-like scale (mean 1000, 400/ln(10) slope).
    scale = 400.0 / math.log(10)
    return {c: 1000.0 + scale * math.log(max(strength[c], 1e-12)) for c in competitors}
