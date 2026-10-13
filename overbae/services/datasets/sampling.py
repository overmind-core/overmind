import contextlib
import heapq
import json
import math
import random
from collections import Counter, defaultdict

from overbae.services.datasets import rows
from overbae.services.datasets.examples import input_objects


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


def select_rows(cell, *, limit: int | None, fraction: float = 1.0):
    if not limit and fraction == 1.0:
        return list(rows.iter_rows(cell))
    groups = defaultdict(list)
    configured = (cell.dataset.source_spec or {}).get("split", {}).get("stratify_by")
    fields = (
        [configured]
        if configured
        else ["worker_mode", "mode", "task_type", "behaviour_key", "label"]
    )
    for row in rows.iter_rows(cell):
        values = input_objects({**row.extra, "input": row.input})
        label = next(
            (
                (field, str(value[field]))
                for field in fields
                for value in values
                if isinstance(value.get(field), (str, int, bool)) and value[field] != ""
            ),
            ("", ""),
        )
        groups[label].append(row.index)
    total = sum(map(len, groups.values()))
    target = min(total, limit or total, math.ceil(total * fraction))
    rng = random.Random(str(cell.id))
    labels = sorted(groups)
    rng.shuffle(labels)
    for indices in groups.values():
        rng.shuffle(indices)
    # Give each stratum a place, then distribute the remaining budget in proportion.
    queue = [(0.0, order, 0, label) for order, label in enumerate(labels)]
    heapq.heapify(queue)
    selected = []
    while queue and len(selected) < target:
        _, order, count, label = heapq.heappop(queue)
        indices = groups[label]
        selected.append(indices[count])
        count += 1
        if count < len(indices):
            heapq.heappush(queue, (count / len(indices), order, count, label))
    return rows.rows(cell, sorted(selected))
