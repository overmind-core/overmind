import argparse
import hashlib
import json
import os
import tomllib
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads(args.receipt.read_text())
    observed = receipt["outcome"]
    assert observed["status"] == "completed"
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    connection = tomllib.loads((config / "overmind/connection.toml").read_text())
    base = connection["base-url"].rstrip("/")
    assert base == "http://localhost:8000"
    report_path = observed["progress"]["report"]["json_path"]
    assert report_path == f"/api/native-evaluations/{observed['id']}/report/?format=json"
    response = requests.get(
        base + report_path,
        headers={"X-Api-Key": connection["api-key"]},
        timeout=120,
        allow_redirects=False,
    )
    assert response.status_code == 200, response.status_code
    digest = hashlib.sha256(response.content).hexdigest()
    expected = observed["progress"]["results"]["report"]
    assert digest == expected["sha256"]
    assert len(response.content) == expected["bytes"]
    report = response.json()
    results = {}
    for key in ("base", "candidate"):
        results[key] = {}
        for variant in ("raw", "calibrated"):
            downloaded = report["comparisons"][key][variant]["candidate"]
            summary = observed["progress"]["results"]["comparisons"][key][variant]["candidate"]
            for field in ("model_identity", "macro_across_benchmarks"):
                assert downloaded[field] == summary[field], (key, variant, field)
            counts = {
                field: sum(benchmark[field] for benchmark in downloaded["benchmarks"].values())
                for field in (
                    "expected",
                    "scored",
                    "missing_predictions",
                    "invalid_predictions",
                    "incompatible_inputs",
                )
            }
            counts["fraction"] = counts["scored"] / counts["expected"]
            assert counts == summary["coverage"]
            assert summary["coverage"] == {
                "expected": 89,
                "scored": 89,
                "missing_predictions": 0,
                "invalid_predictions": 0,
                "incompatible_inputs": 0,
                "fraction": 1,
            }
            results[key][variant] = summary["macro_across_benchmarks"]
    result = {
        "evaluation": observed["id"],
        "status": "passed",
        "report_sha256": digest,
        "report_bytes": len(response.content),
        "participants": results,
        "scope": "Download bytes and metrics agree with the retained MCP completion receipt",
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
