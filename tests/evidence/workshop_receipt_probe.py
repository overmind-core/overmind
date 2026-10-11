import argparse
import asyncio
import json
import os
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"


async def main(options):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    with (config / "overmind/connection.toml").open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    fixture = json.loads(options.fixture.read_text())
    report = {"endpoint": base, "cases": [], "success": False}
    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            projects = await session.call_tool("list_projects", {})
            assert PROJECT in {p["id"] for p in projects.structuredContent["projects"]}
            report["catalog_sha256"] = projects.structuredContent["catalog_sha256"]
            for case in fixture["cases"]:
                for run in case["runs"]:
                    started = time.monotonic()
                    result = await session.call_tool(
                        "get_job",
                        {"project_id": PROJECT, "kind": "dataset_pipeline", "id": run["id"]},
                    )
                    assert not result.isError, result.structuredContent
                    job = result.structuredContent
                    uri = job["details"]["evidence_resource"]["uri"]
                    resource = await session.read_resource(uri)
                    full = json.loads(resource.contents[0].text)
                    assert (
                        full["id"] == job["id"] and full["status"] == job["status"] == "completed"
                    )
                    assert full["source_fingerprint"] == job["details"]["source_fingerprint"]
                    assert full["result"]["stage_seconds"] == job["progress"]["stage_seconds"]
                    for observed, retained in zip(
                        job["progress"]["steps"], full["result"]["steps"], strict=True
                    ):
                        for key in (
                            "input_rows",
                            "output_rows",
                            "output_fingerprint",
                            "check_results",
                            "state",
                        ):
                            assert observed.get(key) == retained.get(key)
                        assert "input_examples" not in observed.get("impact", {})
                        assert "input_examples" in retained.get("impact", {})
                    wire_bytes = len(result.model_dump_json().encode())
                    assert wire_bytes < 64000
                    report["cases"].append(
                        {
                            "kind": case["kind"],
                            "id": run["id"],
                            "mode": job["details"]["mode"],
                            "wire_bytes": wire_bytes,
                            "evidence_bytes": len(resource.contents[0].text.encode()),
                            "seconds": round(time.monotonic() - started, 3),
                            "resource": uri,
                            "server_seconds": job["progress"].get("seconds"),
                            "stage_seconds": job["progress"].get("stage_seconds"),
                        }
                    )
            report["success"] = True
    finally:
        options.report.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
