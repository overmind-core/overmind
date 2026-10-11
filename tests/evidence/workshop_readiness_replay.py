"""Repeat an explicit local MCP readiness request without launching training."""

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection


async def main(args):
    path = Path(args.record)
    previous = json.loads(path.read_text())
    request = previous["request"]
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000"
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=300) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {"limit": 100})).structuredContent
        assert projects["connection"]["mcp_url"] == base + "/api/mcp/"
        assert request["project_id"] in {p["id"] for p in projects["projects"]}
        started = time.monotonic()
        result = await session.call_tool("check_finetune_readiness", request)
        assert not result.isError, result.structuredContent
        data = result.structuredContent
        report = {
            "seconds": round(time.monotonic() - started, 3),
            "request": request,
            "ready": data["ready"],
            "validation": data["dataset"]["validation"],
            "assessment": data["assessment"],
            "missing": data["missing"],
        }
        path.write_text(json.dumps(report, indent=2) + "\n")
        assert report["ready"], report
        assert report["validation"]["num_examples"] == args.expected_rows, report
        print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", required=True)
    parser.add_argument("--expected-rows", required=True, type=int)
    asyncio.run(main(parser.parse_args()))
