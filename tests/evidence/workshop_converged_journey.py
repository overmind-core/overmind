import argparse
import asyncio
import collections
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
from workshop_real_journeys import FINANCE, package, rows

DATASET = "dece0a89-ab26-4293-aa3a-b292d3a278c9"
SOURCE = "c13a1cd1-b095-4c7d-b1fd-c4d298a748ac"
FINGERPRINT = "46fdca74f75a2eb35c1f1b49cafe84e250249d265e2a91b502b5b6c510e266ab"
FAMILY = "ece8f610-dff8-4c46-9f5f-a359a9d5b23a"
ORIGINAL_OUTPUT = "e53ff724-3134-4cfe-8864-52472c4578bc"
CLI = "/Users/tyleredwards/.local/bin/overmind"


async def main(options):
    report_path = options.report
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "overmind/connection.toml"
    )
    with config.open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    work = Path(tempfile.mkdtemp(prefix="workshop-converged-"))
    report = {
        "success": False,
        "dataset": DATASET,
        "source": SOURCE,
        "runs": [],
        "artifacts": str(work),
    }
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }

    async def cli(*argv):
        result = await asyncio.to_thread(
            subprocess.run,
            [CLI, *map(str, argv)],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            initialized = await session.initialize()
            report["server_version"] = initialized.serverInfo.version

            async def call(tool_name, **arguments):
                result = await session.call_tool(tool_name, {"project_id": FINANCE, **arguments})
                assert not result.isError, result.structuredContent
                return result.structuredContent

            projects = await session.call_tool("list_projects", {})
            assert any(item["id"] == FINANCE for item in projects.structuredContent["projects"])
            bench = await call("inspect_dataset_workbench", dataset=DATASET)
            runtime = bench["runner"]["images"][0]
            location = work / "package"
            steps = package("pairs", runtime, location)
            steps = steps[:-1]
            for step in steps[2:]:
                step["condition"]["expression"] = (
                    "Review flagged" if step["id"] == "review" else "No measured flags"
                )
            steps.append(
                {
                    "id": "training",
                    "inputs": ["review", "unflagged"],
                    "name": "Final training data · entity matching",
                    "entrypoint": "messages.py",
                    "input_schema": {"source_row": "integer", "features": "object"},
                    "output_schema": {"source_row": "integer", "messages": "array"},
                    "checks": {"preserve_rows": True, "min_rows": 10000, "max_rows": 10000},
                }
            )
            manifest = json.loads((location / "manifest.json").read_text())
            manifest["steps"] = steps
            (location / "manifest.json").write_text(json.dumps(manifest))
            if options.pipeline:
                saved = await call("inspect_dataset_workbench", pipeline=options.pipeline)
            else:
                uploaded = await cli(
                    "dataset", "pipeline-upload", location, "--project-id", FINANCE, "--json"
                )
                saved = await call(
                    "save_dataset_pipeline",
                    name="Entity matching: branches to final training data",
                    pipeline=FAMILY,
                    expected_revision=5,
                    request_key="kyc-visible-scripts-revision-6",
                    package=uploaded["id"],
                )
            recipe = saved["pipeline"]
            assert recipe["flow"]["terminal_steps"] == ["training"]
            assert not recipe["flow"]["unconsumed_steps"]
            report["pipeline"] = recipe["id"]
            validation = await call(
                "validate_dataset_pipeline",
                pipeline=recipe["id"],
                source_cell=SOURCE,
                source_fingerprint=FINGERPRINT,
            )
            assert validation["validation"]["valid"] and not validation["validation"]["warnings"]
            prior = work / "prior.jsonl"
            await cli(
                "dataset", "export", DATASET, "--cell", ORIGINAL_OUTPUT, "--output", prior, "--json"
            )
            expected = [
                {key: value for key, value in row.items() if not key.startswith("_overmind_")}
                for row in rows(prior)
            ]
            for index, mode in enumerate(["preview", *(["publish"] * options.repeats)]):
                start = time.monotonic()
                args = dict(
                    dataset=DATASET,
                    pipeline=recipe["id"],
                    source_cell=SOURCE,
                    source_fingerprint=FINGERPRINT,
                    request_key=f"{options.run}-{index}",
                    mode=mode,
                    preview_rows=1000,
                )
                launched = await call("run_dataset_pipeline", **args)
                run_id = launched["run"]["id"]
                for _ in range(120):
                    job = await call("get_job", kind="dataset_pipeline", id=run_id)
                    if job["status"] not in {"queued", "running"}:
                        break
                    await asyncio.sleep(5)
                assert job["status"] == "completed", job
                receipt = job["details"]
                assert receipt["completed_at"] and receipt["poll_after_seconds"] is None
                branch_steps = receipt["result"]["steps"]
                assert len(branch_steps) == 5
                assert (
                    sum(step["output_rows"] for step in branch_steps[2:4])
                    == branch_steps[-1]["output_rows"]
                )
                assert branch_steps[-1]["input_steps"] == ["review", "unflagged"]
                replay = await call("run_dataset_pipeline", **args)
                assert replay["run"]["id"] == run_id
                observed = {
                    "mode": mode,
                    "run": run_id,
                    "seconds": round(time.monotonic() - start, 3),
                    "rows": branch_steps[-1]["output_rows"],
                    "branch_rows": [step["output_rows"] for step in branch_steps[2:4]],
                    "stage_seconds": receipt["result"]["stage_seconds"],
                }
                if mode == "publish":
                    output = work / f"output-{index}.jsonl"
                    await cli(
                        "dataset",
                        "export",
                        DATASET,
                        "--cell",
                        receipt["output_cell"],
                        "--output",
                        output,
                        "--json",
                    )
                    actual = rows(output)
                    assert [
                        {
                            key: value
                            for key, value in row.items()
                            if not key.startswith("_overmind_")
                        }
                        for row in actual
                    ] == expected
                    assert collections.Counter(row["judgement"] for row in actual) == {
                        "positive": 7690,
                        "negative": 2310,
                    }
                    assert sum(row["needs_review"] for row in actual) == 119
                    parents = {step["step_id"]: step for step in branch_steps[2:4]}
                    for row in actual:
                        parent = parents["review" if row["needs_review"] else "unflagged"]
                        assert row["_overmind_provenance"]["parents"] == [
                            {
                                "cell": parent["output_cell"],
                                "fingerprint": parent["output_fingerprint"],
                                "row": row["source_row"],
                            }
                        ]
                    assert branch_steps[-1]["input_cells"] == [
                        step["output_cell"] for step in branch_steps[2:4]
                    ]
                    observed["output_cell"] = receipt["output_cell"]
                report["runs"].append(observed)
                report_path.write_text(json.dumps(report, indent=2) + "\n")
                print(json.dumps(observed), flush=True)
            report["success"] = True
    finally:
        report_path.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, choices=range(1, 4), default=3)
    parser.add_argument(
        "--pipeline", help="Reuse a saved revision; omit only to publish visible-script revision 6"
    )
    parser.add_argument(
        "--run",
        default="kyc-visible-scripts-6",
        help="Stable request-key prefix; unchanged replays recover receipts",
    )
    parser.add_argument(
        "--report", type=Path, default=Path(__file__).with_name("workshop-converged-results.json")
    )
    asyncio.run(main(parser.parse_args()))
