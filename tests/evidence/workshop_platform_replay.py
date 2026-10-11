import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"


def user_values(path):
    with path.open() as stream:
        return [
            {
                key: value
                for key, value in json.loads(line).items()
                if not key.startswith("_overmind_")
            }
            for line in stream
        ]


async def main(options):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    with (config / "overmind/connection.toml").open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    fixture = json.loads(options.fixture.read_text())
    work = Path(tempfile.mkdtemp(prefix="workshop-platform-replay-"))
    report = {"success": False, "endpoint": base, "artifacts": str(work), "cases": []}
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }

    async def export(dataset, cell):
        path = Path(tempfile.mkdtemp(prefix=f"{cell}-", dir=work)) / "rows.jsonl"
        completed = await asyncio.to_thread(
            subprocess.run,
            [
                options.cli,
                "dataset",
                "export",
                dataset,
                "--cell",
                cell,
                "--output",
                str(path),
                "--json",
            ],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stdout
        return json.loads(completed.stdout), user_values(path)

    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            projects = await session.call_tool("list_projects", {})
            assert not projects.isError
            assert PROJECT in {item["id"] for item in projects.structuredContent["projects"]}
            report["catalog_sha256"] = projects.structuredContent["catalog_sha256"]

            async def call(tool_name, **arguments):
                result = await session.call_tool(tool_name, {"project_id": PROJECT, **arguments})
                assert not result.isError, result.structuredContent
                return result.structuredContent

            async def cells(dataset):
                found, offset = [], 0
                while True:
                    detail = await call(
                        "inspect_dataset", dataset=dataset, cell_offset=offset, cell_limit=5
                    )
                    found.extend(detail["cells"])
                    cursor = detail["cell_page"]["next_cursor"]
                    if cursor is None:
                        return detail, found
                    offset = int(cursor)

            for case in fixture["cases"]:
                if options.only and case["kind"] not in options.only:
                    continue
                prior = case["runs"][0]
                original_dataset = prior["dataset"]
                original, original_cells = await cells(original_dataset)
                baseline = await call("get_job", kind="dataset_pipeline", id=prior["publish"]["id"])
                assert baseline["status"] == "completed"
                source = baseline["details"]
                bench = await call("inspect_dataset_workbench", pipeline=case["recipe"], limit=1)
                recipe = bench["pipeline"]
                before_source, _ = await export(original_dataset, source["source_cell"])
                expected = {}
                for step in baseline["progress"]["steps"]:
                    _, expected[step["step_id"]] = await export(
                        original_dataset, step["output_cell"]
                    )
                draft = await call(
                    "start_dataset",
                    name=f"Platform replay {options.run} {case['kind']}",
                    brief="Regression of unchanged retained scripts: verify execution, schema and value preservation. Not a semantic quality review. No training.",
                    intent="explore",
                )
                destination = draft["dataset"]["id"]
                observed = {
                    "kind": case["kind"],
                    "original_dataset": original_dataset,
                    "dataset": destination,
                    "recipe": recipe["id"],
                    "recipe_fingerprint": recipe["fingerprint"],
                    "source_cell": source["source_cell"],
                    "source_fingerprint": source["source_fingerprint"],
                    "runs": [],
                }
                report["cases"].append(observed)
                options.report.write_text(json.dumps(report, indent=2) + "\n")
                for index, mode in enumerate(["preview", *(["publish"] * options.repeats)]):
                    arguments = {
                        "dataset": destination,
                        "pipeline": recipe["id"],
                        "source_cell": source["source_cell"],
                        "source_fingerprint": source["source_fingerprint"],
                        "request_key": f"{options.run}-{case['kind']}-{index}",
                        "mode": mode,
                        "preview_rows": 3,
                    }
                    tick = time.monotonic()
                    run = (await call("run_dataset_pipeline", **arguments))["run"]
                    recovered = (await call("run_dataset_pipeline", **arguments))["run"]
                    assert recovered["id"] == run["id"]
                    while time.monotonic() - tick < 300:
                        job = await call("get_job", kind="dataset_pipeline", id=run["id"])
                        if job["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(job["details"].get("poll_after_seconds") or 5)
                    assert job["status"] == "completed", job
                    _, published = await cells(destination)
                    if mode == "preview":
                        assert not published
                    else:
                        by_id = {cell["id"]: cell for cell in published}
                        for step in job["progress"]["steps"]:
                            cell = by_id[step["output_cell"]]
                            assert cell["transformation"]["execution"] == "isolated_container"
                            assert cell["transformation"]["pipeline"] == recipe["id"]
                            previous_step = next(
                                s
                                for s in baseline["progress"]["steps"]
                                if s["step_id"] == step["step_id"]
                            )
                            previous_cell = next(
                                c for c in original_cells if c["id"] == previous_step["output_cell"]
                            )
                            assert cell["script"] == previous_cell["script"]
                            names = [column["name"] for column in cell["columns"]]
                            assert len(names) == len(set(names)), names
                            result = await call(
                                "query_dataset",
                                dataset=destination,
                                cell=cell["id"],
                                sql="SELECT * FROM t WHERE false",
                            )
                            assert result["columns"] == names
                            assert "source_row_1" not in result["columns"]
                            _, actual = await export(destination, cell["id"])
                            assert actual == expected[step["step_id"]], step["step_id"]
                    measurement = {
                        "id": run["id"],
                        "mode": mode,
                        "seconds": round(time.monotonic() - tick, 3),
                        "steps": [
                            {
                                key: step.get(key)
                                for key in ("step_id", "input_rows", "output_rows", "output_cell")
                            }
                            for step in job["progress"]["steps"]
                        ],
                    }
                    observed["runs"].append(measurement)
                    options.report.write_text(json.dumps(report, indent=2) + "\n")
                    print(json.dumps({"kind": case["kind"], **measurement}), flush=True)
                final, final_cells = await cells(original_dataset)
                assert final["active"]["id"] == original["active"]["id"]
                assert [c["id"] for c in final_cells] == [c["id"] for c in original_cells]
                after_source, _ = await export(original_dataset, source["source_cell"])
                assert (
                    before_source["fingerprint"]
                    == after_source["fingerprint"]
                    == source["source_fingerprint"]
                )
                after_recipe = (
                    await call("inspect_dataset_workbench", pipeline=recipe["id"], limit=1)
                )["pipeline"]
                assert after_recipe["fingerprint"] == recipe["fingerprint"]
                observed["original_unchanged"] = True
                observed["output_user_values_sha256"] = {
                    step: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
                    for step, value in expected.items()
                }
            report["success"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)[:4000]}
        raise
    finally:
        options.report.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path(__file__).with_name("workshop-real-visible-scripts.json"),
    )
    parser.add_argument("--run", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--repeats", type=int, default=2)
    asyncio.run(main(parser.parse_args()))
