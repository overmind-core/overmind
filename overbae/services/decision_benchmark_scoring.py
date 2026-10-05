import json
import math
import sqlite3
import tempfile
from collections import Counter, defaultdict
from itertools import groupby
from pathlib import Path

import numpy as np

from modal_shared.decision_inference import input_digest
from modal_shared.decision_metrics import score_decision
from modal_shared.training_data import file_digest

METRICS = {
    "accuracy",
    "cross_entropy",
    "brier",
    "top1_reference_mass",
    "mode_agreement",
    "rps",
    "expected_score_mae",
    "expected_score_squared_error",
}


def records(path):
    with Path(path).open() as stream:
        for line in stream:
            yield json.loads(line)


def interval(groups, samples, seed):
    if len(groups) < 2 or samples < 2:
        return None
    values = np.array(list(groups.values()), dtype=float)
    generator = np.random.default_rng(seed)
    estimates = []
    for start in range(0, samples, 32):
        indices = generator.integers(0, len(values), size=(min(32, samples - start), len(values)))
        totals = values[indices].sum(axis=1)
        estimates.extend((totals[:, 0] / totals[:, 1]).tolist())
    return np.quantile(estimates, [0.025, 0.975]).tolist()


def binary_detection(values):
    positives = sum(target for _, target in values)
    negatives = len(values) - positives
    result = {
        "positive_decisions": positives,
        "negative_decisions": negatives,
        "auroc": None,
        "average_precision": None,
        "fpr_at_95_tpr": None,
    }
    if not positives or not negatives:
        return result
    tp = fp = 0
    last_tpr = last_fpr = auc = average_precision = 0.0
    for _, tied in groupby(sorted(values, reverse=True), key=lambda value: value[0]):
        for _, target in tied:
            tp += target
            fp += 1 - target
        tpr, fpr = tp / positives, fp / negatives
        auc += (fpr - last_fpr) * (tpr + last_tpr) / 2
        average_precision += (tpr - last_tpr) * tp / (tp + fp)
        if tpr >= 0.95 and result["fpr_at_95_tpr"] is None:
            result["fpr_at_95_tpr"] = fpr
        last_tpr, last_fpr = tpr, fpr
    return {**result, "auroc": auc, "average_precision": average_precision}


class Accumulator:
    def __init__(self):
        self.groups = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))
        self.confidence = []
        self.labels = None
        self.fixed_labels = True
        self.confusion = Counter()
        self.scored = 0
        self.out_of_scope_label = None
        self.out_of_scope = []

    def add(self, score, group, request, reference, prediction=None):
        self.scored += 1
        for name in METRICS & score.keys():
            value = self.groups[name][group]
            value[0] += score[name]
            value[1] += 1
        if "top1_reference_mass" in score:
            self.confidence.append((score["confidence"], score["top1_reference_mass"]))
        labels = tuple(request["options"])
        if self.labels is None:
            self.labels = labels
        self.fixed_labels = self.fixed_labels and self.labels == labels
        if "accuracy" in score:
            q = reference["probabilities"]
            target = q.index(1)
            self.confusion[target, score["predicted_option"]] += 1
            if self.out_of_scope_label is not None:
                index = labels.index(self.out_of_scope_label)
                self.out_of_scope.append((prediction["probabilities"][index], int(target == index)))

    def report(self, samples, seed):
        metrics = {}
        for name, groups in sorted(self.groups.items()):
            total = math.fsum(value[0] for value in groups.values())
            count = sum(value[1] for value in groups.values())
            metrics[name] = {
                "mean": total / count,
                "decisions": count,
                "groups": len(groups),
                "interval_95": interval(groups, samples, seed),
            }
        result = {"scored": self.scored, "metrics": metrics}
        if self.out_of_scope_label is not None:
            result["out_of_scope_detection"] = binary_detection(self.out_of_scope)
        if self.fixed_labels and self.confusion:
            f1 = []
            for label in range(len(self.labels)):
                tp = self.confusion[label, label]
                target_count = sum(n for (a, _), n in self.confusion.items() if a == label)
                predicted_count = sum(n for (_, b), n in self.confusion.items() if b == label)
                denominator = target_count + predicted_count
                f1.append(2 * tp / denominator if denominator else 0)
            result["macro_f1"] = math.fsum(f1) / len(f1)
        if self.confidence:
            bins = [[] for _ in range(15)]
            for confidence, mass in self.confidence:
                bins[min(14, int(confidence * 15))].append((confidence, mass))
            reliability = []
            for index, values in enumerate(bins):
                if not values:
                    continue
                reliability.append(
                    {
                        "lower": index / 15,
                        "upper": (index + 1) / 15,
                        "decisions": len(values),
                        "confidence": math.fsum(v[0] for v in values) / len(values),
                        "reference_mass": math.fsum(v[1] for v in values) / len(values),
                    }
                )
            result["reliability"] = reliability
            result["expected_top1_ece"] = math.fsum(
                b["decisions"] * abs(b["confidence"] - b["reference_mass"]) for b in reliability
            ) / len(self.confidence)
            ordered = sorted(self.confidence, key=lambda value: value[0], reverse=True)
            result["selective_reference_mass"] = {}
            for coverage in [0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
                count = math.ceil(len(ordered) * coverage)
                result["selective_reference_mass"][str(coverage)] = (
                    math.fsum(v[1] for v in ordered[:count]) / count
                )
        return result


def score_suite(suite, predictions, destination, *, bootstrap_samples=1000, seed=73491):
    suite, predictions, destination = Path(suite), Path(predictions), Path(destination)
    if destination.exists():
        raise ValueError("Choose a new score directory")
    manifest = json.loads((suite / "manifest.json").read_text())
    for name in ("inputs.jsonl", "references.jsonl", "failures.jsonl"):
        if manifest["files"].get(name) != file_digest(suite / name):
            raise ValueError("Sealed benchmark files changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="decision-scores-", dir=destination.parent
    ) as temporary:
        output = Path(temporary) / "result"
        output.mkdir()
        database = sqlite3.connect(Path(temporary) / "index.sqlite")
        database.execute(
            "CREATE TABLE rows(key TEXT PRIMARY KEY, hash TEXT, request TEXT, reference TEXT, benchmark TEXT, seen INTEGER DEFAULT 0)"
        )
        for row in records(suite / "inputs.jsonl"):
            if row["input_sha256"] != input_digest(row["decision"]):
                raise ValueError("Benchmark input identity differs")
            try:
                database.execute(
                    "INSERT INTO rows(key,hash,request) VALUES(?,?,?)",
                    (row["key"], row["input_sha256"], json.dumps(row["decision"])),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("Duplicate benchmark input") from exc
        coverage = defaultdict(Counter)
        for ref in records(suite / "references.jsonl"):
            cursor = database.execute(
                "UPDATE rows SET reference=?,benchmark=? WHERE key=? AND hash=? AND reference IS NULL",
                (json.dumps(ref), ref["benchmark"], ref["key"], ref["input_sha256"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Reference identity or coverage differs")
            coverage[ref["benchmark"]]["expected"] += 1
        if database.execute("SELECT count(*) FROM rows WHERE reference IS NULL").fetchone()[0]:
            raise ValueError("Benchmark inputs are missing references")
        if sum(v["expected"] for v in coverage.values()) != manifest["decisions"]:
            raise ValueError("Benchmark manifest count differs")
        for failure in records(suite / "failures.jsonl"):
            coverage[failure["benchmark"]]["incompatible_inputs"] += failure["decisions"]
            coverage[failure["benchmark"]]["expected"] += failure["decisions"]
        preparation_failures = predictions.with_suffix(".failures.jsonl")
        if not preparation_failures.exists():
            preparation_failures = suite / "preparation-failures.jsonl"
        if preparation_failures.exists():
            for failure in records(preparation_failures):
                found = database.execute(
                    "SELECT benchmark FROM rows WHERE key=? AND hash=? AND seen=0",
                    (failure["key"], failure["input_sha256"]),
                ).fetchone()
                if found is None:
                    raise ValueError("Unknown, duplicate or changed preparation failure")
                database.execute("UPDATE rows SET seen=2 WHERE key=?", (failure["key"],))
                coverage[found[0]]["incompatible_inputs"] += 1
        cards = defaultdict(Accumulator)
        slices = defaultdict(Accumulator)
        model_identity = None
        with (
            (output / "scored.jsonl").open("w") as scored,
            (output / "failures.jsonl").open("w") as failures,
        ):
            for prediction in records(predictions):
                found = database.execute(
                    "SELECT hash, request, reference, seen FROM rows WHERE key=?",
                    (prediction["key"],),
                ).fetchone()
                if found is None or found[3] or found[0] != prediction["input_sha256"]:
                    raise ValueError("Unknown, duplicate or changed prediction input")
                identity = prediction.get("model_identity")
                if (
                    not isinstance(identity, str)
                    or not identity
                    or (model_identity is not None and identity != model_identity)
                ):
                    raise ValueError("Prediction file mixes model identities")
                model_identity = identity
                request, ref = json.loads(found[1]), json.loads(found[2])
                database.execute("UPDATE rows SET seen=1 WHERE key=?", (prediction["key"],))
                benchmark = ref["benchmark"]
                try:
                    if prediction["kind"] != request["kind"]:
                        raise ValueError("Prediction decision kind differs")
                    score = score_decision(request, ref["reference"], prediction)
                except (ValueError, KeyError, TypeError) as exc:
                    coverage[benchmark]["invalid_predictions"] += 1
                    failures.write(
                        json.dumps(
                            {"key": prediction["key"], "benchmark": benchmark, "reason": str(exc)}
                        )
                        + "\n"
                    )
                    continue
                if benchmark == "CLINC150":
                    cards[benchmark].out_of_scope_label = "oos"
                cards[benchmark].add(score, ref["group"], request, ref["reference"], prediction)
                for field in (
                    "language",
                    "subject",
                    "heuristic",
                    "subcase",
                    "family",
                    "representation",
                    "upstream_section",
                ):
                    value = ref["metadata"].get(field)
                    if value:
                        slices[benchmark, field, str(value)].add(
                            score, ref["group"], request, ref["reference"]
                        )
                slices[benchmark, "query", ref["query"]].add(
                    score, ref["group"], request, ref["reference"]
                )
                scored.write(
                    json.dumps(
                        {
                            "key": prediction["key"],
                            "benchmark": benchmark,
                            "group": ref["group"],
                            "input_sha256": prediction["input_sha256"],
                            "metrics": score,
                        }
                    )
                    + "\n"
                )
            for key, benchmark in database.execute("SELECT key,benchmark FROM rows WHERE seen=0"):
                coverage[benchmark]["missing_predictions"] += 1
                failures.write(
                    json.dumps({"key": key, "benchmark": benchmark, "reason": "missing_prediction"})
                    + "\n"
                )
        database.close()
        benchmarks = {}
        for benchmark, counts in sorted(coverage.items()):
            card = cards[benchmark].report(bootstrap_samples, seed)
            benchmarks[benchmark] = {
                **card,
                **{
                    name: counts[name]
                    for name in (
                        "expected",
                        "missing_predictions",
                        "invalid_predictions",
                        "incompatible_inputs",
                    )
                },
                "coverage": card["scored"] / counts["expected"],
            }
        macro = defaultdict(list)
        for card in benchmarks.values():
            for name, metric in card["metrics"].items():
                macro[name].append(metric["mean"])
        result = {
            "model_identity": model_identity,
            "benchmarks": benchmarks,
            "macro_across_benchmarks": {
                name: {"mean": math.fsum(values) / len(values), "benchmarks": len(values)}
                for name, values in macro.items()
            },
            "slices": [
                {"benchmark": b, "field": f, "value": v, **card.report(0, seed)}
                for (b, f, v), card in sorted(slices.items())
            ],
            "bootstrap": {
                "method": "resample groups with replacement within each benchmark",
                "samples": bootstrap_samples,
                "seed": seed,
            },
            "predictions_sha256": file_digest(predictions),
            "suite_manifest_sha256": file_digest(suite / "manifest.json"),
        }
        (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        output.rename(destination)
    return result


def compare_suite(suite, baseline, candidate, destination, *, bootstrap_samples=1000, seed=73491):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Choose a new comparison directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="decision-comparison-", dir=destination.parent
    ) as temporary:
        output = Path(temporary) / "result"
        output.mkdir()
        summaries = {}
        for label, predictions in (("baseline", baseline), ("candidate", candidate)):
            summaries[label] = score_suite(
                suite,
                predictions,
                output / label,
                bootstrap_samples=bootstrap_samples,
                seed=seed,
            )
        if (
            summaries["baseline"]["suite_manifest_sha256"]
            != summaries["candidate"]["suite_manifest_sha256"]
        ):
            raise ValueError("Benchmark suite changed during comparison")
        database = sqlite3.connect(Path(temporary) / "paired.sqlite")
        database.execute("CREATE TABLE baseline(key TEXT PRIMARY KEY, row TEXT)")
        for row in records(output / "baseline/scored.jsonl"):
            database.execute("INSERT INTO baseline VALUES(?,?)", (row["key"], json.dumps(row)))
        groups = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: [0.0, 0])))
        paired = Counter()
        with (output / "paired.jsonl").open("w") as stream:
            for row in records(output / "candidate/scored.jsonl"):
                found = database.execute(
                    "SELECT row FROM baseline WHERE key=?", (row["key"],)
                ).fetchone()
                if found is None:
                    continue
                previous = json.loads(found[0])
                if any(
                    row[field] != previous[field]
                    for field in ("benchmark", "group", "input_sha256")
                ):
                    raise ValueError("Paired prediction identities differ")
                deltas = {
                    metric: row["metrics"][metric] - previous["metrics"][metric]
                    for metric in METRICS & row["metrics"].keys() & previous["metrics"].keys()
                }
                for metric, delta in deltas.items():
                    group = groups[row["benchmark"]][metric][row["group"]]
                    group[0] += delta
                    group[1] += 1
                paired[row["benchmark"]] += 1
                stream.write(
                    json.dumps(
                        {
                            "key": row["key"],
                            "benchmark": row["benchmark"],
                            "group": row["group"],
                            "input_sha256": row["input_sha256"],
                            "candidate_minus_baseline": deltas,
                        }
                    )
                    + "\n"
                )
        database.close()
        benchmarks = {}
        macro = defaultdict(list)
        for benchmark, base_card in summaries["baseline"]["benchmarks"].items():
            candidate_card = summaries["candidate"]["benchmarks"][benchmark]
            metrics = {}
            for metric, values in sorted(groups[benchmark].items()):
                total = math.fsum(value[0] for value in values.values())
                count = sum(value[1] for value in values.values())
                mean = total / count
                metrics[metric] = {
                    "candidate_minus_baseline": mean,
                    "direction": (
                        "higher_is_better"
                        if metric in {"accuracy", "top1_reference_mass", "mode_agreement"}
                        else "lower_is_better"
                    ),
                    "decisions": count,
                    "groups": len(values),
                    "interval_95": interval(values, bootstrap_samples, seed),
                }
                macro[metric].append(mean)
            benchmarks[benchmark] = {
                "expected": base_card["expected"],
                "paired_decisions": paired[benchmark],
                "baseline_only": base_card["scored"] - paired[benchmark],
                "candidate_only": candidate_card["scored"] - paired[benchmark],
                "metrics": metrics,
            }
        result = {
            **summaries,
            "benchmarks": benchmarks,
            "macro_across_benchmarks": {
                name: {
                    "candidate_minus_baseline": math.fsum(values) / len(values),
                    "benchmarks": len(values),
                }
                for name, values in sorted(macro.items())
            },
            "bootstrap": {
                "method": "paired differences; resample shared groups within each benchmark",
                "samples": bootstrap_samples,
                "seed": seed,
            },
            "paired_sha256": file_digest(output / "paired.jsonl"),
        }
        (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
        output.rename(destination)
    return result
