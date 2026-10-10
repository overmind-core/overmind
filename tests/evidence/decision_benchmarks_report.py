import gzip
import hashlib
import json
import os
import tomllib
from pathlib import Path

import requests

ROOT = Path(__file__).parent
EXPECTED = {"banking77": 3080, "sst5": 2210, "boolq": 3270}
NAMES = {
    "qwen3-base": "Qwen3 0.6B base",
    "candidate-0": "Qwen3 0.6B trained",
    "qwen35-base": "Qwen3.5 0.8B base",
    "candidate-1": "Qwen3.5 0.8B trained",
    "jev": "Jev 1.13",
}


def main():
    evidence = json.loads((ROOT / "decision-benchmarks-results.json").read_text())
    majority = json.loads((ROOT / "decision-benchmarks-majority.json").read_text())
    connection = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "overmind/connection.toml"
    )
    saved = tomllib.loads(connection.read_text())
    base = saved["base-url"].rstrip("/")
    assert base == "http://localhost:8000"
    session = requests.Session()
    session.headers["X-Api-Key"] = saved["api-key"]
    directory = ROOT / "decision-benchmarks-reports"
    directory.mkdir(exist_ok=True)
    results = {}
    lines = [
        "# Decision benchmark results",
        "",
        "Three full training epochs; fixed recipe and last-checkpoint selection. Accuracy and probability metrics use complete official held-out splits. The base is an unchanged foundation with its initialized decision head, not a prompted chat classifier.",
        "",
        "| Benchmark | Participant | Accuracy | Macro F1 | Raw cross entropy | Calibrated cross entropy | Raw Brier | Scored |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for benchmark, expected in EXPECTED.items():
        case = evidence["cases"][benchmark]
        experiment = case["observation"]["progress"]
        assert experiment["state"] == "completed", (benchmark, experiment["state"])
        plan = case["evaluation_observation"]["progress"]
        assert plan["state"] == "completed"
        assert plan["id"] == experiment["evaluation"]
        report_path = plan["report"]["json_path"]
        assert report_path.startswith(f"/api/native-evaluations/{plan['id']}/report/")
        response = session.get(base + report_path, timeout=90, allow_redirects=False)
        response.raise_for_status()
        report_digest = hashlib.sha256(response.content).hexdigest()
        receipt = plan["results"].get("report")
        if receipt:
            assert receipt["sha256"] == report_digest
            assert receipt["bytes"] == len(response.content)
        report = response.json()
        (directory / f"{benchmark}.json.gz").write_bytes(gzip.compress(response.content, mtime=0))
        identities = {participant["key"]: participant for participant in report["participants"]}
        trained = {
            job["requested"]["configuration"]["base_model"]: job for job in experiment["jobs"]
        }
        for candidate, base_key, model in (
            ("candidate-0", "qwen3-base", "Qwen/Qwen3-0.6B"),
            ("candidate-1", "qwen35-base", "Qwen/Qwen3.5-0.8B"),
        ):
            assert identities[candidate]["job"] == trained[model]["job_id"]
            assert identities[base_key]["model"] == model
            assert trained[model]["effective"]["parameters"]["N_EPOCHS"] == "3"
        jev_identity = report["calls"]["final_jev"]["receipt"]["served_model"]
        assert jev_identity == report["calls"]["calibration_jev"]["receipt"]["served_model"]
        participants = {}
        for key, name in NAMES.items():
            comparison = report["comparisons"][key]
            raw = comparison["raw"]["candidate"]["benchmarks"][benchmark]
            calibrated = comparison["calibrated"]["candidate"]["benchmarks"][benchmark]
            for card in (raw, calibrated):
                assert card["expected"] == card["scored"] == expected
                assert not any(
                    card[k]
                    for k in ("missing_predictions", "invalid_predictions", "incompatible_inputs")
                )
            participants[key] = {
                "name": name,
                "raw": raw,
                "calibrated": calibrated,
                "cost": report["costs"][key],
            }
            metrics = raw["metrics"]
            lines.append(
                f"| {benchmark} | {name} | {100 * metrics['accuracy']['mean']:.2f}% | {raw['macro_f1']:.4f} | {metrics['cross_entropy']['mean']:.4f} | {calibrated['metrics']['cross_entropy']['mean']:.4f} | {metrics['brier']['mean']:.4f} | {expected:,} |"
            )
        for job in experiment["jobs"]:
            check = job["artifact"]["reload_verification"]
            assert job["status"] == "succeeded" and check["decisions"] > 0
            assert check["max_absolute_error"] <= check["tolerance"] <= 1e-4
        results[benchmark] = {
            "experiment": experiment["id"],
            "evaluation": plan["id"],
            "participants": participants,
            "training": experiment["jobs"],
            "majority_baseline": majority[benchmark],
            "report_sha256": report_digest,
            "report_bytes": len(response.content),
            "jev_served_model": jev_identity,
        }
    lines += [
        "",
        "The constant-label baseline selects the most frequent training label, then scores the unchanged final split: "
        + "; ".join(f"{name} {100 * item['accuracy']:.2f}%" for name, item in majority.items())
        + ". Label selection and export checksums are retained in decision-benchmarks-majority.json.",
        "",
        "| Benchmark | Trained model | Gain over own base | Difference from Jev |",
        "| --- | --- | ---: | ---: |",
    ]
    for benchmark, result in results.items():
        participants = result["participants"]
        accuracy = {key: p["raw"]["metrics"]["accuracy"]["mean"] for key, p in participants.items()}
        for candidate, foundation in (
            ("candidate-0", "qwen3-base"),
            ("candidate-1", "qwen35-base"),
        ):
            lines.append(
                f"| {benchmark} | {NAMES[candidate]} | {100 * (accuracy[candidate] - accuracy[foundation]):+.2f} pp | {100 * (accuracy[candidate] - accuracy['jev']):+.2f} pp |"
            )
    lines += [
        "",
        "BoolQ final evaluation uses the complete public validation split. Training excludes text/passage groups overlapping reserved splits. All duplicate final observations are retained. Calibration uses its own frozen rows and never changes the training recipe. One seed does not estimate training-run variance; pretraining exposure to these public benchmarks is unknown. Recorded provider usage is not an all-in invoice. Full metrics, confidence intervals, coverage, calibration fits, costs and provider identities are retained in the losslessly compressed JSON reports. SHA-256 values identify the original uncompressed report bytes.",
        "",
        "## Reproduction",
        "",
        "Run from the repository with the local Overmind MCP, CLI account connection, Docker Workshop runtime and the pinned Modal release available. The evidence JSON contains immutable source revisions, checksums, cells, packages, experiments and provider calls.",
        "",
        "```sh",
        ".venv/bin/python tests/evidence/decision_benchmarks_fetch.py /tmp/decision-benchmarks-20261010",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py data",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py verify",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py prepare",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py experiments",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py launch",
        ".venv/bin/python tests/evidence/decision_benchmarks_live.py observe",
        ".venv/bin/python tests/evidence/decision_benchmarks_report.py",
        "```",
        "",
        "The saved request keys recover these runs. Repeating launch does not create independent replications. Inspect readiness, costs and existing state before any fresh experiment.",
    ]
    (ROOT / "decision-benchmarks-summary.json").write_text(json.dumps(results, indent=2) + "\n")
    (ROOT / "decision-benchmarks-results.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:21]))


if __name__ == "__main__":
    main()
