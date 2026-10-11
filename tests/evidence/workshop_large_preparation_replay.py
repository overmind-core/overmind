"""Run a retained identity transformation over an existing local source through MCP."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection


async def verify(call, record):
    job = await call("get_job", kind="dataset_pipeline", id=record["job"]["id"])
    assert job["status"] == "completed", job["status"]
    dataset = await call("inspect_dataset", dataset=record["dataset"])
    workbench = await call("inspect_dataset_workbench", dataset=record["dataset"], limit=1)
    status = (await call("inspect_dataset_preparation", dataset=record["dataset"]))[
        "preparation_status"
    ]
    active = dataset["active"]
    receipt = job["details"]["result"]["steps"][0]
    assert active["rows"] == record["source"]["rows"]
    assert active["id"] == receipt["output_cell"]
    assert active["fingerprint"] == receipt["output_fingerprint"]
    assert workbench["current_pipeline"] == record["recipe"]["id"]
    assert {node["cell"] for node in workbench["preparation"]["nodes"]} == {
        record["source"]["id"],
        active["id"],
    }
    assert status["counts"]["failed"] == 0
    assert any(
        finding["category"] == "task_suitability" and finding["status"] == "not_assessed"
        for finding in status["findings"]
    )
    record["result"] = job
    record["verification"] = {
        "active": active,
        "current_pipeline": workbench["current_pipeline"],
        "preparation": workbench["preparation"],
        "preparation_status": status,
        "output_fingerprint_matches_receipt": True,
    }


async def main(args):
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000"
    record = {"endpoint": base, "source_dataset": args.source, "project": args.project}
    output = Path(args.output)
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=120) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {"limit": 100})).structuredContent
        assert projects["connection"]["mcp_url"] == base + "/api/mcp/"
        assert args.project in {p["id"] for p in projects["projects"]}

        async def call(tool, **arguments):
            response = await session.call_tool(tool, {"project_id": args.project, **arguments})
            assert not response.isError, response.structuredContent
            return response.structuredContent

        if args.verify_only:
            record = json.loads(output.read_text())
            assert record["project"] == args.project
            assert record["source_dataset"] == args.source
            await verify(call, record)
            if args.preview_check:
                preview = await call(
                    "run_dataset_pipeline",
                    dataset=record["dataset"],
                    pipeline=record["recipe"]["id"],
                    source_cell=record["source"]["id"],
                    source_fingerprint=record["source"]["fingerprint"],
                    mode="preview",
                    preview_rows=100,
                    request_key=f"post-restart-preview-{int(time.time())}",
                )
                record["post_restart_preview"] = preview
                output.write_text(json.dumps(record, indent=2) + "\n")
                deadline = time.monotonic() + 120
                while time.monotonic() < deadline:
                    job = await call("get_job", kind="dataset_pipeline", id=preview["job"]["id"])
                    if job["status"] in {"completed", "failed", "cancelled"}:
                        record["post_restart_preview"] = job
                        output.write_text(json.dumps(record, indent=2) + "\n")
                        assert job["status"] == "completed", job
                        assert job["details"]["output_cell"] is None
                        assert job["details"]["result"]["steps"][0]["output_rows"] == 100
                        await verify(call, record)
                        break
                    await asyncio.sleep(1)
                else:
                    raise AssertionError("Preview did not complete within two minutes.")
            output.write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps(record["verification"], indent=2))
            return

        inspected = await call("inspect_dataset", dataset=args.source)
        source = inspected["active"]
        record["source"] = source
        workbench = await call("inspect_dataset_workbench", dataset=args.source)
        runtime = workbench["runner"]["images"][0]
        destination = await call(
            "start_dataset",
            name="Tasksource retained execution verification",
            intent="explore",
            brief="Verify full-population bounded execution against the existing prepared Tasksource data. Preserve all fields and observations. This validates execution and technical format only; inherited target semantics and upstream provenance remain uncertified. No training.",
        )
        dataset = (
            destination["dataset"]["id"]
            if isinstance(destination["dataset"], dict)
            else destination["dataset"]
        )
        record["dataset"] = dataset
        prefix = f"large-preparation-{int(time.time())}"
        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            path = Path(directory)
            manifest = {
                "version": 1,
                "runtime": runtime,
                "limits": {"scratch_mb": 1024, "memory_mb": 1024, "seconds": 300},
                "steps": [
                    {
                        "id": "preserve",
                        "input": "source",
                        "name": "Validate and preserve decision observations",
                        "entrypoint": "preserve.py",
                        "batch_rows": 20000,
                        "consumer": "decision_train",
                        "input_schema": {"source_row": "integer", "decision": "object"},
                        "output_schema": {"source_row": "integer", "decision": "object"},
                        "checks": {"preserve_rows": True},
                    }
                ],
            }
            (path / "manifest.json").write_text(json.dumps(manifest))
            (path / "preserve.py").write_text(
                "import sys\nwith open(sys.argv[1]) as source, open(sys.argv[2], 'w') as output:\n    for line in source:\n        output.write(line)\n"
            )
            uploaded = await asyncio.to_thread(
                subprocess.run,
                [
                    "overmind",
                    "dataset",
                    "pipeline-upload",
                    directory,
                    "--project-id",
                    args.project,
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert uploaded.returncode == 0, uploaded.stdout
            package = json.loads(uploaded.stdout)
        recipe = (
            await call(
                "save_dataset_pipeline",
                dataset=dataset,
                name="Preserve decision observations",
                request_key=prefix,
                package=package["id"],
            )
        )["pipeline"]
        record["recipe"] = recipe
        run = await call(
            "run_dataset_pipeline",
            dataset=dataset,
            pipeline=recipe["id"],
            source_cell=source["id"],
            source_fingerprint=source["fingerprint"],
            request_key=prefix,
        )
        record["job"] = run["job"]
        output.write_text(json.dumps(record, indent=2) + "\n")
        deadline = time.monotonic() + 3600
        last = None
        started = time.monotonic()
        interrupted = False
        while time.monotonic() < deadline:
            job = await call("get_job", kind="dataset_pipeline", id=run["job"]["id"])
            progress = job.get("progress", {})
            steps = progress.get("steps", [])
            facts = {
                "state": job["status"],
                "stage": progress.get("stage"),
                "batches": steps[0].get("batches") if steps else None,
            }
            if facts != last:
                print(json.dumps(facts), flush=True)
                last = facts
            record["result"] = job
            record["wall_seconds"] = round(time.monotonic() - started, 2)
            output.write_text(json.dumps(record, indent=2) + "\n")
            if (
                args.restart_controller
                and not interrupted
                and job["status"] == "running"
                and steps
                and steps[0].get("provider_execution")
            ):
                restarted = await asyncio.to_thread(
                    subprocess.run,
                    ["docker", "compose", "restart", "workshop-runner"],
                    capture_output=True,
                    text=True,
                    timeout=45,
                )
                assert restarted.returncode == 0, restarted.stderr
                interrupted = True
                continue
            if job["status"] in {"completed", "failed", "cancelled"}:
                if interrupted and "interrupted_job" not in record:
                    assert job["status"] == "failed", job
                    assert "controller stopped" in job["details"]["error"], job
                    assert job["details"]["output_cell"] is None, job
                    record["interrupted_job"] = job
                    print(
                        "Verified interrupted attempt failed without publication; submitting a new explicit attempt.",
                        flush=True,
                    )
                    run = await call(
                        "run_dataset_pipeline",
                        dataset=dataset,
                        pipeline=recipe["id"],
                        source_cell=source["id"],
                        source_fingerprint=source["fingerprint"],
                        request_key=prefix + "-retry",
                    )
                    record["job"] = run["job"]
                    started = time.monotonic()
                    continue
                assert job["status"] == "completed", job
                assert job["details"]["result"]["rows"] == source["rows"]
                assert steps[0]["batches"]["input_rows"] == source["rows"]
                assert steps[0]["batches"]["output_rows"] == source["rows"]
                await verify(call, record)
                output.write_text(json.dumps(record, indent=2) + "\n")
                print(
                    json.dumps(
                        {
                            "verified_rows": source["rows"],
                            "wall_seconds": record["wall_seconds"],
                            "output": str(output),
                        }
                    ),
                    flush=True,
                )
                return
            await asyncio.sleep(10)
        raise AssertionError(
            "Large preparation did not finish within one hour; inspect its saved receipt."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--restart-controller", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--preview-check", action="store_true")
    asyncio.run(main(parser.parse_args()))
