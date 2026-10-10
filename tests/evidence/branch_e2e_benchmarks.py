import argparse
import hashlib
import json
import os
import tomllib
from pathlib import Path

import requests

ROOT = Path(__file__).parent
PROJECT = "1e3f3e92-b50d-4590-85ed-97921d132d3c"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "branch-e2e-benchmarks-observed.json")
    args = parser.parse_args()
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    saved = tomllib.loads((config / "overmind/connection.toml").read_text())
    base = saved["base-url"].rstrip("/")
    assert base == "http://localhost:8000"
    session = requests.Session()
    session.headers["X-Api-Key"] = saved["api-key"]

    def call(name, **arguments):
        response = session.post(
            base + "/api/mcp/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            timeout=120,
            allow_redirects=False,
        )
        assert response.status_code == 200, response.status_code
        body = response.json()
        assert "error" not in body, body
        result = body["result"]
        assert not result.get("isError"), result
        return result["structuredContent"]

    catalogue = call("list_projects")
    assert catalogue["connection"]["mcp_url"] == base + "/api/mcp/"
    summary = json.loads((ROOT / "decision-benchmarks-summary.json").read_text())
    archived = {
        row["benchmark"]: row
        for row in json.loads((ROOT / "branch-e2e-benchmarks-observed.json").read_text())
    }
    receipts = []
    for name, benchmark in summary.items():
        evaluation = call(
            "get_job", project_id=PROJECT, kind="native_evaluation", id=benchmark["evaluation"]
        )
        experiment = call(
            "get_job", project_id=PROJECT, kind="training_experiment", id=benchmark["experiment"]
        )
        assert evaluation["status"] == experiment["status"] == "completed"
        report_path = evaluation["progress"]["report"]["json_path"]
        assert report_path.startswith("/api/native-evaluations/")
        response = session.get(base + report_path, timeout=120, allow_redirects=False)
        assert response.status_code == 200, response.status_code
        report = response.json()
        archived_sha256 = archived[name]["archived_report_sha256"]
        live_sha256 = hashlib.sha256(response.content).hexdigest()
        assert live_sha256 == archived_sha256, name
        participants = {}
        for key, participant in benchmark["participants"].items():
            for variant in ("raw", "calibrated"):
                assert (
                    report["comparisons"][key][variant]["candidate"]["benchmarks"][name]
                    == participant[variant]
                )
            participants[key] = {
                "name": participant["name"],
                "scored": participant["raw"]["scored"],
                "coverage": participant["raw"]["coverage"],
                "accuracy": participant["raw"]["metrics"]["accuracy"]["mean"],
            }
        receipt = {
            "benchmark": name,
            "experiment": benchmark["experiment"],
            "evaluation": benchmark["evaluation"],
            "status": "passed",
            "archived_report_sha256": archived_sha256,
            "live_report_sha256": live_sha256,
            "report_bytes": len(response.content),
            "participants": participants,
        }
        receipts.append(receipt)
        print(json.dumps(receipt), flush=True)
    args.output.write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    main()
