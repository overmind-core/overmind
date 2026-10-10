import math

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp


def fit_temperature(samples, count, bounds):
    def objective(value):
        temperature = math.exp(float(value[0]))
        loss = gradient = 0.0
        for sample in samples():
            z = np.asarray(sample["logits"], dtype=np.float64) / temperature
            q = np.asarray(sample["target"], dtype=np.float64)
            logp = z - logsumexp(z)
            loss -= float(q @ logp)
            gradient += float((q - np.exp(logp)) @ z)
        return loss / count, np.array([gradient / count])

    fitted = minimize(
        objective,
        np.array([0.0]),
        jac=True,
        method="L-BFGS-B",
        bounds=[tuple(math.log(value) for value in bounds)],
        options={"maxiter": 100},
    )
    if not fitted.success or not np.isfinite(fitted.fun):
        raise ValueError(f"Decision temperature fitting failed: {fitted.message}")
    return math.exp(float(fitted.x[0]))


def fit_decision_calibration(samples):
    counts = {}
    hard = True
    for sample in samples():
        counts[sample["kind"]] = counts.get(sample["kind"], 0) + 1
        hard = hard and max(sample["target"]) == 1.0
    count = sum(counts.values())
    if not count:
        raise ValueError("Calibration has no valid probability-labelled decisions")
    # Unsloth 2026.10.3 decision.py: global head fit, then per-type relative fits.
    # References stay local; scipy supplies the optimizer and soft targets remain intact.
    head = fit_temperature(samples, count, (0.05, 20.0)) if count >= 10 else 1.0
    temperatures = {}
    for kind, size in sorted(counts.items()):

        def group(kind=kind):
            return (
                {**s, "logits": [z / head for z in s["logits"]]}
                for s in samples()
                if s["kind"] == kind
            )

        relative = fit_temperature(group, size, (0.5, 5.0)) if size >= 10 else 1.0
        temperatures[kind] = head * relative
    loss = 0.0
    for sample in samples():
        z = np.asarray(sample["logits"]) / temperatures[sample["kind"]]
        loss -= float(np.asarray(sample["target"]) @ (z - logsumexp(z)))
    return {
        "temperature": head,
        "temperature_by_kind": temperatures,
        "decisions": count,
        "cross_entropy": loss / count,
        "target_basis": "one_hot_references" if hard else "recorded_distributions",
        "fitted": count >= 10,
        "optimizer": "scipy_l_bfgs_b",
        "scope": "calibration_in_sample",
    }
