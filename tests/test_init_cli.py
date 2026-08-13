"""``overmind init`` writes the right MCP config shape per IDE and installs the skill.

Covers the three supported IDEs (cursor, claude, opencode) — opencode's config
lives in the project-root ``opencode.json`` under an ``mcp`` key with
``type: "remote"`` (opencode reads no ``.mcp.json``), while cursor/claude get
``{dest}/mcp.json`` with an ``mcpServers`` map. Uses the typer CliRunner so
each test runs in a clean temp cwd.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from typer.testing import CliRunner

from overmind.__main__ import app

runner = CliRunner()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def test_init_opencode_writes_remote_mcp_and_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["init", "--ide", "opencode", "--env", "development"],
        env={"OVERMIND_API_KEY": "ovr_test_key"},
    )
    assert result.exit_code == 0, result.output

    cfg = _read_json(tmp_path / "opencode.json")
    assert cfg["mcp"]["overmind"] == {
        "type": "remote",
        "url": "http://localhost:8000/api/mcp/",
        "headers": {"X-Api-Key": "ovr_test_key"},
    }
    assert (tmp_path / ".opencode" / "skills" / "overmind" / "SKILL.md").is_file()


def test_init_cursor_and_claude_write_mcp_servers_map(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    for ide, dest in (("cursor", ".cursor"), ("claude", ".claude")):
        result = runner.invoke(
            app,
            ["init", "--ide", ide, "--env", "development"],
            env={"OVERMIND_API_KEY": "ovr_test_key"},
        )
        assert result.exit_code == 0, result.output

        cfg = _read_json(tmp_path / dest / "mcp.json")
        assert cfg["mcpServers"]["overmind"] == {
            "url": "http://localhost:8000/api/mcp/",
            "headers": {"X-Api-Key": "ovr_test_key"},
        }
        assert (tmp_path / dest / "skills" / "overmind" / "SKILL.md").is_file()


def test_init_opencode_merges_into_existing_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "opencode.json").write_text(
        json.dumps({"mcp": {"other": {"type": "local", "command": ["x"]}}}),
    )
    result = runner.invoke(
        app,
        ["init", "--ide", "opencode", "--env", "development"],
        env={"OVERMIND_API_KEY": "ovr_test_key"},
    )
    assert result.exit_code == 0, result.output

    cfg = _read_json(tmp_path / "opencode.json")
    assert "other" in cfg["mcp"]  # pre-existing server untouched
    assert cfg["mcp"]["overmind"]["type"] == "remote"


def test_init_rejects_unknown_ide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        app,
        ["init", "--ide", "vscode", "--env", "development"],
        env={"OVERMIND_API_KEY": "ovr_test_key"},
    )
    assert result.exit_code != 0
    assert "use cursor, claude_code or opencode" in result.output
