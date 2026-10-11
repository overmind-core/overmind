"""Create an explicitly synthetic fork for UI verification through MCP and local transfer."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection


async def main(project):
    key, base = resolve_transfer_connection("", "", None)
    assert base in {"http://localhost:8000", "http://127.0.0.1:8000"}
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=60) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        async def call(tool_name, **arguments):
            result = await session.call_tool(tool_name, {"project_id": project, **arguments})
            assert not result.isError, result.structuredContent
            return result.structuredContent

        projects = await session.call_tool("list_projects", {"limit": 100})
        assert any(item["id"] == project for item in projects.structuredContent["projects"])
        with tempfile.TemporaryDirectory(prefix="workshop-canvas-") as directory:
            source = Path(directory) / "canvas-verification-synthetic.jsonl"
            source.write_text(
                "".join(
                    json.dumps(row) + "\n"
                    for row in [
                        {"item": "Example A", "eligible": True},
                        {"item": "Example B", "eligible": False},
                        {"item": "Example C", "eligible": True},
                    ]
                )
            )
            uploaded = await asyncio.to_thread(
                subprocess.run,
                [
                    "overmind",
                    "dataset",
                    "upload",
                    str(source),
                    "--project-id",
                    project,
                    "--intent",
                    "explore",
                    "--wait",
                    "--json",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=True,
            )
            dataset = json.loads(uploaded.stdout)["id"]
        original = (await call("inspect_dataset", dataset=dataset))["active"]
        outputs = []
        for eligible in (True, False):
            recipe = (
                await call(
                    "save_dataset_pipeline",
                    name=f"Eligibility = {eligible}",
                    request_key=str(uuid.uuid4()),
                    steps=[{"operation": "filter", "column": "eligible", "equals": eligible}],
                )
            )["pipeline"]
            receipt = await call(
                "run_dataset_pipeline",
                dataset=dataset,
                pipeline=recipe["id"],
                source_cell=original["id"],
                source_fingerprint=original["fingerprint"],
                request_key=str(uuid.uuid4()),
            )
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                job = await call("get_job", kind="dataset_pipeline", id=receipt["job"]["id"])
                if job["status"] in {"completed", "failed", "cancelled"}:
                    assert job["status"] == "completed", job
                    outputs.append(job["details"]["output_cell"])
                    break
                await asyncio.sleep(1)
            else:
                raise AssertionError("Fixture transformation did not complete")
        print(
            json.dumps(
                {
                    "dataset": dataset,
                    "project": project,
                    "source": original["id"],
                    "outputs": outputs,
                    "url": f"http://localhost:5173/datasets/{dataset}?projectId={project}",
                }
            )
        )


parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True)
asyncio.run(main(parser.parse_args().project))
