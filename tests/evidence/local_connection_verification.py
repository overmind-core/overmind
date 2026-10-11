import json
import os
import subprocess
import tomllib
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def discover(transport):
    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=30) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        result = await session.call_tool("list_projects", {"limit": 100})
        assert not result.isError
        payload = result.structuredContent or json.loads(result.content[0].text)
        assert payload["connection"]["mcp_url"] == "http://localhost:8000/api/mcp/"
        return payload["projects"]


home = Path("/Users/tyleredwards")
roots = [
    home / "Documents/ChatGPT" / n
    for n in ["Board paper", "empty", "thesis", "Gameboy", "devday", "Launch Video"]
]
roots += [
    home / "Documents/GitHub" / n
    for n in ["Biomni", "website", "paper-qa", "vigil", "wazuh", "financial-services", "overmind"]
]
global_config = tomllib.loads((home / ".codex/config.toml").read_text())
roots += [Path(p) for p in global_config.get("projects", {}) if Path(p).is_dir()]
environment = dict(os.environ)
for name in ["OVERMIND_API_KEY", "OVERMIND_API_URL", "OVERMIND_BASE_URL"]:
    environment.pop(name, None)
results = []
for root in dict.fromkeys(roots):
    process = subprocess.run(
        ["/usr/local/bin/codex", "mcp", "get", "overmind", "--json"],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert process.returncode == 0, f"Effective configuration unavailable: {root}"
    config = json.loads(process.stdout)
    assert (
        config.get("enabled") and config["transport"]["url"] == "http://localhost:8000/api/mcp/"
    ), str(root)
    accessible = anyio.run(discover, config["transport"])
    assert accessible
    project_ids = {item["id"] for item in accessible}
    path = root / "overmind.toml"
    project = tomllib.loads(path.read_text()).get("project-id") if path.exists() else None
    if not project:
        project = (
            "e18b29b5-915d-45a7-80cd-77ffe6559205"
            if "e18b29b5-915d-45a7-80cd-77ffe6559205" in project_ids
            else accessible[0]["id"]
        )
    assert project in project_ids, (
        f"Workspace project is not accessible through its MCP connection: {root}"
    )
    process = subprocess.run(
        [
            "/Users/tyleredwards/.local/bin/overmind",
            "connection",
            "check",
            "--project-id",
            project,
            "--json",
        ],
        cwd=root,
        env=environment,
        text=True,
        capture_output=True,
    )
    check = json.loads(process.stdout)
    row = {
        "workspace": str(root),
        "mcp_url": config["transport"]["url"],
        "mcp_authenticated": True,
        "ready": check.get("ready"),
        "api_url": check.get("api_url"),
        "project_id": project,
        "credential_scope": check.get("transfer", {}).get("credential_scope"),
    }
    if not check.get("ready"):
        row["error"] = check.get("error")
    results.append(row)
    print(json.dumps(row), flush=True)
assert all(row["ready"] and row["api_url"] == "http://localhost:8000" for row in results)
assert global_config["plugins"]["overmind@personal"]["enabled"] is False
print(
    json.dumps(
        {
            "workspaces_passed": len(results),
            "hosted_plugin_enabled": False,
            "injected_credentials": False,
        }
    )
)
