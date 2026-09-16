from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump
from overmind.connector_cmd import ConnectorAddError, add_connector


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 201):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(payload)

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.headers = {}
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self.response

    def close(self):
        pass


def test_add_connector_posts_credentials_and_strips_hint():
    session = FakeSession(
        FakeResponse({
            "id": "conn-1",
            "name": "langfuse",
            "connector_type": "langfuse",
            "verified_at": "2026-01-01T00:00:00Z",
            "api_key_hint": "pk-l****************",
        })
    )

    result = add_connector(
        "langfuse",
        project_id="project-1",
        api_key="ovr_key",
        api_url="http://localhost:8000",
        provider_key="pk-lf-real",
        provider_secret="sk-lf-real",
        session=session,
    )

    assert result["id"] == "conn-1"
    assert result["verified"] is True
    assert result["next_mcp_calls"][0]["arguments"]["include_source_projects"] is True
    encoded = json.dumps(result)
    assert "pk-lf-real" not in encoded
    assert "sk-lf-real" not in encoded
    assert "api_key_hint" not in encoded
    assert "pk-l" not in encoded
    assert session.calls[0][0] == "POST"
    assert session.calls[0][1].endswith("/api/connector-credentials/")
    body = session.calls[0][2]["json"]
    assert body["project"] == "project-1"
    assert body["connector_type"] == "langfuse"
    assert body["api_key"] == "pk-lf-real"
    assert body["api_secret"] == "sk-lf-real"
    assert body["auto_sync_enabled"] is False


def test_add_connector_rejects_unknown_type():
    with pytest.raises(ConnectorAddError, match="Unsupported connector type"):
        add_connector(
            "opik",
            project_id="project-1",
            api_key="ovr_key",
            api_url="http://localhost:8000",
            provider_key="k",
        )


def test_add_connector_surfaces_http_detail():
    session = FakeSession(FakeResponse({"detail": "Verify failed"}, status_code=400))

    with pytest.raises(ConnectorAddError, match="HTTP 400: Verify failed"):
        add_connector(
            "langfuse",
            project_id="project-1",
            api_key="ovr_key",
            api_url="http://localhost:8000",
            provider_key="pk",
            provider_secret="sk",
            session=session,
        )


def test_add_command_json_from_env(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "overmind.toml"
    dump(Config(api_key="toml-key", project_id="toml-project"), config_path)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env")
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)

    captured: dict = {}

    def fake_add(kind, **kwargs):
        captured["kind"] = kind
        captured.update(kwargs)
        return {
            "id": "conn-1",
            "name": "langfuse",
            "connector_type": "langfuse",
            "verified": True,
            "next_mcp_calls": [],
        }

    monkeypatch.setattr("overmind.connector_cmd.add_connector", fake_add)
    result = CliRunner().invoke(
        app,
        ["connector", "add", "langfuse", "--path", str(config_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["id"] == "conn-1"
    assert captured["kind"] == "langfuse"
    assert captured["provider_key"] == "pk-env"
    assert captured["provider_secret"] == "sk-env"
    assert captured["project_id"] == "toml-project"
    assert "pk-env" not in result.output
    assert "sk-env" not in result.output
    assert "api_key_hint" not in result.output


def test_add_command_missing_env_non_tty(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "overmind.toml"
    dump(Config(api_key="toml-key", project_id="toml-project"), config_path)
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("OVERMIND_CONNECTOR_API_KEY", raising=False)
    monkeypatch.delenv("OVERMIND_CONNECTOR_API_SECRET", raising=False)

    result = CliRunner().invoke(
        app,
        ["connector", "add", "langfuse", "--path", str(config_path), "--json"],
    )

    assert result.exit_code == 1
    body = json.loads(result.output)
    assert "LANGFUSE_PUBLIC_KEY" in body["error"]
    assert "LANGFUSE_SECRET_KEY" in body["error"]
    assert "Do not paste them into chat" in body["error"]


def test_add_help_has_no_provider_key_flags(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")
    result = CliRunner().invoke(app, ["connector", "add", "--help"])

    assert result.exit_code == 0, result.output
    help_text = "".join(re.sub(r"\x1b\[[0-9;]*m", "", result.output).split())
    assert "--api-key" in help_text
    assert "LANGFUSE_PUBLIC_KEY" not in help_text
    assert "LANGFUSE_SECRET_KEY" not in help_text
    assert "--provider" not in help_text.lower()
