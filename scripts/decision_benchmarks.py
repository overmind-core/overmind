"""Convert the pinned Jev research exports into separate native inputs and references."""

import argparse
import hashlib
import json
import math
import tempfile
from collections import Counter
from pathlib import Path

from modal_shared.decision_metrics import score_decision
from modal_shared.decisions import decision_request
from modal_shared.training_data import file_digest


class DuplicateOptionsError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def convert_row(row):
    users = [m for m in row["input"]["messages"] if m["role"] == "user"]
    if len(users) != 1:
        raise ValueError("Research inputs require exactly one evidence payload")
    payload = json.loads(users[0]["content"])
    state = payload["state"]
    if not isinstance(state, str):
        state = canonical(state)
    if "queries" in payload:
        if payload["queries"] != row["decision_queries"]:
            raise ValueError("Published queries and metadata disagree")
        queries = payload["queries"]
        if len({q["id"] for q in queries}) != len(queries):
            raise ValueError("Query identities must be unique")
    else:
        options = payload["options"]
        if options != [
            {"id": str(i), "label": label} for i, label in enumerate(row["decision_options"])
        ]:
            raise ValueError("Published option order and metadata disagree")
        queries = [
            {
                "id": "decision",
                "question": payload["question"],
                "kind": "noul" if row["decision_kind"] == "bool" else row["decision_kind"],
                "options": row["decision_options"],
            }
        ]

    for query in queries:
        if len(set(query["options"])) != len(query["options"]):
            raise DuplicateOptionsError("duplicate_option_labels")
        question = query.get("question")
        proposition = query.get("proposition")
        if proposition:
            question = (question or "Is the following proposition true?") + "\n" + proposition
        request = decision_request(
            {
                "state": state,
                "question": question,
                "kind": query["kind"],
                "options": query["options"],
            }
        )
        count = len(request["options"])
        if "queries" in payload:
            target = row["reference_targets"][query["id"]]
            if "distribution" in target:
                reference = {"probabilities": target["distribution"]}
            else:
                reference = {
                    "mean": target["expected_normalized_value"],
                    "values": query["normalized_level_values"],
                }
        else:
            label = row["expected_output"]["content"]
            if label not in [str(i) for i in range(count)]:
                raise ValueError("Hard references require a valid decimal option index")
            reference = {"probabilities": [int(i == int(label)) for i in range(count)]}
        # The metric validator rejects ambiguous, unnormalized, and out-of-range references.
        score_decision(
            request,
            reference,
            {
                "probabilities": [1 / count] * count,
                "log_probabilities": [-math.log(count)] * count,
            },
        )
        key = canonical([row["benchmark_id"], query["id"]])
        digest = hashlib.sha256(canonical(request).encode()).hexdigest()
        metadata = {
            k: row[k]
            for k in (
                "language",
                "upstream_section",
                "upstream_split",
                "upstream_repo",
                "upstream_revision",
                "upstream_file_sha256",
                "upstream_row",
                "reference_kind",
                "heuristic",
                "subcase",
                "subject",
                "family",
                "representation",
            )
            if k in row
        }
        yield (
            {"key": key, "decision": request, "input_sha256": digest},
            {
                "key": key,
                "input_sha256": digest,
                "benchmark": row["benchmark"],
                "group": row["benchmark_group"],
                "query": query["id"],
                "kind": query["kind"],
                "reference": reference,
                "metadata": metadata,
            },
        )


def prepare(inputs, destination, *, calibration=False):
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Choose a new output directory; sealed suites are never overwritten")
    destination.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    counts = Counter()
    source_rows = excluded_rows = 0
    incompatible_decisions = 0
    sources = [{"path": str(path.resolve()), "sha256": file_digest(path)} for path in inputs]
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="decision-suite-") as temp:
        root = Path(temp) / "suite"
        root.mkdir()
        with (
            (root / "inputs.jsonl").open("w") as requests,
            (root / "references.jsonl").open("w") as refs,
            (root / "failures.jsonl").open("w") as failures,
        ):
            for path in inputs:
                with path.open() as stream:
                    for line in stream:
                        row = json.loads(line)
                        source_rows += 1
                        if calibration:
                            if type(row.get("calibration_eligible")) is not bool:
                                raise ValueError("Calibration eligibility must be explicit")
                            if not row["calibration_eligible"]:
                                excluded_rows += 1
                                continue
                        try:
                            converted = list(convert_row(row))
                        except DuplicateOptionsError:
                            affected = len(row.get("decision_queries") or [None])
                            incompatible_decisions += affected
                            failures.write(
                                canonical(
                                    {
                                        "benchmark_id": row["benchmark_id"],
                                        "benchmark": row["benchmark"],
                                        "group": row["benchmark_group"],
                                        "reason": "duplicate_option_labels",
                                        "decisions": affected,
                                    }
                                )
                                + "\n"
                            )
                            continue
                        for request, reference in converted:
                            if request["key"] in seen:
                                raise ValueError("A benchmark query appears more than once")
                            seen.add(request["key"])
                            counts[reference["benchmark"]] += 1
                            requests.write(canonical(request) + "\n")
                            refs.write(canonical(reference) + "\n")
        manifest = {
            "role": "calibration" if calibration else "final",
            "sources": sources,
            "source_rows": source_rows,
            "excluded_rows": excluded_rows,
            "incompatible_decisions": incompatible_decisions,
            "decisions": sum(counts.values()),
            "by_benchmark": dict(counts),
            "files": {
                name: file_digest(root / name)
                for name in ("inputs.jsonl", "references.jsonl", "failures.jsonl")
            },
            "converter_sha256": file_digest(Path(__file__)),
        }
        (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        root.rename(destination)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--calibration", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.input, args.output_dir, calibration=args.calibration), indent=2))
