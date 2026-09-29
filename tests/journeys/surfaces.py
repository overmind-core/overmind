from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from typer.testing import CliRunner

AGENTS = Path(__file__).parent / "agents"


@dataclass
class SampleAgent:
    repo: Path
    truth: dict[str, Any]

    @property
    def toml(self) -> Path:
        return self.repo / "overmind.toml"

    @classmethod
    def copy(cls, name: str, into: Path) -> SampleAgent:
        repo = into / name
        shutil.copytree(AGENTS / name, repo, ignore=shutil.ignore_patterns("__pycache__"))
        return cls(repo=repo, truth=json.loads((repo / "truth.json").read_text()))


class CliSurface:
    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def run(self, *args: str) -> str:
        from overmind.__main__ import app

        result = CliRunner().invoke(app, list(args), catch_exceptions=False)
        if result.exit_code != 0:
            raise AssertionError(
                f"overmind {' '.join(args)} exited {result.exit_code}:\n{result.output}"
            )
        return result.output

    def sync(self, agent: SampleAgent) -> str:
        return self.run(
            "sync",
            "up",
            "--api-key",
            self.api_key,
            "--api-url",
            self.api_url,
            "--path",
            str(agent.toml),
        )

    def project_key(self, agent: SampleAgent) -> str:
        from overmind.config import load

        return load(agent.toml).api_key


class McpSurface:
    def __init__(self, api_url: str, api_key: str) -> None:
        self.url = f"{api_url}/api/mcp/"
        self.api_key = api_key

    async def _session(self, action):
        async with (
            streamablehttp_client(self.url, headers={"X-Api-Key": self.api_key}) as (
                read,
                write,
                _,
            ),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            return await action(session)

    def read(self, uri: str) -> dict[str, Any]:
        async def action(session: ClientSession):
            return await session.read_resource(uri)

        result = asyncio.run(self._session(action))
        return json.loads(result.contents[0].text)

    def call(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        async def action(session: ClientSession):
            return await session.call_tool(tool, arguments or {})

        result = asyncio.run(self._session(action))
        if result.isError:
            raise AssertionError(f"MCP {tool} failed: {result.content}")
        return result.structuredContent or json.loads(result.content[0].text)

    def capability(self, slug: str) -> dict[str, Any]:
        return self.read(f"overmind://capabilities/{slug}")

    def project(self) -> dict[str, Any]:
        return self.read("overmind://project/current")
