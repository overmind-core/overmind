import hashlib
import json

import pytest
import requests
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.dataset_cmd import upload_file


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status
        self.ok = 200 <= status < 300
        self.headers = {}

    def json(self):
        return self.data


class TransferServer:
    def __init__(self):
        self.headers = {}
        self.content = bytearray()
        self.receipt = None
        self.publications = 0
        self.lose_completion = False
        self.calls = []

    def request(self, method, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.calls.append((method, url, kwargs))
        if url.endswith("/api/mcp/"):
            request = kwargs["json"]
            method = request["method"]
            if method == "initialize":
                return Response({"result": {"protocolVersion": "2025-06-18", "serverInfo": {"name": "overmind"}}})
            if method == "notifications/initialized":
                return Response({}, 202)
            if method == "tools/call":
                return Response({
                    "result": {"structuredContent": {"projects": [{"id": "project-1"}], "next_offset": None}}
                })
            return Response({
                "result": {
                    "contents": [
                        {
                            "text": json.dumps({
                                "contract_version": "4.1.0",
                                "transfer_protocol_version": 1,
                                "catalog_sha256": "catalog",
                                "connection": {"mcp_url": "http://localhost:8000/api/mcp/"},
                            })
                        }
                    ]
                }
            })
        if "/readiness/" in url:
            return Response({
                "protocol_version": 1,
                "credential_scope": "account",
                "project_id": "project-1",
                "user_id": "user-1",
                "can_upload": True,
                "can_export": True,
                "mcp_url": "http://localhost:8000/api/mcp/",
            })
        if method == "POST" and url.endswith("/api/dataset-transfers/"):
            spec = kwargs["json"]
            if self.receipt is None:
                self.receipt = {
                    "id": "transfer-1",
                    "state": "uploading",
                    "received": 0,
                    "size": spec["size"],
                    "sha256": spec["sha256"],
                    "request_key": spec["request_key"],
                    "chunk_bytes": 8,
                    "max_bytes": 1000,
                    "result": {},
                }
            elif spec["sha256"] != self.receipt["sha256"]:
                return Response({"code": "request_key_conflict", "detail": "Different file"}, 409)
            return Response(dict(self.receipt), 201)
        if method == "PUT":
            assert kwargs["params"]["offset"] == len(self.content)
            self.content.extend(kwargs["data"])
            self.receipt["received"] = len(self.content)
        if url.endswith("/complete/"):
            if self.receipt["state"] != "published":
                assert hashlib.sha256(self.content).hexdigest() == self.receipt["sha256"]
                self.publications += 1
                self.receipt.update(state="published", result={"id": "dataset-1", "state": "landing"})
            if self.lose_completion:
                self.lose_completion = False
                raise requests.ConnectionError("response lost secret-key")
        return Response(dict(self.receipt))

    def close(self):
        pass


def test_command_restart_recovers_published_transfer_without_duplicate(tmp_path):
    path = tmp_path / "rows.json"
    path.write_text('[{"input":"one"},{"input":"two"}]')
    server = TransferServer()
    server.lose_completion = True
    params = dict(project_id="project-1", api_key="secret-key", api_url="http://localhost:8000", session=server)
    with pytest.raises(Exception) as failed:
        upload_file(path, **params)
    assert "secret-key" not in str(failed.value)
    result = upload_file(path, **params)
    assert result["id"] == "dataset-1"
    assert result["transfer"]["id"] == "transfer-1"
    assert result["state_scope"] == "at_publication"
    assert server.publications == 1
    assert bytes(server.content) == path.read_bytes()
    assert result["next_mcp_actions"][0]["arguments"]["project_id"] == "project-1"


def test_recovered_upload_does_not_present_saved_landing_state_as_current(tmp_path, monkeypatch):
    path = tmp_path / "rows.json"
    path.write_text('[{"text":"retained"}]')
    server = TransferServer()
    monkeypatch.setattr(requests, "Session", lambda: server)
    argv = [
        "dataset",
        "upload",
        str(path),
        "--project-id",
        "project-1",
        "--api-key",
        "secret-key",
        "--api-url",
        "http://localhost:8000",
    ]
    first = CliRunner().invoke(app, argv)
    assert first.exit_code == 0, first.output
    recovered = CliRunner().invoke(app, argv)
    assert recovered.exit_code == 0, recovered.output
    assert "is landing" not in recovered.output
    assert "get_job" in recovered.output
    assert server.publications == 1


@pytest.mark.parametrize("code", ["file_too_large", ["file_too_large"]])
def test_document_limit_has_actionable_safe_error_without_a_transfer(tmp_path, code):
    path = tmp_path / "large.pdf"
    path.write_bytes(b"PDF fixture")
    server = TransferServer()
    request = server.request

    def reject(method, url, **kwargs):
        if method == "POST" and url.endswith("/api/dataset-transfers/"):
            return Response({"code": code, "detail": "untrusted secret-key"}, 400)
        return request(method, url, **kwargs)

    server.request = reject
    with pytest.raises(Exception) as failure:
        upload_file(path, project_id="project-1", api_key="secret-key", api_url="http://localhost:8000", session=server)
    record = failure.value.record()
    assert record["code"] == "file_too_large"
    assert record["next_action"] == "split_or_reduce_file"
    assert "secret-key" not in json.dumps(record)
    assert server.receipt is None and server.publications == 0


def test_readiness_reports_denied_transport_without_mutation_or_secrets(monkeypatch):
    server = TransferServer()

    def denied(method, url, **kwargs):
        raise requests.ConnectionError("private path and secret-key") from PermissionError(1, "Operation not permitted")

    server.request = denied
    monkeypatch.setattr(requests, "Session", lambda: server)
    result = CliRunner().invoke(
        app,
        [
            "connection",
            "check",
            "--api-key",
            "secret-key",
            "--api-url",
            "http://localhost:8000",
            "--project-id",
            "project-1",
            "--json",
        ],
    )
    assert result.exit_code == 1, result.output
    body = json.loads(result.output)
    assert not body["ready"]
    assert body["error"]["code"] == "network_permission_denied"
    assert body["error"]["next_action"] == "request_host_network_permission"
    assert "secret-key" not in result.output and "private path" not in result.output
    assert server.publications == 0


def test_readiness_checks_both_transports_and_project_from_this_process(monkeypatch):
    server = TransferServer()
    monkeypatch.setattr(requests, "Session", lambda: server)
    result = CliRunner().invoke(
        app,
        [
            "connection",
            "check",
            "--api-key",
            "secret-key",
            "--api-url",
            "http://localhost:8000",
            "--project-id",
            "project-1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["ready"] and body["project_id"] == "project-1"
    assert body["mcp"]["status"] == body["transfer"]["status"] == "ready"
    assert not any(url.endswith("/api/dataset-transfers/") for _, url, _ in server.calls)


@pytest.mark.parametrize(
    "status,code", [(401, "authentication_failed"), (403, "permission_denied"), (302, "redirect_refused")]
)
def test_readiness_preserves_auth_permission_and_redirect_failures(monkeypatch, status, code):
    server = TransferServer()
    server.request = lambda *args, **kwargs: Response({"detail": "secret-key"}, status)
    monkeypatch.setattr(requests, "Session", lambda: server)
    result = CliRunner().invoke(
        app,
        [
            "connection",
            "check",
            "--api-key",
            "secret-key",
            "--api-url",
            "http://localhost:8000",
            "--project-id",
            "project-1",
            "--json",
        ],
    )
    body = json.loads(result.output)
    assert result.exit_code == 1
    assert body["error"]["code"] == code
    assert "secret-key" not in result.output


def test_global_setup_uses_one_connection_without_pinning_a_project(tmp_path, monkeypatch):
    from pathlib import Path

    import tomllib

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("OVERMIND_API_KEY", "secret-key")
    server = TransferServer()
    monkeypatch.setattr(requests, "Session", lambda: server)
    result = CliRunner().invoke(
        app, ["connection", "configure", "--api-url", "http://localhost:8000", "--project-id", "project-1", "--json"]
    )
    assert result.exit_code == 0, result.output
    saved = tmp_path / "config/overmind/connection.toml"
    mcp = tomllib.loads((tmp_path / ".codex/config.toml").read_text())["mcp_servers"]["overmind"]
    profile = tomllib.loads(saved.read_text())
    assert profile["api-key"] == mcp["http_headers"]["X-Api-Key"] == "secret-key"
    assert profile["base-url"] + "/api/mcp/" == mcp["url"]
    assert "project-id" not in profile
    assert saved.stat().st_mode & 0o777 == 0o600
    assert "secret-key" not in result.output
    assert json.loads(result.output)["mcp_client_reload_required"] is True


def test_changed_bytes_under_an_explicit_key_report_the_conflict(tmp_path):
    from overmind.transfer_connection import TransferError

    path = tmp_path / "rows.json"
    path.write_text('[{"input":"one"}]')
    server = TransferServer()
    params = dict(
        project_id="project-1",
        api_key="secret-key",
        api_url="http://localhost:8000",
        request_key="fixed",
        session=server,
    )
    upload_file(path, **params)
    path.write_text('[{"input":"two"}]')
    with pytest.raises(TransferError) as failure:
        upload_file(path, **params)
    assert failure.value.code == "request_key_conflict"
    assert failure.value.next_action == "use_original_inputs_or_new_request_key"
    assert server.publications == 1
