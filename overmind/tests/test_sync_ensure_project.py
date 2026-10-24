"""Unit tests for account-scoped project minting in ``overmind sync``."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from overmind.config import Config, dump, load
from overmind.init_cmd import SUPPORTED_IDES, claude_config_path, write_mcp_config
from overmind.sync import (
    SyncError,
    _api_key_scope,
    _slugify,
    ensure_mcp_project_key,
    ensure_project_id,
    resolve_api_key,
    run_down,
    run_up,
)


def test_slugify_strips_and_caps():
    assert _slugify(" Hello World!! ") == "hello-world"
    assert _slugify("") == "project"


def test_ensure_project_id_noop_when_present(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    cfg = Config(api_key="k", project_id="already-set", repo_summary="bot")
    dump(cfg, path)
    out, created = ensure_project_id(cfg, path, "k", "https://api.example")
    assert out.project_id == "already-set"
    assert created is False


def test_ensure_project_id_falls_back_to_directory_name(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    cfg = Config(
        api_key="k",
        project_id="",
        repo_summary="A long paragraph that must not become the console project name.",
    )
    dump(cfg, path)

    with patch("overmind.sync.create_project", return_value="11111111-1111-1111-1111-111111111111") as create:
        ensure_project_id(cfg, path, "k", "https://api.example")

    assert create.call_args.kwargs["name"] == tmp_path.name
    assert load(path).project_name == tmp_path.name


def test_ensure_project_id_surfaces_project_scoped_refusal(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    cfg = Config(api_key="k", project_id="")
    dump(cfg, path)

    fake = MagicMock()
    fake.status_code = 403
    fake.ok = False
    fake.json.return_value = {"detail": "Project-scoped API keys cannot create projects."}
    fake.text = "forbidden"

    with patch("overmind.sync._session") as session_factory:
        session_factory.return_value.post.return_value = fake
        with pytest.raises(SyncError, match="account-scoped"):
            ensure_project_id(cfg, path, "k", "https://api.example")


def test_api_key_scope_reads_current_endpoint():
    fake = MagicMock()
    fake.ok = True
    fake.json.return_value = {
        "scope": "account",
        "permission": ["read", "write"],
    }

    with patch("overmind.sync._session") as session_factory:
        session_factory.return_value.get.return_value = fake
        assert _api_key_scope("acct", "https://api.example") == "account"


def test_ensure_mcp_project_key_swaps_account_scoped_key(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    project_id = "11111111-1111-1111-1111-111111111111"
    cfg = Config(api_key="acct", project_id=project_id, project_name="demo")
    dump(cfg, path)

    with (
        patch("overmind.sync.mint_project_api_key", return_value="ovr_project_key") as mint,
    ):
        out, key = ensure_mcp_project_key(
            cfg,
            path,
            "acct",
            "https://api.example",
            key_scope="account",
        )

    mint.assert_called_once_with("https://api.example", "acct", project_id)
    assert key == "ovr_project_key"
    assert out.api_key == "ovr_project_key"
    assert load(path).api_key == "ovr_project_key"
    assert "api-key" not in path.read_text()


def test_ensure_mcp_project_key_noop_for_project_scoped_key(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    cfg = Config(api_key="already-project", project_id="11111111-1111-1111-1111-111111111111")
    dump(cfg, path)

    out, key = ensure_mcp_project_key(
        cfg,
        path,
        "already-project",
        "https://api.example",
        key_scope="project",
    )

    assert key == "already-project"
    assert out.api_key == "already-project"
    assert load(path).api_key == "already-project"


def test_sync_saves_project_key_without_touching_oauth_mcp_configs(tmp_path: Path, monkeypatch, capsys):
    path = tmp_path / "overmind.toml"
    project_id = "11111111-1111-1111-1111-111111111111"
    dump(Config(project_id=project_id, project_name="demo"), path)
    configs = [write_mcp_config(ide, tmp_path, "https://api.example/api/mcp/") for ide in SUPPORTED_IDES]
    before = [config.read_text() for config in configs]

    with (
        patch("overmind.sync._api_key_scope", return_value="account"),
        patch("overmind.sync.post_snapshot", return_value={"project_id": project_id, "capabilities": []}),
        patch("overmind.sync.mint_project_api_key", return_value="ovr_project_key"),
    ):
        result = run_up(path, "bootstrap", "https://api.example")

    assert result.api_key == "ovr_project_key"
    assert "ovr_project_key" not in capsys.readouterr().out
    assert "api-key" not in path.read_text()
    assert [config.read_text() for config in configs] == before

    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
    assert resolve_api_key("", load(path)) == "ovr_project_key"


def test_saved_project_key_wins_over_stale_bootstrap_environment(tmp_path: Path, monkeypatch):
    path = tmp_path / "overmind.toml"
    dump(
        Config(
            api_key="ovr_project_key",
            project_id="11111111-1111-1111-1111-111111111111",
        ),
        path,
    )
    monkeypatch.setenv("OVERMIND_API_KEY", "stale-account-key")

    assert resolve_api_key("", load(path)) == "ovr_project_key"
    assert resolve_api_key("replacement", load(path)) == "replacement"


@pytest.mark.parametrize("sync", [run_up, run_down])
@pytest.mark.parametrize("explicit_key", ["", "saved-project-key"])
def test_sync_rejects_saved_key_on_another_endpoint(tmp_path, monkeypatch, sync, explicit_key):
    path = tmp_path / "overmind.toml"
    dump(Config(api_key="saved-project-key", project_id="project", base_url="https://staging.example"), path)
    monkeypatch.setenv("OVERMIND_API_URL", "http://localhost:8000")
    with patch("overmind.sync._session") as session:
        with pytest.raises(SyncError, match="No request was sent"):
            sync(path, explicit_key, "")
        session.assert_not_called()


def test_explicit_saved_endpoint_overrides_inherited_url(tmp_path, monkeypatch):
    path = tmp_path / "overmind.toml"
    dump(Config(api_key="saved-project-key", project_id="project", base_url="https://staging.example"), path)
    monkeypatch.setenv("OVERMIND_API_URL", "http://localhost:8000")
    with patch("overmind.sync.get_snapshot", return_value={"capabilities": []}) as fetch:
        run_down(path, "", "https://staging.example")
    fetch.assert_called_once_with("https://staging.example", "saved-project-key", "project")


def test_setup_sync_reuses_saved_project_key_without_minting_another(tmp_path: Path, monkeypatch):
    path = tmp_path / "overmind.toml"
    project_id = "11111111-1111-1111-1111-111111111111"
    dump(Config(project_id=project_id, project_name="demo"), path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)

    with (
        patch("overmind.sync._api_key_scope", side_effect=["account", "project"]) as scope,
        patch("overmind.sync.post_snapshot", return_value={"project_id": project_id, "capabilities": []}),
        patch("overmind.sync.mint_project_api_key", return_value="ovr_project_key") as mint,
    ):
        run_up(path, "bootstrap", "https://api.example")

        config = load(path)
        assert config.base_url == "https://api.example"
        config.repo_summary = "Discovered during setup"
        dump(config, path)
        result = run_up(path, "", "https://api.example")

    assert result.api_key == "ovr_project_key"
    assert scope.call_args_list[0].args[0] == "bootstrap"
    assert scope.call_args_list[1].args[0] == "ovr_project_key"
    mint.assert_called_once_with("https://api.example", "bootstrap", project_id)


def test_sync_does_not_add_overmind_to_uninitialized_ide_config(tmp_path: Path):
    path = tmp_path / "overmind.toml"
    project_id = "11111111-1111-1111-1111-111111111111"
    dump(Config(project_id=project_id, project_name="demo"), path)
    unrelated = {"mcpServers": {"other": {"command": "other"}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(unrelated))
    claude_config_path().write_text(json.dumps({"projects": {tmp_path.resolve().as_posix(): unrelated}}))

    with (
        patch("overmind.sync._api_key_scope", return_value="account"),
        patch("overmind.sync.post_snapshot", return_value={"project_id": project_id, "capabilities": []}),
        patch("overmind.sync.mint_project_api_key", return_value="ovr_project_key"),
    ):
        run_up(path, "bootstrap", "https://api.example")

    assert json.loads((tmp_path / ".mcp.json").read_text()) == unrelated
    assert json.loads(claude_config_path().read_text()) == {"projects": {tmp_path.resolve().as_posix(): unrelated}}
