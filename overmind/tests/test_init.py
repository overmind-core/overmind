import json
import stat
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump

runner = CliRunner()


def _init(tmp_path: Path, ide: str, *extra: str) -> None:
    result = runner.invoke(
        app,
        ["init", "--ide", ide, "--env", "local", *extra],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output


def test_init_writes_cursor_mcp_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "cursor")
    cfg = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert cfg["mcpServers"]["overmind"] == {
        "url": "http://localhost:8000/api/mcp/",
    }
    assert (tmp_path / ".cursor" / "skills" / "overmind" / "SKILL.md").is_file()
    assert (tmp_path / ".cursor" / "skills" / "overmind" / "references" / "setup.md").is_file()


def test_init_seeds_toml_and_slash_commands(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_PROJECT_ID", "proj-uuid")
    monkeypatch.setenv("OVERMIND_API_URL", "http://localhost:8000")
    monkeypatch.delenv("OVERMIND_BASE_URL", raising=False)

    result = runner.invoke(
        app,
        ["init", "--ide", "cursor"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    toml = (tmp_path / "overmind.toml").read_text()
    assert "api-key" not in toml
    assert 'project-id = "proj-uuid"' in toml
    assert 'project-name = "' in toml
    assert 'base-url = "http://localhost:8000"' in toml
    assert "[capabilities]" in toml

    setup_cmd = (tmp_path / ".cursor" / "commands" / "overmind-setup.md").read_text()
    assert setup_cmd.startswith("Scan the repository and sync capabilities\n")
    assert not setup_cmd.startswith("---")
    assert "/overmind setup" in setup_cmd
    assert "Scan the repository" in setup_cmd
    assert ".cursor/skills/overmind/references/setup.md" not in setup_cmd
    tracing_cmd = (tmp_path / ".cursor" / "commands" / "overmind-ensure-tracing.md").read_text()
    assert "/overmind ensure-tracing" in tracing_cmd
    assert "inspect_capability_health" in tracing_cmd

    mcp = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert mcp["mcpServers"]["overmind"]["url"] == "http://localhost:8000/api/mcp/"


def test_init_preserves_existing_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "overmind.toml").write_text('project-id = "keep-me"\n[capabilities]\n')

    _init(tmp_path, "cursor")
    toml = (tmp_path / "overmind.toml").read_text()
    assert 'project-id = "keep-me"' in toml
    assert f'project-name = "{tmp_path.name}"' in toml


def test_init_backfills_project_name_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "overmind.toml").write_text(
        'project-id = ""\n'
        'project-name = "custom-name"\n'
        'repo_summary = "Long summary that must not become the project name."\n'
        "[capabilities]\n"
    )

    _init(tmp_path, "cursor")
    toml = (tmp_path / "overmind.toml").read_text()
    assert 'project-name = "custom-name"' in toml


def test_init_claude_writes_project_root_mcp_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)

    for alias in ("claude", "claude_code", "claude-code"):
        _init(tmp_path, alias)

        # Claude Code reads .mcp.json at the project root, never .claude/mcp.json.
        cfg = json.loads((tmp_path / ".mcp.json").read_text())
        assert cfg["mcpServers"]["overmind"] == {
            "type": "http",
            "url": "http://localhost:8000/api/mcp/",
        }
        assert not (tmp_path / ".claude" / "mcp.json").exists()
        assert (tmp_path / ".claude" / "skills" / "overmind" / "SKILL.md").is_file()
        assert (tmp_path / ".claude" / "commands" / "overmind-setup.md").is_file()
        claude_setup = (tmp_path / ".claude" / "commands" / "overmind-setup.md").read_text()
        assert claude_setup.startswith('---\ndescription: "Scan the repository and sync capabilities"\n---\n')
        assert (tmp_path / "overmind.toml").is_file()


def test_init_claude_preserves_other_servers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "uvx", "args": ["other"]}}}))

    _init(tmp_path, "claude")

    cfg = json.loads((tmp_path / ".mcp.json").read_text())
    assert cfg["mcpServers"]["other"] == {"command": "uvx", "args": ["other"]}
    assert "overmind" in cfg["mcpServers"]


def test_init_keeps_bootstrap_key_out_of_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "secret")
    for ide in ("cursor", "claude", "opencode", "codex"):
        result = runner.invoke(app, ["init", "--ide", ide], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    for path in (
        tmp_path / ".cursor" / "mcp.json",
        tmp_path / ".mcp.json",
        tmp_path / "opencode.json",
        tmp_path / ".codex" / "config.toml",
        tmp_path / "overmind.toml",
    ):
        assert "secret" not in path.read_text()
    assert not (tmp_path / ".overmind" / "credentials.toml").exists()


def test_init_opencode_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "opencode")
    cfg = json.loads((tmp_path / "opencode.json").read_text())
    assert cfg["mcp"]["overmind"] == {
        "type": "remote",
        "url": "http://localhost:8000/api/mcp/",
        "enabled": True,
    }
    assert (tmp_path / ".opencode" / "skills" / "overmind" / "SKILL.md").is_file()
    assert not (tmp_path / ".opencode" / "commands").exists()


def test_init_codex_writes_project_config_and_skill(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "k")
    result = runner.invoke(app, ["init", "--ide", "codex", "--env", "production"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    config = (tmp_path / ".codex" / "config.toml").read_text()
    assert '[mcp_servers.overmind]\nurl = "https://api.overmindlab.ai/api/mcp/"' in config
    assert "http_headers" not in config
    assert "env_http_headers" not in config
    assert (tmp_path / ".agents" / "skills" / "overmind" / "SKILL.md").is_file()
    assert (tmp_path / "overmind.toml").is_file()
    assert not (tmp_path / ".codex" / "commands").exists()


def test_init_codex_replaces_own_section_and_preserves_other_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "k")
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        'model = "gpt-5"\n\n'
        '[mcp_servers.overmind]\nurl = "https://old.example"\n\n'
        '[mcp_servers.other]\nurl = "https://other.example"\n'
    )

    result = runner.invoke(app, ["init", "--ide", "codex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    config = config_path.read_text()
    assert 'model = "gpt-5"' in config
    assert 'url = "https://other.example"' in config
    assert config.count("[mcp_servers.overmind]") == 1
    assert 'url = "https://old.example"' not in config
    assert "http_headers" not in config
    assert "env_http_headers" not in config


def test_init_uses_saved_project_key_when_adding_an_ide(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    monkeypatch.delenv("OVERMIND_BASE_URL", raising=False)
    dump(
        Config(
            api_key="ovr_project_key",
            base_url="http://localhost:8000",
            project_id="11111111-1111-1111-1111-111111111111",
        ),
        tmp_path / "overmind.toml",
    )

    result = runner.invoke(app, ["init", "--ide", "claude"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    config = json.loads((tmp_path / ".mcp.json").read_text())
    assert config["mcpServers"]["overmind"]["headers"] == {"X-Api-Key": "ovr_project_key"}
    assert config["mcpServers"]["overmind"]["url"] == "http://localhost:8000/api/mcp/"


def test_init_does_not_copy_legacy_inline_key_into_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "overmind.toml").write_text(
        'api-key = "legacy-bootstrap"\n'
        'base-url = "https://api.overmindlab.ai"\n'
        'project-id = "11111111-1111-1111-1111-111111111111"\n'
        "[capabilities]\n"
    )

    result = runner.invoke(app, ["init", "--ide", "codex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "legacy-bootstrap" not in (tmp_path / ".codex" / "config.toml").read_text()
    assert "legacy-bootstrap" in (tmp_path / "overmind.toml").read_text()
    assert not (tmp_path / ".overmind" / "credentials.toml").exists()


def test_init_can_prepare_a_tracked_unauthenticated_mcp_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[mcp_servers.other]\ncommand = "other"\n')
    subprocess.run(["git", "add", ".codex/config.toml"], cwd=tmp_path, check=True)

    result = runner.invoke(app, ["init", "--ide", "codex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert "http_headers" not in config_path.read_text()
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o644


def test_init_honors_api_url_over_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    monkeypatch.delenv("OVERMIND_BASE_URL", raising=False)

    result = runner.invoke(
        app,
        [
            "init",
            "--ide",
            "cursor",
            "--env",
            "production",
            "--api-url",
            "http://127.0.0.1:9000",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    mcp = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert mcp["mcpServers"]["overmind"]["url"] == "http://127.0.0.1:9000/api/mcp/"
    assert 'base-url = "http://127.0.0.1:9000"' in (tmp_path / "overmind.toml").read_text()


def test_init_requires_ide(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--env", "local"])
    assert result.exit_code != 0
    assert "ide" in result.output.lower()
