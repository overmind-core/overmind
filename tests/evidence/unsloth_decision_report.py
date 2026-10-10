import argparse
import json
import os
import tomllib
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", action="append", default=[])
    parser.add_argument("--evaluation", action="append", default=[])
    args = parser.parse_args()
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "overmind/connection.toml"
    )
    saved = tomllib.loads(config.read_text())
    base = saved["base-url"].rstrip("/")
    assert base == "http://localhost:8000"
    session = requests.Session()
    session.headers["X-Api-Key"] = saved["api-key"]

    def get(path):
        response = session.get(base + path, timeout=60, allow_redirects=False)
        response.raise_for_status()
        return response.json()

    output = Path(__file__).with_name("unsloth-decision-reports")
    output.mkdir(exist_ok=True)
    for job in args.job:
        data = get(f"/api/finetuning-jobs/{job}/")
        assert data["project"] == "1e3f3e92-b50d-4590-85ed-97921d132d3c"
        record = data["record"]
        monitoring = get(f"/api/finetuning-jobs/{job}/monitoring/?limit=100")
        path = output / f"training-{job}.json"
        path.write_text(
            json.dumps({"record": record, "monitoring": monitoring}, separators=(",", ":")) + "\n"
        )
        print(json.dumps({"job": job, "state": data["status"], "path": str(path)}))
    for evaluation in args.evaluation:
        data = get(f"/api/native-evaluations/{evaluation}/")
        assert data["project"] == "1e3f3e92-b50d-4590-85ed-97921d132d3c"
        assert data["state"] == "completed", data["state"]
        report = get(f"/api/native-evaluations/{evaluation}/report/?format=json")
        path = output / f"evaluation-{evaluation}.json"
        path.write_text(json.dumps({"plan": data, "report": report}, separators=(",", ":")) + "\n")
        print(json.dumps({"evaluation": evaluation, "state": data["state"], "path": str(path)}))


if __name__ == "__main__":
    main()
