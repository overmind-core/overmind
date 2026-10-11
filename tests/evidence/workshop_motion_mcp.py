"""Publish a synthetic cell through the local MCP while the Console observes it."""

import argparse
import asyncio
import json
import time
import uuid

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection


async def main(args):
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000"
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=60) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = await session.call_tool("list_projects", {"limit": 100})
        assert any(p["id"] == args.project for p in projects.structuredContent["projects"])

        async def call(tool_name, **arguments):
            result = await session.call_tool(tool_name, {"project_id": args.project, **arguments})
            assert not result.isError, result.content
            return result.structuredContent

        if args.action == "prepare":
            print(
                json.dumps(
                    await call(
                        "derive_dataset",
                        source_cell=args.source,
                        name="Live motion verification · synthetic",
                        request_key=str(uuid.uuid4()),
                    )
                )
            )
            return

        original = (await call("inspect_dataset", dataset=args.dataset))["active"]
        recipe = (
            await call(
                "save_dataset_pipeline",
                name="Eligible examples · motion verification",
                steps=[{"operation": "filter", "column": "eligible", "equals": True}],
                request_key=str(uuid.uuid4()),
            )
        )["pipeline"]
        print(json.dumps({"ready": True, "delay_seconds": args.delay}), flush=True)
        await asyncio.sleep(args.delay)
        receipt = await call(
            "run_dataset_pipeline",
            dataset=args.dataset,
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
                print(json.dumps(job), flush=True)
                return
            await asyncio.sleep(0.5)
        raise AssertionError("Publication did not complete within 60 seconds")


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["prepare", "publish"])
parser.add_argument("--project", required=True)
parser.add_argument("--source")
parser.add_argument("--dataset")
parser.add_argument("--delay", type=float, default=10)
asyncio.run(main(parser.parse_args()))
