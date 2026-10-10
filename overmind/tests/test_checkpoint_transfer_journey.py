import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump


@pytest.fixture
def checkpoint_host(tmp_path, monkeypatch):
    observed = []
    mode = {"redirect": None}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.path, self.headers.get("X-Api-Key")))
            metadata = self.path.endswith("/checkpoints/")
            stage = "metadata" if metadata else "artifact"
            if mode["redirect"] == stage and self.path != "/redirected":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{self.server.server_port}/redirected")
                self.end_headers()
                return
            body = (
                json.dumps({
                    "files": [{"name": "weights.zip", "size_bytes": 7, "download_url": f"{base}/artifact"}]
                }).encode()
                if metadata
                else b"weights"
            )
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    host = HTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{host.server_port}"
    worker = Thread(target=host.serve_forever, daemon=True)
    worker.start()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "account"))
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)
    monkeypatch.delenv("OVERMIND_API_URL", raising=False)
    profile = tmp_path / "account" / "overmind" / "connection.toml"
    profile.parent.mkdir(parents=True)
    profile.write_text(f'api-key = "local-test-account"\nbase-url = "{base}"\n')
    profile.chmod(0o600)
    try:
        yield base, observed, mode, profile
    finally:
        host.shutdown()
        host.server_close()
        worker.join(timeout=2)


def run_download(*args):
    return CliRunner().invoke(app, ["model", "download-checkpoint", "deployment", "--json", *args])


def test_repository_free_download_uses_address_bound_account_without_forwarding_key(checkpoint_host, tmp_path):
    _, observed, _, _ = checkpoint_host
    result = run_download()
    assert result.exit_code == 0, result.output
    assert (tmp_path / "weights.zip").read_bytes() == b"weights"
    assert json.loads(result.output)["bytes_written"] == 7
    assert observed == [("/api/deployed-models/deployment/checkpoints/", "local-test-account"), ("/artifact", None)]
    assert "local-test-account" not in result.output


@pytest.mark.parametrize("credentials", ["saved", "repository"])
def test_checkpoint_endpoint_override_cannot_move_a_bound_key(checkpoint_host, credentials):
    base, observed, _, _ = checkpoint_host
    if credentials == "repository":
        dump(
            Config(api_key="repository-test-key", base_url=base, project_id="project"),
            __import__("pathlib").Path("overmind.toml"),
        )
    result = run_download("--api-url", "http://localhost:1")
    assert result.exit_code == 1
    assert "different API" in result.output
    assert observed == []


@pytest.mark.parametrize("stage", ["metadata", "artifact"])
def test_checkpoint_redirects_are_refused_without_following_or_retaining_bytes(checkpoint_host, tmp_path, stage):
    base, observed, mode, _ = checkpoint_host
    mode["redirect"] = stage
    result = run_download("--api-url", base, "--api-key", "explicit-test-key")
    assert result.exit_code == 1
    assert "HTTP 302" in result.output
    assert not any(path == "/redirected" for path, _ in observed)
    assert not (tmp_path / "weights.zip").exists()
    assert "explicit-test-key" not in result.output
