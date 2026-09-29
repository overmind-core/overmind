from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from typer.testing import CliRunner

AGENTS = Path(__file__).parent / "agents"
SDK = Path(__file__).resolve().parents[2] / "overmind"


@dataclass
class SampleAgent:
    repo: Path
    truth: dict[str, Any]

    @property
    def toml(self) -> Path:
        return self.repo / "overmind.toml"

    @property
    def analysis(self) -> Path:
        return self.repo / "overmind_capabilities.json"

    @classmethod
    def copy(cls, name: str, into: Path) -> SampleAgent:
        repo = into / name
        shutil.copytree(
            AGENTS / name,
            repo,
            ignore=shutil.ignore_patterns(
                "__pycache__", "truth.json", "overmind_capabilities.json"
            ),
        )
        truth = json.loads((AGENTS / name / "truth.json").read_text())
        git = ["git", "-c", "user.name=journey", "-c", "user.email=journey@example.com"]
        subprocess.run([*git, "init", "-q", "-b", "main"], cwd=repo, check=True)
        subprocess.run([*git, "add", "-A"], cwd=repo, check=True)
        subprocess.run([*git, "commit", "-q", "-m", "support desk"], cwd=repo, check=True)
        return cls(repo=repo, truth=truth)

    def commit(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def scan_output(self, *, without: tuple[str, ...] = ()) -> None:
        data = json.loads((AGENTS / self.repo.name / "overmind_capabilities.json").read_text())
        data["capabilities"] = [c for c in data["capabilities"] if c["slug_hint"] not in without]
        self.analysis.write_text(json.dumps(data))

    def run(self, *tickets: str, api_url: str, llm_url: str) -> list[str]:
        env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(SDK), str(self.repo)]),
            "OPENAI_BASE_URL": llm_url,
            "OPENAI_API_KEY": "sk-fake",
            "OVERMIND_API_URL": api_url,
            "OVERMIND_ANALYTICS_ENABLED": "false",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
            "LITELLM_LOCAL_MODEL_COST_MAP": "True",
        }
        env.pop("OVERMIND_API_KEY", None)
        result = subprocess.run(
            [sys.executable, "-m", "support_desk", *tickets],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise AssertionError(f"support_desk exited {result.returncode}:\n{result.stderr}")
        return result.stdout.splitlines()


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

    def scan(self, agent: SampleAgent, *, without: tuple[str, ...] = ()) -> None:
        from overmind.utils import convert_json_to_toml

        self.run("chassis", "--root", str(agent.repo))
        agent.scan_output(without=without)
        convert_json_to_toml(agent.analysis, agent.toml)
        agent.analysis.unlink()

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

        from mcp.shared.exceptions import McpError

        try:
            result = asyncio.run(self._session(action))
        except* McpError as group:
            raise LookupError(str(group.exceptions[0])) from None
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


class RestSurface:
    def __init__(self, api_url: str, api_key: str) -> None:
        self.api_url = api_url
        self.api_key = api_key

    def request(self, method: str, path: str, **kwargs) -> Any:
        import requests

        response = requests.request(
            method,
            f"{self.api_url}{path}",
            headers={"X-Api-Key": self.api_key, **kwargs.pop("headers", {})},
            timeout=30,
            **kwargs,
        )
        if response.status_code >= 400:
            raise AssertionError(
                f"{method} {path} -> {response.status_code}: {response.text[:500]}"
            )
        return response.json() if response.content else None
