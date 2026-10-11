"""Read a live local training run through freshly discovered MCP contracts."""

import argparse
import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def inspect(args):
    configured = subprocess.run(
        ["/usr/local/bin/codex", "mcp", "get", "overmind", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    transport = json.loads(configured.stdout)["transport"]
    if transport["url"] != "http://localhost:8000/api/mcp/":
        raise ValueError("The configured MCP endpoint is not local")
    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=60) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        catalog = await session.list_tools()
        assert "inspect_training_progress" in {tool.name for tool in catalog.tools}
        offset = 0
        selected_project = None
        while selected_project is None:
            discovered = await session.call_tool("list_projects", {"offset": offset, "limit": 100})
            if discovered.isError:
                raise RuntimeError("Project discovery failed")
            projects = discovered.structuredContent
            if projects["connection"]["mcp_url"] != transport["url"]:
                raise ValueError("Project discovery reported a different MCP endpoint")
            selected_project = next(
                (project for project in projects["projects"] if project["id"] == args.project),
                None,
            )
            offset = projects["next_offset"]
            if selected_project is None and offset is None:
                raise ValueError("The requested project is not available on local MCP")

        async def call(name, fields):
            response = await session.call_tool(name, {"project_id": args.project, **fields})
            if response.isError:
                raise RuntimeError(json.dumps(response.structuredContent))
            return response.structuredContent

        job = await call("get_job", {"kind": "finetune_job", "id": args.job})
        progress = await call("inspect_training_progress", {"job": args.job, "limit": 100})
        snapshot = progress["progress"]
        evidence = {}
        for check in snapshot["checks"]:
            if check["evidence_available"]:
                page = await call(
                    "inspect_training_progress", {"job": args.job, "check": check["id"], "limit": 5}
                )
                evidence[check["id"]] = page["progress"]
        probes = {}
        for name in snapshot["probes"]:
            probes[name] = await call(
                "inspect_training_progress", {"job": args.job, "probe": name, "limit": 5}
            )
        resource = await session.read_resource(
            f"overmind://finetunes/{args.job}?project_id={args.project}"
        )
        record = json.loads(resource.contents[0].text)
        deployment = (
            await call("get_job", {"kind": "deployment", "id": record["deployed_model"]})
            if record.get("deployed_model")
            else None
        )
        interface = await session.read_resource("overmind://interface/current")
        result = {
            "observed_at": datetime.now(UTC).isoformat(),
            "endpoint": transport["url"],
            "project": args.project,
            "selected_project": selected_project,
            "interface": json.loads(interface.contents[0].text),
            "job": job,
            "progress": snapshot,
            "evidence_pages": evidence,
            "probe_pages": probes,
            "record": record,
            "deployment": deployment,
            "scope": "Read-only MCP observation; examples are paginated, not exhaustive",
        }
    args.output.write_text(json.dumps(result, indent=2, default=str))
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": job["status"],
                "stage": job.get("progress"),
                "checks": [
                    {
                        "step": c["step"],
                        "state": c["state"],
                        "metrics": c["metrics"],
                        "error": c["error"],
                    }
                    for c in snapshot["checks"]
                ],
                "checkpoint_count": len(snapshot["checkpoints"]),
            },
            default=str,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(inspect(parser.parse_args()))
