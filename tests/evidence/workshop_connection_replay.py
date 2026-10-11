"""Verify a fresh native MCP transport using the saved connection, read-only."""

import json
import tomllib
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import AnyUrl


async def verify():
    project_path = Path(".codex/config.toml")
    project = tomllib.loads(project_path.read_text()) if project_path.exists() else {}
    config = project.get("mcp_servers", {}).get("overmind")
    if config is None:
        config = tomllib.loads((Path.home() / ".codex/config.toml").read_text())["mcp_servers"][
            "overmind"
        ]
    async with (
        httpx.AsyncClient(headers=config.get("http_headers", {}), timeout=60) as http,
        streamable_http_client(config["url"], http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        initialized = await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names >= {
            "inspect_dataset_workbench",
            "save_dataset_pipeline",
            "run_dataset_pipeline",
            "import_dataset_version",
            "update_dataset",
            "validate_dataset_pipeline",
            "cancel_dataset_pipeline_run",
            "save_dataset_pipeline_binding",
            "set_dataset_pipeline_binding_state",
            "run_dataset_pipeline_binding",
        }
        assert not names & {
            "message_dataset_agent",
            "run_dataset",
            "manage_dataset_workflow",
        }
        interface = await session.read_resource(AnyUrl("overmind://interface/current"))
        assert interface.contents
        projects = await session.call_tool("list_projects", {})
        assert not projects.isError
        print(
            json.dumps(
                {
                    "server_version": initialized.serverInfo.version,
                    "tools": len(names),
                    "workshop_lifecycle_tools_verified": 10,
                    "retired_tools": 0,
                    "transport": "MCP SDK Streamable HTTP",
                    "resource_read": "passed",
                    "authenticated_call": "passed",
                }
            )
        )


anyio.run(verify)
