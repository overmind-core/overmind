"""Installed CLI -> local landing -> MCP cells -> CLI export, without injected CLI auth."""

import argparse
import asyncio
import json
import os
import shutil
import time
import tomllib
import uuid
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl


async def replay(directory):
    config = tomllib.loads((Path.home() / ".codex/config.toml").read_text())
    server = config["mcp_servers"]["overmind"]
    assert server["url"] == "http://localhost:8000/api/mcp/"
    executable = shutil.which("overmind")
    assert executable
    environment = dict(os.environ)
    for name in ("OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"):
        environment.pop(name, None)
    environment["OVERMIND_ANALYTICS_ENABLED"] = "false"
    assert not (directory / "overmind.toml").exists()
    evidence = {
        "cli": executable,
        "cwd": str(directory),
        "credential_flags": False,
        "credential_environment": False,
        "cases": [],
        "deleted_fixtures": [],
    }

    async def cli(*arguments):
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            cwd=directory,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
        assert process.returncode == 0, stdout.decode() + stderr.decode()
        return json.loads(stdout)

    async with (
        httpx.AsyncClient(headers=server["http_headers"], timeout=60, trust_env=False) as http,
        streamable_http_client(server["url"], http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        evidence["server_version"] = initialized.serverInfo.version
        await session.read_resource(AnyUrl("overmind://dataset-upload"))

        async def call(name, arguments):
            result = await session.call_tool(name, arguments)
            assert not result.isError, result.content
            return result.structuredContent

        projects = (await call("list_projects", {"limit": 100}))["projects"]
        chosen = [p for p in projects if p["name"] in {"overmind", "financial-services"}]
        assert len(chosen) == 2
        for project in chosen:
            project_id = project["id"]
            uploaded = await cli(
                "dataset",
                "upload",
                str(directory / "handoff.csv"),
                "--project-id",
                project_id,
                "--intent",
                "train",
                "--json",
                "--wait",
            )
            dataset = uploaded["id"]
            try:
                args = {"project_id": project_id, "dataset": dataset}
                source = (await call("inspect_dataset", args))["active"]
                assert source["rows"] == 3
                source_id = source["id"]
                for name, step in [
                    (
                        "Keep declared examples",
                        {"operation": "filter", "column": "keep", "equals": "yes"},
                    ),
                    (
                        "Conversation projection",
                        {
                            "operation": "conversation",
                            "question": "question",
                            "answer": "answer",
                        },
                    ),
                ]:
                    key = uuid.uuid4().hex
                    recipe = (
                        await call(
                            "save_dataset_pipeline",
                            {**args, "name": name, "request_key": key, "steps": [step]},
                        )
                    )["pipeline"]
                    run = await call(
                        "run_dataset_pipeline",
                        {
                            **args,
                            "pipeline": recipe["id"],
                            "source_cell": source["id"],
                            "source_fingerprint": source["fingerprint"],
                            "request_key": key,
                        },
                    )
                    deadline = time.monotonic() + 90
                    while True:
                        job = await call(
                            "get_job",
                            {
                                "project_id": project_id,
                                "kind": "dataset_pipeline",
                                "id": run["job"]["id"],
                            },
                        )
                        if job["status"] == "completed":
                            break
                        assert job["status"] not in {"failed", "cancelled"}, job
                        assert time.monotonic() < deadline
                        await asyncio.sleep(1)
                    source = (await call("inspect_dataset", args))["active"]
                    assert source["rows"] == 2
                rows = (
                    await call(
                        "query_dataset",
                        {**args, "sql": "SELECT * FROM t ORDER BY source_row"},
                    )
                )["rows"]
                assert len(rows) == 2 and all(row["messages"] for row in rows)
                original = await call(
                    "query_dataset", {**args, "cell": source_id, "sql": "SELECT * FROM t"}
                )
                assert original["n"] == 3
                output = directory / f"export-{dataset}.jsonl"
                exported = await cli(
                    "dataset",
                    "export",
                    dataset,
                    "--cell",
                    source["id"],
                    "--output",
                    str(output),
                    "--json",
                )
                assert exported["cell"] == source["id"]
                exported_rows = [json.loads(line) for line in output.read_text().splitlines()]
                assert len(exported_rows) == 2 and all(row["messages"] for row in exported_rows)
                evidence["cases"].append(
                    {
                        "project": project["name"],
                        "source_rows": 3,
                        "output_rows": 2,
                        "transformation_cells": 2,
                        "original_rows_preserved": 3,
                        "exported_exact_cell": True,
                        "status": "passed",
                    }
                )
            finally:
                removed = await http.delete(f"http://localhost:8000/api/datasets/{dataset}/")
                assert removed.status_code == 204
                evidence["deleted_fixtures"].append(dataset)
    evidence["browser_calls"] = 0
    evidence["paid_jobs"] = 0
    print(json.dumps(evidence))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    asyncio.run(replay(parser.parse_args().directory))
