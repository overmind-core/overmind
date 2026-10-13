import hashlib
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from decimal import Decimal

DEFAULT_POLICY = {
    "version": 1,
    "mode": "adaptive",
    "initial": True,
    "final": True,
    "loss_sample": 2048,
    "train_sample": 256,
    "seed": 42,
    "interval_steps": None,
    "target_seconds": 300,
    "overhead_fraction": 0.1,
    "max_checks": 12,
    "generation_every": 3,
    "generation": None,
    "selection": "last",
    "early_stopping": None,
    "failure_policy": "continue",
}


def capabilities(provider):
    controlled = provider in {"modal", "baseten"}
    return {
        "schedule_modes": ["adaptive", "steps", "epoch", "off"] if controlled else ["off"],
        "loss_metrics": controlled,
        "generation_checks": ["classification", "exact_match", "json_schema", "json_fields"]
        if provider == "modal"
        else [],
        "native_decisions": provider == "modal",
        "checkpoint_reload_verification": controlled,
        "optimizer_resume": "native_latest_state_only" if provider == "modal" else "not_qualified",
        "runtime_qualification": "release_specific; CPU fixtures do not qualify GPU execution",
        "limitations": [
            "Generation references come from the final supervised answer, not tool execution",
            "Metered judges, challenge suites and declared slice sampling are not supported",
            "Monitoring overhead target is not a spend limit",
        ]
        + (
            ["Generation evidence collection is not qualified on this provider"]
            if provider != "modal"
            else []
        ),
    }


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def generation_tokens(row):
    ids, labels = row["input_ids"], row["labels"]
    supervised = [index for index, label in enumerate(labels) if label != -100]
    if len(ids) != len(labels) or not supervised:
        raise ValueError("Generation requires a supervised final answer")
    end = supervised[-1] + 1
    start = end - 1
    while start > 0 and labels[start - 1] != -100:
        start -= 1
    if start == 0:
        raise ValueError("Generation requires an input-only prefix")
    return ids[:start], ids[start:end]


def _number(value, name, low, high, *, integer=False):
    types = {int} if integer else {int, float}
    if type(value) not in types or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(
            f"{name} must be {'an integer' if integer else 'finite'} in [{low}, {high}]"
        )


def resolve_policy(value, *, has_development, provider):
    if value is not None and (not isinstance(value, dict) or set(value) - DEFAULT_POLICY.keys()):
        raise ValueError("Unknown monitoring configuration fields")
    policy = {**DEFAULT_POLICY, **(value or {})}
    if value is None and (not has_development or provider not in {"modal", "baseten"}):
        policy["mode"] = "off"
    if policy["version"] != 1 or type(policy["version"]) is not int:
        raise ValueError("Unsupported monitoring version")
    if policy["mode"] not in {"adaptive", "steps", "epoch", "off"}:
        raise ValueError("Monitoring mode must be adaptive, steps, epoch or off")
    for name in ("initial", "final"):
        if type(policy[name]) is not bool:
            raise ValueError(f"{name} must be a boolean")
    for name, low, high in (
        ("loss_sample", 1, 1000000),
        ("train_sample", 0, 1000000),
        ("seed", 0, 2**32 - 1),
        ("max_checks", 1, 100),
        ("generation_every", 1, 100),
    ):
        _number(policy[name], name, low, high, integer=True)
    _number(policy["target_seconds"], "target_seconds", 1, 86400)
    _number(policy["overhead_fraction"], "overhead_fraction", 0.001, 0.9)
    if policy["interval_steps"] is not None:
        _number(policy["interval_steps"], "interval_steps", 1, 100000000, integer=True)
    if policy["mode"] == "steps" and policy["interval_steps"] is None:
        raise ValueError("Step monitoring requires interval_steps")
    if policy["mode"] != "steps" and policy["interval_steps"] is not None:
        raise ValueError("interval_steps requires steps mode")
    if policy["selection"] not in {"last", "development_loss"}:
        raise ValueError("Select last or development_loss")
    if policy["failure_policy"] not in {"continue", "stop"}:
        raise ValueError("failure_policy must be continue or stop")
    generation = policy["generation"]
    if generation is not None:
        if not isinstance(generation, dict) or set(generation) - {
            "kind",
            "labels",
            "sample",
            "max_new_tokens",
            "normalization",
            "schema",
            "fields",
        }:
            raise ValueError("Unknown generation probe fields")
        generation = {"sample": 256, "max_new_tokens": 128, "normalization": "strip", **generation}
        if generation.get("kind") not in {
            "classification",
            "exact_match",
            "json_schema",
            "json_fields",
        }:
            raise ValueError(
                "Generation probes support classification, exact_match, json_schema or json_fields"
            )
        if generation["normalization"] not in {"none", "strip", "strip_thinking"}:
            raise ValueError("Unknown output normalization")
        _number(generation["sample"], "generation.sample", 1, 10000, integer=True)
        _number(generation["max_new_tokens"], "generation.max_new_tokens", 1, 4096, integer=True)
        if generation["kind"] == "classification":
            labels = generation.get("labels")
            if (
                not isinstance(labels, list)
                or not 2 <= len(labels) <= 1000
                or any(not isinstance(label, str) or not label for label in labels)
                or len(set(labels)) != len(labels)
            ):
                raise ValueError("Classification requires distinct, explicit labels")
        if generation["kind"] == "json_schema" and not isinstance(generation.get("schema"), dict):
            raise ValueError("json_schema requires a schema object")
        fields = generation.get("fields")
        if generation["kind"] == "json_fields":
            if generation.get("schema") is not None or generation.get("labels") is not None:
                raise ValueError(
                    "json_fields cannot combine schema or labels with field comparisons"
                )
            if (
                not isinstance(fields, list)
                or not 1 <= len(fields) <= 64
                or any(
                    not isinstance(path, str)
                    or len(path) > 1024
                    or (path != "" and not path.startswith("/"))
                    or re.search(r"~(?![01])", path)
                    or path.count("/") > 64
                    for path in fields
                )
                or len(set(fields)) != len(fields)
            ):
                raise ValueError(
                    "json_fields requires 1–64 distinct JSON Pointers, at most 1024 characters and 64 segments each"
                )
        elif fields is not None:
            raise ValueError("fields requires json_fields generation")
        policy["generation"] = generation
    early = policy["early_stopping"]
    if early is not None:
        if not isinstance(early, dict) or set(early) - {"patience", "min_delta", "warmup_checks"}:
            raise ValueError("Unknown early_stopping fields")
        early = {"patience": 3, "min_delta": 0.0, "warmup_checks": 2, **early}
        _number(early["patience"], "patience", 1, 100, integer=True)
        _number(early["min_delta"], "min_delta", 0, 1000000)
        _number(early["warmup_checks"], "warmup_checks", 0, 100, integer=True)
        policy["early_stopping"] = early
    required = policy["selection"] != "last" or early is not None
    if policy["mode"] == "off" and (required or generation is not None):
        raise ValueError(
            "Monitoring selection, generation and early stopping require development checks"
        )
    if policy["mode"] != "off":
        if not has_development:
            raise ValueError("Monitoring requires development data")
        if provider not in {"modal", "baseten"}:
            raise ValueError("This provider does not support the requested monitoring policy")
    if required:
        if not policy["final"]:
            raise ValueError("Development selection and stopping require final validation")
        if value and value.get("failure_policy") == "continue":
            raise ValueError("Required selection/stop checks cannot continue after failure")
        policy["failure_policy"] = "stop"
    return policy


def freeze_probe(rows, *, target, seed):
    groups = defaultdict(list)
    for index, row in enumerate(rows):
        groups[str(row.get("group") or row.get("key") or fingerprint(row))].append(index)
    ordered = sorted(groups, key=lambda group: fingerprint([seed, group]))
    selected = []
    for group in ordered:
        if len(selected) >= target:
            break
        selected.extend(groups[group])
    selected.sort()
    identities = [{"index": i, "sha256": fingerprint(rows[i])} for i in selected]
    return {
        "indices": selected,
        "identities": identities,
        "fingerprint": fingerprint(identities),
        "requested_rows": target,
        "actual_rows": len(selected),
        "population_rows": len(rows),
        "groups": len(
            {
                str(rows[i].get("group") or rows[i].get("key") or fingerprint(rows[i]))
                for i in selected
            }
        ),
        "sampling": "seeded_whole_groups; duplicates retained; descriptive unweighted evidence",
        "seed": seed,
    }


def row_identity(row):
    return {
        "key": row.get("key") or fingerprint(row),
        "group": row.get("group"),
        "sha256": fingerprint(row),
    }


def freeze_plan(policy, train, development):
    probes = {
        "development": freeze_probe(development, target=policy["loss_sample"], seed=policy["seed"]),
        "training_reference": freeze_probe(
            train, target=policy["train_sample"], seed=policy["seed"]
        ),
    }
    if policy["generation"]:
        probes["generation"] = freeze_probe(
            development, target=policy["generation"]["sample"], seed=policy["seed"]
        )
    return {
        "policy": policy,
        "policy_fingerprint": fingerprint(policy),
        "probes": probes,
        "identity": fingerprint({"policy": policy, "probes": probes}),
    }


class MonitoringSchedule:
    def __init__(self, policy, total_steps, *, state=None):
        self.policy, self.total_steps = policy, total_steps
        self.checks, self.step_seconds = 0, []
        self.last_checked_step = None
        self.interval_steps = None
        self.next_step = (
            policy["interval_steps"]
            if policy["mode"] == "steps"
            else max(1, math.ceil(total_steps * 0.1))
        )
        if policy["mode"] in {"off", "epoch"} or total_steps <= 1:
            self.next_step = None
        if state is not None:
            self.checks = state["checks"]
            self.step_seconds = list(state["step_seconds"])
            self.next_step = state["next_step"]
            self.last_checked_step = state["last_checked_step"]
            self.interval_steps = state.get("interval_steps")

    def observe_step(self, duration):
        _number(duration, "optimizer_step_seconds", 1e-9, 86400)
        self.step_seconds = [*self.step_seconds[-29:], duration]

    def due(self, step):
        return self.next_step is not None and self.next_step <= step < self.total_steps

    def final_required(self, step):
        return self.policy["mode"] != "off" and self.policy["final"]

    def completed(self, *, step, duration):
        _number(duration, "monitoring_seconds", 0, 86400 * 7)
        self.checks += 1
        self.last_checked_step = step
        median = statistics.median(self.step_seconds) if self.step_seconds else None
        overhead_interval = None
        target_interval = max(1, math.ceil(self.total_steps * 0.1))
        if median:
            b = self.policy["overhead_fraction"]
            overhead_interval = math.ceil(duration * (1 - b) / (b * median))
            target_interval = max(1, math.ceil(self.policy["target_seconds"] / median))
        candidate = max(target_interval, overhead_interval or 0)
        interval, adaptation = candidate, "first_measurement" if median else "provisional"
        mode = self.policy["mode"]
        if mode == "adaptive" and median and self.interval_steps is not None:
            previous = self.interval_steps
            if abs(candidate - previous) <= max(1, previous * 0.2):
                interval, adaptation = previous, "within_hysteresis"
            else:
                interval = min(previous * 2, max(math.ceil(previous / 2), candidate))
                adaptation = "bounded_change" if interval != candidate else "measured_change"
        elif mode == "steps":
            candidate = interval = self.policy["interval_steps"]
            adaptation = "explicit_interval"
        self.interval_steps = interval
        self.next_step, stop_reason = step + interval, None
        conflicts = []
        if mode in {"off", "epoch"}:
            stop_reason = "disabled" if mode == "off" else "epoch_boundary"
        elif self.checks >= self.policy["max_checks"]:
            stop_reason = "check_limit"
        elif self.next_step >= self.total_steps:
            stop_reason = "run_boundary"
            remaining = self.total_steps - step
            if (
                mode == "adaptive"
                and overhead_interval
                and target_interval < remaining <= overhead_interval
            ):
                conflicts.append(
                    "monitoring_budget_conflict: overhead target prevents another periodic check"
                )
        elif mode == "adaptive" and overhead_interval and interval < overhead_interval:
            conflicts.append(
                "monitoring_budget_conflict: bounded cadence cannot yet meet overhead target"
            )
        if stop_reason is not None:
            self.next_step = None
        return {
            "step": step,
            "next_step": self.next_step,
            "check_seconds": duration,
            "optimizer_step_seconds_median": median,
            "overhead_interval_steps": overhead_interval,
            "target_interval_steps": target_interval,
            "candidate_interval_steps": candidate,
            "effective_interval_steps": interval,
            "adaptation": adaptation,
            "stop_reason": stop_reason,
            "basis": "measured_optimizer_time" if median else "provisional_step_schedule",
            "formula_version": 2,
            "conflicts": conflicts,
        }

    def state(self):
        return {
            "checks": self.checks,
            "step_seconds": self.step_seconds,
            "next_step": self.next_step,
            "last_checked_step": self.last_checked_step,
            "interval_steps": self.interval_steps,
        }


def classification_metrics(examples, labels):
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    support, predictions, references = Counter(), Counter(), Counter()
    technical, invalid, unscorable, scored, correct = 0, 0, 0, 0, 0
    for example in examples:
        if example.get("reference") in index:
            references[example["reference"]] += 1
        if example["status"] != "completed":
            technical += 1
            continue
        reference, prediction = example["reference"], example["prediction"]
        if reference not in index:
            unscorable += 1
            continue
        support[reference] += 1
        scored += 1
        if prediction not in index:
            invalid += 1
            continue
        predictions[prediction] += 1
        matrix[index[reference]][index[prediction]] += 1
        correct += int(reference == prediction)
    per_class = {}
    for label, i in index.items():
        precision = matrix[i][i] / predictions[label] if predictions[label] else None
        recall = matrix[i][i] / support[label] if support[label] else None
        f1 = 2 * precision * recall / (precision + recall) if recall and precision else 0.0
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1 if support[label] else None,
            "support": support[label],
        }
    represented = [value["f1"] for value in per_class.values() if value["support"]]
    return {
        "expected": len(examples),
        "scored": scored,
        "technical_errors": technical,
        "invalid_labels": invalid,
        "unscorable": unscorable,
        "coverage": scored / len(examples) if examples else None,
        "accuracy": correct / scored if scored else None,
        "macro_f1": statistics.mean(represented) if represented else None,
        "invalid_label_rate": invalid / scored if scored else None,
        "confusion_matrix": matrix,
        "labels": labels,
        "per_class": per_class,
        "prediction_distribution": dict(predictions),
        "reference_distribution": dict(references),
        "unrepresented_labels": [label for label in labels if not references[label]],
        "interpretation": "development sample; technical errors excluded from accuracy, retained in coverage",
    }


def paired_generation_metrics(before, after, *, seed, labels=None):
    previous = {item["row"]: item for item in before}
    current = {item["row"]: item for item in after}
    groups = defaultdict(list)
    improved = regressed = unchanged = 0
    for identity in previous.keys() & current.keys():
        left, right = previous[identity], current[identity]
        if left.get("status") != "completed" or right.get("status") != "completed":
            continue
        if left.get("reference") != right.get("reference"):
            continue
        if labels is not None and right.get("reference") not in labels:
            continue
        if any("passed" in item and type(item["passed"]) is not bool for item in (left, right)):
            continue
        old = left.get("passed", left.get("prediction") == left.get("reference"))
        new = right.get("passed", right.get("prediction") == right.get("reference"))
        delta = int(new) - int(old)
        groups[str(right.get("group") or right.get("key") or identity)].append(delta)
        improved += int(delta > 0)
        regressed += int(delta < 0)
        unchanged += int(delta == 0)
    paired = improved + regressed + unchanged
    interval = None
    if len(groups) >= 2:
        rng = random.Random(seed)
        blocks = list(groups.values())
        estimates = []
        for _ in range(1000):
            sample = [value for block in rng.choices(blocks, k=len(blocks)) for value in block]
            estimates.append(statistics.mean(sample))
        estimates.sort()
        interval = [estimates[24], estimates[974]]
    return {
        "paired": paired,
        "unpaired": len(previous.keys() | current.keys()) - paired,
        "groups": len(groups),
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "delta": (improved - regressed) / paired if paired else None,
        "interval_95": interval,
        "method": "paired_group_bootstrap",
        "replicates": 1000,
        "seed": seed,
        "interpretation": "descriptive development evidence; not a repeated-testing guarantee",
    }


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("Non-finite JSON constant")


def _json_document(value):
    return json.loads(
        value, object_pairs_hook=_json_object, parse_float=Decimal, parse_constant=_invalid_constant
    )


def _json_field(document, pointer):
    if pointer == "":
        return document
    for segment in pointer[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(document, dict):
            document = document[key]
        elif isinstance(document, list) and re.fullmatch(r"0|[1-9][0-9]*", key):
            document = document[int(key)]
        else:
            raise KeyError(key)
    return document


def _json_equal(left, right):
    if type(left) in {int, Decimal} and type(right) in {int, Decimal}:
        return left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _json_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _json_equal(a, b) for a, b in zip(left, right, strict=True)
        )
    return left == right


def score_json_fields(prediction, reference, fields):
    try:
        expected = _json_document(reference)
        reference_valid = True
    except (ValueError, RecursionError):
        expected, reference_valid = None, False
    try:
        actual = _json_document(prediction)
        prediction_valid = True
    except (ValueError, RecursionError):
        actual, prediction_valid = None, False
    results = {}
    for pointer in fields:
        try:
            if not reference_valid:
                raise ValueError("Invalid reference")
            target = _json_field(expected, pointer)
        except (KeyError, IndexError, ValueError):
            results[pointer] = {"passed": None, "reason": "reference_missing_or_invalid"}
            continue
        try:
            if not prediction_valid:
                raise ValueError("Invalid prediction")
            passed = _json_equal(_json_field(actual, pointer), target)
            results[pointer] = {"passed": passed, "reason": "equal" if passed else "different"}
        except (KeyError, IndexError, ValueError, RecursionError):
            results[pointer] = {"passed": False, "reason": "prediction_missing_or_invalid"}
    scorable = all(item["passed"] is not None for item in results.values())
    return {
        "scorer": "json_fields:1",
        "scoring_status": "completed" if scorable else "unscorable",
        "passed": all(item["passed"] for item in results.values()) if scorable else None,
        "fields": results,
    }


def contract_metrics(examples, policy):
    completed = [item for item in examples if item.get("status") == "completed"]

    def measured(items, expected):
        scored = [item for item in items if type(item.get("passed")) is bool]
        return {
            "expected": expected,
            "scored": len(scored),
            "technical_errors": expected - len(items),
            "unscorable": len(items) - len(scored),
            "coverage": len(scored) / expected if expected else None,
            "pass_rate": sum(item["passed"] for item in scored) / len(scored) if scored else None,
        }

    result = measured(completed, len(examples))
    if policy["kind"] == "json_fields":
        result["scorer"] = "json_fields:1"
        result["fields"] = {
            pointer: measured(
                [(item.get("fields") or {}).get(pointer, {}) for item in completed], len(examples)
            )
            for pointer in policy["fields"]
        }
    return result
