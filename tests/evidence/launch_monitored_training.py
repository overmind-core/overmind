"""Local MCP qualification with one frozen recipe and an explicit launch switch."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def run(args):
    recipe = json.loads(args.recipe.read_text())
    if not recipe.get("request_key") or not recipe.get("project_id"):
        raise ValueError("A stable request key and local project are required")
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
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=120) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        catalogue = {tool.name: tool for tool in (await session.list_tools()).tools}

        async def call(name, source):
            allowed = catalogue[name].inputSchema["properties"]
            response = await session.call_tool(
                name, {k: v for k, v in source.items() if k in allowed}
            )
            if response.isError:
                raise ValueError(json.dumps(response.structuredContent))
            return response.structuredContent

        projects = await call("list_projects", {})
        assert projects["connection"]["mcp_url"] == transport["url"]
        assert recipe["project_id"] in {item["id"] for item in projects["projects"]}
        selected = {**recipe, "monitoring": recipe["hyperparameters"]["monitoring"]}
        readiness = await call("check_finetune_readiness", selected)
        estimate = await call("estimate_finetune", selected)
        preparation = await call(
            "prepare_training_data",
            {
                **selected,
                "context_length": recipe["hyperparameters"]["context_length"],
                "training_type": "lora",
            },
        )
        result = {
            "recipe": recipe,
            "readiness": readiness,
            "estimate": estimate,
            "preparation": preparation,
        }
        if args.launch:
            if not readiness["ready"] or preparation["state"] != "ready":
                raise ValueError("Preparation/readiness is not ready; no launch was submitted")
            result["launch"] = await call("start_finetune", recipe)
        args.output.write_text(json.dumps(result, indent=2, default=str))
        print(
            json.dumps(
                {
                    "output": str(args.output),
                    "ready": readiness["ready"],
                    "preparation": preparation["state"],
                    "launch": result.get("launch"),
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--launch", action="store_true")
    asyncio.run(run(parser.parse_args()))
