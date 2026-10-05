import contextlib
import json
import math
import random
from collections import Counter


def validate_request(*, rows, seed, stratify_by=(), target_type=False, minimum_per_stratum=1):
    if type(rows) is not int or not 1 <= rows <= 1_000_000:
        raise ValueError("rows must be an integer from 1 to 1,000,000")
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("seed must be an integer from 0 to 4,294,967,295")
    if not isinstance(stratify_by, (list, tuple)) or len(stratify_by) > 8:
        raise ValueError("stratify_by supports at most eight column paths")
    if any(not isinstance(key, str) or not key.strip() or len(key) > 200 for key in stratify_by):
        raise ValueError("Each stratum field must be a nonempty column path")
    if len(set(stratify_by)) != len(stratify_by):
        raise ValueError("Stratum fields must be distinct")
    if type(target_type) is not bool:
        raise ValueError("target_type must be boolean")
    if type(minimum_per_stratum) is not int or not 1 <= minimum_per_stratum <= rows:
        raise ValueError("minimum_per_stratum must be between one and rows")
    return dict(
        rows=rows,
        seed=seed,
        stratify_by=list(stratify_by),
        target_type=target_type,
        minimum_per_stratum=minimum_per_stratum,
    )


def field(row, path):
    if path in row:
        value = row[path]
    else:
        value = row
        for part in path.split("."):
            if isinstance(value, str):
                with contextlib.suppress(ValueError):
                    value = json.loads(value)
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"Missing stratum field: {path}")
            value = value[part]
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is not None and type(value) not in {str, int, float, bool}:
        raise ValueError(f"Stratum field must be scalar: {path}")
    return value


def stratum(row, native_decision, config):
    values = [field(row, path) for path in config["stratify_by"]]
    if config["target_type"]:
        decision = native_decision(row) or {}
        if "target_mean" in decision:
            mean, options = decision["target_mean"], decision.get("option_values")
            if (
                decision.get("target_semantics") != "ordinal_mean"
                or decision.get("kind") != "score"
                or "target_probabilities" in decision
                or not isinstance(options, list)
                or len(options) != len(decision.get("options", []))
                or len(options) < 2
                or any(type(v) not in {int, float} or not math.isfinite(v) for v in options)
                or any(a >= b for a, b in zip(options, options[1:], strict=False))
                or type(mean) not in {int, float}
                or not math.isfinite(mean)
                or not options[0] <= mean <= options[-1]
            ):
                raise ValueError("Target stratification requires valid ordinal means")
            values.append("ordinal_mean")
        else:
            q = decision.get("target_probabilities")
            if (
                not isinstance(q, list)
                or len(q) < 2
                or len(q) != len(decision.get("options", []))
                or any(
                    type(v) not in {int, float} or not math.isfinite(v) or not 0 <= v <= 1
                    for v in q
                )
                or not math.isclose(sum(q), 1, abs_tol=1e-6, rel_tol=0)
            ):
                raise ValueError(
                    "Target stratification requires valid native probability distributions"
                )
            values.append("hard" if max(q) == 1 else "soft")
    return json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def census(read_batches, native_decision, config):
    counts = Counter()
    for batch in read_batches():
        for row in batch.to_dict(orient="records"):
            counts[stratum(row, native_decision, config)] += 1
            if len(counts) > 100_000:
                raise ValueError("Sampling supports at most 100,000 strata; choose fewer fields")
    return counts


def feasibility(counts, config):
    total = sum(counts.values())
    minimum = sum(min(n, config["minimum_per_stratum"]) for n in counts.values())
    return {
        "feasible": minimum <= config["rows"] <= total,
        "source_rows": total,
        "strata": len(counts),
        "minimum_rows": minimum,
        "requested_rows": config["rows"],
        "reason": "Requested size cannot retain the minimum for every stratum"
        if config["rows"] < minimum
        else "Requested size exceeds the source"
        if config["rows"] > total
        else "",
    }


def allocation(counts, config):
    if not feasibility(counts, config)["feasible"]:
        return {}
    quotas = {key: min(count, config["minimum_per_stratum"]) for key, count in counts.items()}
    remaining = config["rows"] - sum(quotas.values())
    capacity = {key: counts[key] - quota for key, quota in quotas.items()}
    denominator = sum(capacity.values())
    if denominator:
        for key in quotas:
            quotas[key] += capacity[key] * remaining // denominator
        ranking = sorted(quotas, key=lambda key: (-(capacity[key] * remaining % denominator), key))
        for key in ranking[: config["rows"] - sum(quotas.values())]:
            quotas[key] += 1
    return quotas


def sample_frames(read_batches, native_decision, **request):
    config = validate_request(**request)
    counts = census(read_batches, native_decision, config)
    check = feasibility(counts, config)
    if not check["feasible"]:
        raise ValueError(check["reason"])
    total = check["source_rows"]
    quotas = allocation(counts, config)
    rng = random.Random(config["seed"])
    reservoirs = {key: [] for key in quotas}
    seen = Counter()
    position = 0
    for batch in read_batches():
        for row in batch.to_dict(orient="records"):
            key = stratum(row, native_decision, config)
            seen[key] += 1
            reservoir = reservoirs[key]
            if len(reservoir) < quotas[key]:
                reservoir.append(position)
            else:
                index = rng.randrange(seen[key])
                if index < quotas[key]:
                    reservoir[index] = position
            position += 1
    if seen != counts or any(len(reservoirs[key]) != quota for key, quota in quotas.items()):
        raise ValueError("Source changed during sampling")
    # Retain positions only; even very long selected examples stay in bounded input batches.
    selected = {position for reservoir in reservoirs.values() for position in reservoir}
    offset = emitted = 0
    for batch in read_batches():
        mask = [offset + i in selected for i in range(len(batch))]
        offset += len(batch)
        chosen = batch.loc[mask].copy()
        emitted += len(chosen)
        yield chosen
    if offset != total or emitted != config["rows"]:
        raise ValueError("Source changed while emitting the sample")
