import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump
from overmind.init_cmd import claude_config_path

runner = CliRunner()
SKILL_SOURCE = Path(__file__).resolve().parents[1] / "skills" / "overmind"
SURFACE_SKILLS = (
    "overmind-agent",
    "overmind-observability",
    "overmind-datasets",
    "overmind-evaluations",
    "overmind-optimiser",
    "model-training-engineering",
    "overmind-training",
    "overmind-inference",
    "overmind-integrations",
)


def _init(tmp_path: Path, ide: str, *extra: str) -> None:
    result = runner.invoke(
        app,
        ["init", "--ide", ide, "--env", "local", *extra],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    ("ide", "skill_dir"),
    [
        ("cursor", ".agents"),
        ("claude", ".claude"),
        ("opencode", ".opencode"),
        ("codex", ".agents"),
    ],
)
@pytest.mark.parametrize("existing", [False, True], ids=["fresh", "refresh"])
def test_init_delivers_current_onboarding_workflow(tmp_path, monkeypatch, ide, skill_dir, existing):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    monkeypatch.delenv("OVERMIND_BASE_URL", raising=False)
    for name in ("setup", "onboard"):
        installed = tmp_path / skill_dir / "skills" / "overmind" / "references" / f"{name}.md"
        if existing:
            installed.parent.mkdir(parents=True, exist_ok=True)
            installed.write_text("Outdated workflow\n")

    progress = Path("references/onboarding-progress.md")
    if existing:
        (tmp_path / skill_dir / "skills" / "overmind" / progress).write_text("Outdated progress\n")

    if existing:
        for slug in SURFACE_SKILLS:
            installed = tmp_path / skill_dir / "skills" / slug
            installed.mkdir(parents=True, exist_ok=True)
            (installed / "SKILL.md").write_text("Outdated surface workflow\n")

    result = runner.invoke(app, ["init", "--ide", ide, "--env", "production"], catch_exceptions=False)
    assert result.exit_code == 0, result.output

    for name in ("setup", "onboard"):
        source = (SKILL_SOURCE / "references" / f"{name}.md").read_text()
        installed = tmp_path / skill_dir / "skills" / "overmind" / "references" / f"{name}.md"
        assert installed.read_text() == source
    assert (tmp_path / skill_dir / "skills" / "overmind" / progress).read_text() == (
        SKILL_SOURCE / progress
    ).read_text()
    for slug in SURFACE_SKILLS:
        source = SKILL_SOURCE.parent / slug
        installed = tmp_path / skill_dir / "skills" / slug
        assert (installed / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
        assert (installed / "agents/openai.yaml").read_bytes() == (source / "agents/openai.yaml").read_bytes()


def test_init_installs_cursor_skills_without_writing_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "cursor")
    assert not (tmp_path / ".cursor" / "mcp.json").exists()
    assert (tmp_path / ".agents" / "skills" / "overmind" / "SKILL.md").is_file()
    assert (tmp_path / ".agents" / "skills" / "overmind" / "references" / "setup.md").is_file()


def test_cursor_init_replaces_previous_skill_installs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / ".cursor" / "skills" / "overmind"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("Outdated\n")
    own = tmp_path / ".cursor" / "skills" / "team-release"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("Ours\n")
    removed = tmp_path / ".agents" / "skills" / "overmind" / "references" / "retired.md"
    removed.parent.mkdir(parents=True)
    removed.write_text("No longer shipped\n")

    _init(tmp_path, "cursor")

    assert not legacy.exists()
    assert (own / "SKILL.md").read_text() == "Ours\n"
    assert not removed.exists()
    assert (tmp_path / ".agents" / "skills" / "overmind" / "SKILL.md").is_file()


def test_codex_local_init_without_url_override(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for name in ("OVERMIND_API_URL", "OVERMIND_BASE_URL", "OVERMIND_PROJECT_ID"):
        monkeypatch.delenv(name, raising=False)
    _init(tmp_path, "codex")
    assert not (tmp_path / ".codex" / "config.toml").exists()
    assert 'base-url = "http://localhost:8000"' in (tmp_path / "overmind.toml").read_text()


def test_init_seeds_toml_without_slash_commands(tmp_path, monkeypatch):
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

    assert not list((tmp_path / ".cursor" / "commands").glob("*.md"))
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


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


def test_init_claude_installs_skills_without_writing_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
    before = claude_config_path().read_text() if claude_config_path().exists() else None

    for alias in ("claude", "claude_code", "claude-code"):
        _init(tmp_path, alias)

        after = claude_config_path().read_text() if claude_config_path().exists() else None
        assert after == before
        assert not (tmp_path / ".mcp.json").exists()
        assert not (tmp_path / ".claude" / "commands").exists()
        assert (tmp_path / ".claude" / "skills" / "overmind" / "SKILL.md").is_file()
        assert (tmp_path / "overmind.toml").is_file()


def test_init_claude_preserves_other_config_and_tracked_mcp_json(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = json.dumps({"mcpServers": {"other": {"command": "uvx", "args": ["other"]}}})
    (tmp_path / ".mcp.json").write_text(tracked)
    subprocess.run(["git", "add", ".mcp.json"], cwd=tmp_path, check=True)
    claude_config_path().write_text(
        json.dumps({"theme": "dark", "projects": {"/elsewhere": {"mcpServers": {"x": {"type": "http", "url": "u"}}}}})
    )

    _init(tmp_path, "claude")

    config = json.loads(claude_config_path().read_text())
    assert config["theme"] == "dark"
    assert config["projects"]["/elsewhere"]["mcpServers"] == {"x": {"type": "http", "url": "u"}}
    assert tmp_path.resolve().as_posix() not in config["projects"]
    assert (tmp_path / ".mcp.json").read_text() == tracked


def test_init_keeps_bootstrap_key_out_of_local_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "secret")
    claude = claude_config_path()
    before = claude.read_text() if claude.exists() else None
    for ide in ("cursor", "claude", "opencode", "codex"):
        result = runner.invoke(app, ["init", "--ide", ide], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    for path in (
        tmp_path / ".cursor" / "mcp.json",
        tmp_path / "opencode.json",
        tmp_path / ".codex" / "config.toml",
    ):
        assert not path.exists()
    assert "secret" not in (tmp_path / "overmind.toml").read_text()
    after = claude.read_text() if claude.exists() else None
    assert after == before
    assert not (tmp_path / ".overmind" / "credentials.toml").exists()


def test_init_opencode_installs_skill_without_writing_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init(tmp_path, "opencode")
    assert not (tmp_path / "opencode.json").exists()
    assert (tmp_path / ".opencode" / "skills" / "overmind" / "SKILL.md").is_file()
    assert not (tmp_path / ".opencode" / "commands").exists()


def test_init_codex_installs_skill_without_writing_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "k")
    result = runner.invoke(app, ["init", "--ide", "codex", "--env", "production"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert not (tmp_path / ".codex" / "config.toml").exists()
    assert (tmp_path / ".agents" / "skills" / "overmind" / "SKILL.md").is_file()
    assert (tmp_path / "overmind.toml").is_file()
    assert not (tmp_path / ".codex" / "commands").exists()


def test_init_leaves_existing_codex_config_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OVERMIND_API_KEY", "k")
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = (
        'model = "gpt-5"\n\n'
        '[mcp_servers.overmind]\nurl = "https://old.example"\n\n'
        '[mcp_servers.other]\nurl = "https://other.example"\n'
    )
    config_path.write_text(original)

    result = runner.invoke(app, ["init", "--ide", "codex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert config_path.read_text() == original


def test_init_does_not_copy_saved_project_key_into_mcp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    monkeypatch.delenv("OVERMIND_BASE_URL", raising=False)
    before = claude_config_path().read_text() if claude_config_path().exists() else None
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
    after = claude_config_path().read_text() if claude_config_path().exists() else None
    assert after == before
    assert "ovr_project_key" not in (after or "")


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
    assert not (tmp_path / ".codex" / "config.toml").exists()
    assert "legacy-bootstrap" in (tmp_path / "overmind.toml").read_text()
    assert not (tmp_path / ".overmind" / "credentials.toml").exists()


def test_init_leaves_tracked_mcp_config_unchanged(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    original = '[mcp_servers.other]\ncommand = "other"\n'
    config_path.write_text(original)
    subprocess.run(["git", "add", ".codex/config.toml"], cwd=tmp_path, check=True)

    result = runner.invoke(app, ["init", "--ide", "codex"], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert config_path.read_text() == original


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
    assert not (tmp_path / ".cursor" / "mcp.json").exists()
    assert 'base-url = "http://127.0.0.1:9000"' in (tmp_path / "overmind.toml").read_text()


def test_init_requires_ide(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--env", "local"])
    assert result.exit_code != 0
    assert "ide" in result.output.lower()
