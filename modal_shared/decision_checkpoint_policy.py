import math

from modal_shared.decision_artifact import verify_report


def validate_policy(value, *, has_development):
    if not isinstance(value, dict) or set(value) != {"fractions", "selection"}:
        raise ValueError("Checkpoint policy requires fractions and selection")
    fractions = value["fractions"]
    if (
        not isinstance(fractions, list)
        or not 1 <= len(fractions) <= 8
        or any(
            type(v) not in {int, float} or not math.isfinite(v) or not 0 < v <= 1 for v in fractions
        )
        or fractions != sorted(set(fractions))
        or fractions[-1] != 1
    ):
        raise ValueError("Choose one to eight increasing checkpoint fractions ending at 1")
    if value["selection"] not in {"last", "development_loss"}:
        raise ValueError("Select the last checkpoint or lowest development loss")
    if value["selection"] == "development_loss" and not has_development:
        raise ValueError("Development checkpoint selection requires development data")
    return value


def checkpoint_steps(policy, total_steps):
    return sorted({max(1, math.ceil(fraction * total_steps)) for fraction in policy["fractions"]})


def select_checkpoint(policy, records):
    if not records:
        raise ValueError("No retained checkpoints")
    for record in records:
        verify_report(record.get("reload_verification", {}))
    if policy["selection"] == "last":
        return max(records, key=lambda r: r["step"])
    if any(
        type(r.get("development_loss")) not in {int, float}
        or not math.isfinite(r["development_loss"])
        for r in records
    ):
        raise ValueError("Every checkpoint requires finite development loss for selection")
    return min(records, key=lambda r: (r["development_loss"], r["step"]))
