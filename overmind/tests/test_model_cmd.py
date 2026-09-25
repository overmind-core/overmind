from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import requests
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump
from overmind.model_cmd import CheckpointDownloadError, download_checkpoint


class FakeResponse:
    def __init__(self, payload=None, status_code: int = 200, *, chunks=None):
        self.payload = payload
        self.status_code = status_code
        self.chunks = chunks or []
        self.closed = False

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload

    def iter_content(self, chunk_size):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, metadata, artifact):
        self.metadata = metadata
        self.artifact = artifact
        self.calls: list[tuple[str, str, dict]] = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.metadata if len(self.calls) == 1 else self.artifact

    def close(self):
        self.closed = True


def test_download_checkpoint_streams_metadata_and_artifact(tmp_path: Path):
    url = "https://storage.example/checkpoint.zip?signature=secret-url"
    metadata = FakeResponse({"files": [{"name": "checkpoint.zip", "size_bytes": 10, "download_url": url}]})
    artifact = FakeResponse(chunks=[b"012", b"", b"3456789"])
    session = FakeSession(metadata, artifact)
    output = tmp_path / "model.zip"

    result = download_checkpoint(
        "deployment-1",
        output=output,
        api_key="api-secret",
        api_url="https://api.example/",
        session=session,
    )

    assert result == {
        "path": str(output),
        "deployment_id": "deployment-1",
        "filename": "checkpoint.zip",
        "expected_size": 10,
        "bytes_written": 10,
    }
    assert output.read_bytes() == b"0123456789"
    assert session.calls[0][1] == "https://api.example/api/deployed-models/deployment-1/checkpoints/"
    assert session.calls[0][2]["headers"] == {"X-Api-Key": "api-secret"}
    assert session.calls[1][1] == url
    assert session.calls[1][2] == {"timeout": 120, "stream": True}
    assert metadata.closed is True
    assert artifact.closed is True


def test_download_checkpoint_sanitizes_filename_and_refuses_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    metadata = FakeResponse({
        "files": [{"name": r"..\nested/../checkpoint.zip", "size_bytes": None, "download_url": "s3-url"}]
    })
    artifact = FakeResponse(chunks=[b"weights"])

    result = download_checkpoint(
        "deployment-1",
        output=None,
        api_key="key",
        api_url="https://api.example",
        session=FakeSession(metadata, artifact),
    )

    assert result["path"] == "checkpoint.zip"
    assert result["filename"] == "checkpoint.zip"
    assert "expected_size" not in result
    assert Path("checkpoint.zip").read_bytes() == b"weights"

    with pytest.raises(CheckpointDownloadError, match="already exists"):
        download_checkpoint(
            "deployment-1",
            output=Path("checkpoint.zip"),
            api_key="key",
            api_url="https://api.example",
            session=FakeSession(metadata, artifact),
        )


def test_download_checkpoint_sanitizes_windows_reserved_filename(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    metadata = FakeResponse({"files": [{"name": "CON.zip", "size_bytes": 7, "download_url": "s3-url"}]})

    result = download_checkpoint(
        "deployment-1",
        output=None,
        api_key="key",
        api_url="https://api.example",
        session=FakeSession(metadata, FakeResponse(chunks=[b"weights"])),
    )

    assert result["path"] == "_CON.zip"
    assert Path("_CON.zip").read_bytes() == b"weights"


def test_download_checkpoint_removes_partial_file_after_stream_error(tmp_path: Path):
    class FailingResponse(FakeResponse):
        def iter_content(self, chunk_size):
            yield b"partial"
            raise requests.ConnectionError("presigned-url-must-not-leak")

    output = tmp_path / "checkpoint.zip"
    session = FakeSession(
        FakeResponse({"files": [{"name": "checkpoint.zip", "download_url": "presigned-url"}]}),
        FailingResponse(),
    )

    with pytest.raises(CheckpointDownloadError, match="artifact download failed") as exc_info:
        download_checkpoint(
            "deployment-1",
            output=output,
            api_key="key",
            api_url="https://api.example",
            session=session,
        )

    assert "presigned-url" not in str(exc_info.value)
    assert not output.exists()


@pytest.mark.parametrize(
    ("expected_size", "chunks"),
    [(10, [b"short"]), (3, [b"longer"])],
)
def test_download_checkpoint_rejects_artifact_size_mismatch_and_removes_file(
    tmp_path: Path, expected_size: int, chunks: list[bytes]
):
    output = tmp_path / "checkpoint.zip"
    session = FakeSession(
        FakeResponse({"files": [{"name": "checkpoint.zip", "size_bytes": expected_size, "download_url": "s3-url"}]}),
        FakeResponse(chunks=chunks),
    )

    with pytest.raises(CheckpointDownloadError, match="artifact size mismatch"):
        download_checkpoint(
            "deployment-1",
            output=output,
            api_key="key",
            api_url="https://api.example",
            session=session,
        )

    assert not output.exists()


@pytest.mark.parametrize(
    ("metadata_status", "artifact_status", "message"),
    [
        (404, 200, "metadata request failed (HTTP 404)"),
        (200, 503, "artifact request failed (HTTP 503)"),
    ],
)
def test_download_checkpoint_reports_api_and_artifact_errors(
    tmp_path: Path, metadata_status: int, artifact_status: int, message: str
):
    session = FakeSession(
        FakeResponse({"files": [{"name": "checkpoint.zip", "download_url": "presigned-url"}]}, metadata_status),
        FakeResponse({}, artifact_status),
    )

    with pytest.raises(CheckpointDownloadError, match=re.escape(message)) as exc_info:
        download_checkpoint(
            "deployment-1",
            output=tmp_path / "checkpoint.zip",
            api_key="key",
            api_url="https://api.example",
            session=session,
        )

    assert "presigned-url" not in str(exc_info.value)
    assert not (tmp_path / "checkpoint.zip").exists()


def test_download_checkpoint_closes_owned_session(monkeypatch, tmp_path: Path):
    session = FakeSession(
        FakeResponse({"files": [{"name": "checkpoint.zip", "download_url": "s3-url"}]}),
        FakeResponse(chunks=[b"data"]),
    )
    monkeypatch.setattr("overmind.model_cmd.requests.Session", lambda: session)

    download_checkpoint(
        "deployment-1",
        output=tmp_path / "checkpoint.zip",
        api_key="key",
        api_url="https://api.example",
    )

    assert session.closed is True


def test_download_checkpoint_command_uses_config_and_emits_safe_json(tmp_path: Path, monkeypatch):
    config_path = tmp_path / "overmind.toml"
    dump(
        Config(
            api_key="toml-key",
            base_url="https://configured.example",
            project_id="11111111-1111-1111-1111-111111111111",
        ),
        config_path,
    )
    result_data = {
        "path": str(tmp_path / "checkpoint.zip"),
        "deployment_id": "deployment-1",
        "filename": "checkpoint.zip",
        "expected_size": 4,
        "bytes_written": 4,
    }
    monkeypatch.setattr("overmind.model_cmd.download_checkpoint", lambda *args, **kwargs: result_data)

    result = CliRunner().invoke(
        app,
        ["model", "download-checkpoint", "deployment-1", "--path", str(config_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == result_data
