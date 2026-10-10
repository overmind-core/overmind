import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app


@pytest.fixture
def transfer_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    profile = Path(os.environ["XDG_CONFIG_HOME"]) / "overmind" / "connection.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text('api-key = "saved-secret"\nbase-url = "http://localhost:8000"\n')
    profile.chmod(0o600)
    (tmp_path / "rows.csv").write_text("question,answer\none,two\n")
    return profile


@pytest.mark.parametrize("operation", ["upload", "export"])
def test_transfer_uses_saved_account_connection_without_repository_or_environment(
    transfer_workspace, monkeypatch, operation
):
    calls = []

    def transfer(*args, **kwargs):
        calls.append(kwargs)
        return {"id": "dataset-id"}

    monkeypatch.setattr(
        f"overmind.dataset_cmd.{'upload_file' if operation == 'upload' else 'export_dataset'}", transfer
    )
    arguments = ["dataset", operation, "rows.csv" if operation == "upload" else "dataset-id", "--json"]
    if operation == "upload":
        arguments += ["--project-id", "chosen-project"]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    assert calls[0]["api_key"] == "saved-secret"
    assert calls[0]["api_url"] == "http://localhost:8000"
    if operation == "upload":
        assert calls[0]["project_id"] == "chosen-project"
    assert "saved-secret" not in result.output


@pytest.mark.parametrize("source", ["argument", "environment", "repository"])
def test_saved_credential_is_never_sent_to_a_different_api(transfer_workspace, monkeypatch, source):
    calls = []
    monkeypatch.setattr("overmind.dataset_cmd.upload_file", lambda *a, **kw: calls.append(kw))
    arguments = ["dataset", "upload", "rows.csv", "--project-id", "chosen-project", "--json"]
    if source == "argument":
        arguments += ["--api-url", "https://other.example"]
    elif source == "environment":
        monkeypatch.setenv("OVERMIND_API_URL", "https://other.example")
    else:
        Path("overmind.toml").write_text('base-url = "https://other.example"\nproject-id = "chosen-project"\n')
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 1
    assert not calls
    assert "different API" in json.loads(result.output)["error"]
    assert "saved-secret" not in result.output


@pytest.mark.parametrize(
    "contents",
    [
        'api-key = "saved-secret"\n',
        'api-key = "saved-secret"\nbase-url = "https://user:pass@other.example"\n',
        'api-key = "saved-secret"\nbase-url = "http://localhost:8000?token=secret"\n',
        'api-key = ["saved-secret"]\nbase-url = "http://localhost:8000"\n',
        'api-key = "saved-secret',
    ],
)
def test_invalid_saved_connection_fails_without_network_or_secret_disclosure(transfer_workspace, monkeypatch, contents):
    transfer_workspace.write_text(contents)
    calls = []
    monkeypatch.setattr("overmind.dataset_cmd.upload_file", lambda *a, **kw: calls.append(kw))
    result = CliRunner().invoke(app, ["dataset", "upload", "rows.csv", "--project-id", "chosen", "--json"])
    assert result.exit_code == 1
    assert not calls
    assert json.loads(result.output)["error"]
    assert "saved-secret" not in result.output


def test_explicit_connection_does_not_read_or_use_saved_credentials(transfer_workspace, monkeypatch):
    transfer_workspace.write_text("invalid TOML")
    calls = []
    monkeypatch.setattr("overmind.dataset_cmd.upload_file", lambda *a, **kw: calls.append(kw) or {"id": "created"})
    result = CliRunner().invoke(
        app,
        [
            "dataset",
            "upload",
            "rows.csv",
            "--project-id",
            "chosen",
            "--api-key",
            "explicit",
            "--api-url",
            "https://other.example",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls[0]["api_key"] == "explicit"
    assert calls[0]["api_url"] == "https://other.example"


@pytest.mark.parametrize("operation", ["check", "upload", "export"])
@pytest.mark.parametrize("override", ["argument", "environment", "repeated_key"])
def test_repository_credential_cannot_follow_an_endpoint_override(transfer_workspace, monkeypatch, operation, override):
    Path("overmind.toml").write_text('base-url = "https://hosted.example"\nproject-id = "hosted-project"\n')
    credentials = Path(".overmind/credentials.toml")
    credentials.parent.mkdir()
    credentials.write_text(
        'api-key = "hosted-secret"\nbase-url = "https://hosted.example"\nproject-id = "hosted-project"\n'
    )
    credentials.chmod(0o600)
    requests = []

    def reject_network(*args, **kwargs):
        requests.append(kwargs)
        raise AssertionError("Endpoint conflicts must be rejected before contacting any server")

    monkeypatch.setattr("requests.Session.request", reject_network)
    if operation == "check":
        arguments = ["connection", "check", "--project-id", "chosen-project", "--json"]
    else:
        arguments = ["dataset", operation, "rows.csv" if operation == "upload" else "dataset-id", "--json"]
        if operation == "upload":
            arguments += ["--project-id", "chosen-project"]
    if override == "environment":
        monkeypatch.setenv("OVERMIND_API_URL", "http://localhost:8000")
    else:
        arguments += ["--api-url", "http://localhost:8000"]
        if override == "repeated_key":
            arguments += ["--api-key", "hosted-secret"]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 1
    assert not requests
    assert "different API" in result.output
    assert "hosted-secret" not in result.output
    assert "saved-secret" not in result.output


@pytest.mark.skipif(os.name == "nt", reason="POSIX credential permissions")
def test_world_readable_saved_credential_is_rejected(transfer_workspace, monkeypatch):
    transfer_workspace.chmod(0o644)
    calls = []
    monkeypatch.setattr("overmind.dataset_cmd.upload_file", lambda *a, **kw: calls.append(kw))
    result = CliRunner().invoke(app, ["dataset", "upload", "rows.csv", "--project-id", "chosen", "--json"])
    assert result.exit_code == 1
    assert not calls
    assert "0600" in json.loads(result.output)["error"]
