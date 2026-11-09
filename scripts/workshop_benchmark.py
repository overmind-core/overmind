import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import requests
from overmind.transfer_connection import check_connection, resolve_transfer_connection

ROOT = Path("output/workshop-benchmark")
PROJECT = "1e3f3e92-b50d-4590-85ed-97921d132d3c"
API = "http://localhost:8000"
CLI = str(Path(".venv/bin/overmind").resolve())


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


class Platform:
    def __init__(self):
        key, endpoint = resolve_transfer_connection("", API, None)
        assert endpoint == API
        report = check_connection(api_key=key, api_url=endpoint, project_id=PROJECT)
        if not report["ready"]:
            raise RuntimeError(report)
        self.http = requests.Session()
        self.http.headers.update(
            {
                "X-Api-Key": key,
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            }
        )
        self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "workshop-benchmark", "version": "1"},
            },
        )
        catalog = self.rpc("tools/list", {})
        self.names = {t["name"] for t in catalog["tools"]}
        projects = self.call("list_projects", {})
        assert projects["connection"]["mcp_url"] == API + "/api/mcp/"
        assert any(p["id"] == PROJECT for p in projects["projects"])

    def rpc(self, method, params):
        response = self.http.post(
            API + "/api/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=90,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("error"):
            raise RuntimeError(payload["error"])
        return payload["result"]

    def call(self, name, arguments):
        assert name in self.names
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        data = result.get("structuredContent") or json.loads(result["content"][0]["text"])
        if result.get("isError"):
            raise RuntimeError(data)
        return data


def cli(args, log):
    run = subprocess.run(
        [CLI, *args, "--api-url", API, "--json"],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path("overmind").resolve())},
    )
    log.write_text(run.stdout + run.stderr)
    if run.returncode:
        raise RuntimeError(f"CLI failed; retained log: {log}")
    return json.loads(run.stdout)


def initialise(platform, case):
    directory = ROOT / case["key"]
    directory.mkdir(exist_ok=True)
    path = directory / "state.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    if not state.get("dataset"):
        result = platform.call(
            "start_dataset",
            {
                "project_id": PROJECT,
                "name": "Benchmark · " + case["name"],
                "brief": case["name"]
                + ". Source: "
                + case["source_url"]
                + ". Licence: "
                + case["licence"]
                + ". "
                + case["selection"],
                "intent": case["intent"],
                "request_key": "benchmark-ten:" + case["key"],
            },
        )
        state = {"dataset": result["dataset"]["id"], "case": case, "attempts": []}
        save(path, state)
    if not state.get("uploaded"):
        cli(
            [
                "dataset",
                "upload",
                *case["files"],
                "--project-id",
                PROJECT,
                "--dataset",
                state["dataset"],
                "--request-key",
                "benchmark-ten-source:" + case["key"],
                "--wait",
            ],
            directory / "upload.log",
        )
        state["uploaded"] = True
        save(path, state)
    return directory, path, state


def collect(platform, directory, state, attempt, result):
    save(directory / f"attempt-{attempt['number']}-task.json", result)
    publications = [
        a["result"]
        for a in result["actions"]
        if a["name"] == "publish_workshop" and a["state"] == "completed"
    ]
    if not publications:
        raise RuntimeError("Completed task has no publication")
    publication = publications[0]
    attempt["publication"] = publication
    workbench = platform.call(
        "inspect_dataset_workbench", {"project_id": PROJECT, "pipeline": publication["pipeline"]}
    )
    save(directory / f"attempt-{attempt['number']}-pipeline.json", workbench)
    stem = f"attempt-{attempt['number']}"
    files = [(state["dataset"], publication["cell"], "output")]
    for member in publication.get("partition", {}).get("members", []):
        files.append((member["cell"]["dataset_id"], member["cell"]["id"], member["role"]))
    for dataset, cell, kind in files:
        target = directory / f"{stem}-{kind}.jsonl"
        if not target.exists():
            cli(
                ["dataset", "export", dataset, "--cell", cell, "--output", str(target)],
                directory / f"{stem}-{kind}-export.log",
            )
    pipeline = workbench["pipeline"]
    target = directory / f"{stem}-recipe.zip"
    if not target.exists():
        cli(
            [
                "dataset",
                "pipeline-download",
                pipeline["package"],
                "--project-id",
                PROJECT,
                "--output",
                str(target),
            ],
            directory / f"{stem}-recipe.log",
        )
    attempt["collected"] = True


def run(platform, case, retry=False):
    directory, path, state = initialise(platform, case)
    if retry or not state["attempts"]:
        number = len(state["attempts"]) + 1
        if retry:
            source = None
            offset = 0
            while source is None:
                snapshot = platform.call(
                    "inspect_dataset",
                    {
                        "project_id": PROJECT,
                        "dataset": state["dataset"],
                        "cell_limit": 20,
                        "cell_offset": offset,
                    },
                )
                source = next(
                    (
                        cell["id"]
                        for cell in snapshot["cells"]
                        if cell["transformation"]["execution"] == "source"
                    ),
                    None,
                )
                if not snapshot["cell_page"]["has_more"]:
                    break
                offset = int(snapshot["cell_page"]["next_cursor"])
            if source is None:
                raise RuntimeError("Original source cell is missing; cannot retry faithfully")
            platform.call(
                "update_dataset",
                {
                    "project_id": PROJECT,
                    "dataset": state["dataset"],
                    "active": source,
                    "request_key": f"benchmark-ten:{case['key']}:reset:{number}",
                },
            )
        task = platform.call(
            "start_workshop_task",
            {
                "project_id": PROJECT,
                "dataset": state["dataset"],
                "objective": case["objective"],
                "max_steps": 0,
                "request_key": f"benchmark-ten:{case['key']}:attempt:{number}",
            },
        )["task"]
        state["attempts"].append({"number": number, "task": task["id"]})
        save(path, state)
    attempt = state["attempts"][-1]
    if attempt.get("collected"):
        return
    last = None
    while True:
        try:
            result = platform.call(
                "inspect_workshop_task", {"project_id": PROJECT, "task": attempt["task"]}
            )
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            if isinstance(exc, requests.HTTPError) and exc.response.status_code < 500:
                raise
            print(
                json.dumps({"case": case["key"], "observation_error": type(exc).__name__}),
                flush=True,
            )
            time.sleep(10)
            continue
        task = result["task"]
        attempt["task_state"] = task["state"]
        attempt["used_tokens"] = task.get("used_tokens")
        save(path, state)
        compact = {k: task[k] for k in ("id", "state", "step", "used_tokens")}
        compact["case"] = case["key"]
        if compact != last:
            print(json.dumps(compact), flush=True)
            last = compact
        if task["state"] == "completed":
            collect(platform, directory, state, attempt, result)
            save(path, state)
            return
        if task["state"] in {"blocked", "failed", "cancelled"}:
            save(directory / f"attempt-{attempt['number']}-task.json", result)
            raise RuntimeError(
                f"{case['key']}: {task['state']}; inspect retained task before retry"
            )
        time.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="*")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    platform = Platform()
    cases = json.loads((ROOT / "cases.json").read_text())
    for case in cases:
        if not args.cases or case["key"] in args.cases:
            run(platform, case, args.retry)
