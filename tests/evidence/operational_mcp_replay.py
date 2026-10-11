"""Saved local MCP connection; submit once, then reconnect with read-only commands."""

import argparse
import json
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"
DEPLOYMENT = "adc3fb75-861b-4908-b390-55f6525dfc8e"
REQUEST_KEY = "operational-receipt-20261008-cold-01"


async def replay(args):
    path = Path(".codex/config.toml")
    local = tomllib.loads(path.read_text()) if path.exists() else {}
    connection = local.get("mcp_servers", {}).get("overmind")
    if connection is None:
        connection = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
            "overmind"
        ]
    assert urlparse(connection["url"]).hostname in {"localhost", "127.0.0.1"}
    async with (
        httpx.AsyncClient(headers=connection.get("http_headers", {}), timeout=60) as http,
        streamable_http_client(connection["url"], http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = await session.call_tool("list_projects", {})
        assert not projects.isError
        assert any(project["id"] == PROJECT for project in projects.structuredContent["projects"])
        if args.action in {"catalog", "model"}:
            uri = (
                "overmind://interface/current"
                if args.action == "catalog"
                else f"overmind://deployments/{DEPLOYMENT}?project_id={PROJECT}"
            )
            response = await session.read_resource(uri)
            print(json.dumps([json.loads(content.text) for content in response.contents]))
            return
        if args.action == "submit":
            result = await session.call_tool(
                "run_inference",
                {
                    "project_id": PROJECT,
                    "deployment": DEPLOYMENT,
                    "request_key": args.request_key,
                    "messages": [{"role": "user", "content": "Reply exactly READY."}],
                    "temperature": 0,
                    "max_tokens": 16,
                },
            )
        elif args.action == "read":
            result = await session.call_tool(
                "get_job", {"project_id": PROJECT, "kind": "inference_request", "id": args.id}
            )
        else:
            result = await session.call_tool(
                "inspect_operation",
                {
                    "project_id": PROJECT,
                    "operation": args.id,
                    "after": args.after,
                    "limit": args.limit,
                },
            )
        print(json.dumps({"is_error": result.isError, "result": result.structuredContent}))
        assert not result.isError


parser = argparse.ArgumentParser()
parser.add_argument("action", choices=["submit", "read", "events", "catalog", "model"])
parser.add_argument("id", nargs="?")
parser.add_argument("--after", type=int, default=0)
parser.add_argument("--limit", type=int, default=25)
parser.add_argument("--request-key", default=REQUEST_KEY)
anyio.run(replay, parser.parse_args())
