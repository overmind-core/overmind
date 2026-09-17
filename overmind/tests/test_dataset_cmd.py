from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from overmind.__main__ import app
from overmind.config import Config, dump
from overmind.dataset_cmd import DatasetExportError, DatasetUploadError, export_dataset, upload_file


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200, *, headers=None, chunks=None):
        self.payload = payload
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = json.dumps(payload)
        self.headers = headers or {}
        self.chunks = chunks or []
        self.closed = False

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        return iter(self.chunks)

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, *, received: int = 0, chunk_bytes: int = 4, max_bytes: int = 100):
        self.headers = {}
        self.received = received
        self.chunk_bytes = chunk_bytes
        self.max_bytes = max_bytes
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if url.endswith("/api/uploads/"):
            return FakeResponse(
                {
                    "upload_id": "upload-1",
                    "chunk_bytes": self.chunk_bytes,
                    "max_bytes": self.max_bytes,
                },
                201,
            )
        if url.endswith("/api/datasets/split/"):
            return FakeResponse(
                {
                    "train": {"id": "dataset-1", "state": "landing"},
                    "eval": {"id": "dataset-2", "state": "landing"},
                },
                201,
            )
        return FakeResponse({"id": "dataset-1", "state": "landing"}, 201)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return FakeResponse({"upload_id": "upload-1", "received": self.received})

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        offset = kwargs["params"]["offset"]
        self.received = offset + len(kwargs["data"])
        return FakeResponse({"upload_id": "upload-1", "received": self.received})

    def close(self):
        pass


class ExportSession:
    def __init__(self, response: FakeResponse):
        self.headers = {}
        self.response = response
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.response

    def close(self):
        pass


def _urls(session: FakeSession) -> list[str]:
    return [call[1] for call in session.calls]


def test_upload_file_small_hits_datasets_not_ingestions(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"ab")
    session = FakeSession(chunk_bytes=8)

    result = upload_file(
        path,
        project_id="project-1",
        api_key="key-1",
        api_url="https://api.example/",
        session=session,
    )

    assert result["id"] == "dataset-1"
    assert result["state"] == "landing"
    assert [call[0] for call in session.calls] == ["POST", "GET", "PUT", "POST"]
    assert session.calls[-1][1] == "https://api.example/api/datasets/"
    assert all("/api/ingestions/" not in url for url in _urls(session))
    assert session.calls[-1][2]["json"] == {
        "project": "project-1",
        "name": "rows.jsonl",
        "source": {"upload_id": "upload-1", "filename": "rows.jsonl"},
    }
    assert {action["tool"] for action in result["next_mcp_actions"]} == {
        "get_job",
        "inspect_dataset",
    }
    assert result["next_mcp_actions"][0]["arguments"] == {"kind": "dataset_run", "id": result["id"]}


def test_upload_file_resumes_in_server_chunks_and_creates_dataset(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"0123456789")
    session = FakeSession(received=3, chunk_bytes=4)

    result = upload_file(
        path,
        project_id="project-1",
        api_key="key-1",
        api_url="https://api.example/",
        intent="eval",
        capability="cap-1",
        session=session,
    )

    assert result["id"] == "dataset-1"
    assert result["state"] == "landing"
    assert [call[0] for call in session.calls] == ["POST", "GET", "PUT", "PUT", "POST"]
    chunks = [call[2]["data"] for call in session.calls if call[0] == "PUT"]
    assert chunks == [b"3456", b"789"]
    assert session.calls[-1][1] == "https://api.example/api/datasets/"
    assert all("/api/ingestions/" not in url for url in _urls(session))
    assert session.calls[-1][2]["json"] == {
        "project": "project-1",
        "name": "rows.jsonl",
        "intent": "eval",
        "capability": "cap-1",
        "source": {"upload_id": "upload-1", "filename": "rows.jsonl"},
    }


def test_upload_file_with_split_hits_the_split_endpoint_and_returns_both_ids(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"ab")
    session = FakeSession(chunk_bytes=8)

    result = upload_file(
        path,
        project_id="project-1",
        api_key="key-1",
        api_url="https://api.example/",
        split=25,
        split_position="head",
        session=session,
    )

    assert session.calls[-1][1] == "https://api.example/api/datasets/split/"
    assert session.calls[-1][2]["json"] == {
        "project": "project-1",
        "name": "rows.jsonl",
        "source": {"upload_id": "upload-1", "filename": "rows.jsonl"},
        "eval_percent": 25,
        "position": "head",
    }
    assert (result["id"], result["eval_id"]) == ("dataset-1", "dataset-2")
    assert result["state"] == result["eval_state"] == "landing"
    assert result["next_mcp_actions"][1]["arguments"] == {"dataset": "dataset-1"}


def test_upload_file_rejects_a_bad_split_before_network(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"ab")
    session = FakeSession()
    base = {"project_id": "p", "api_key": "k", "api_url": "https://api.example", "session": session}
    for bad in (
        {"split": 0},
        {"split": 100},
        {"split": 20, "split_position": "middle"},
        {"split": 20, "intent": "train"},
    ):
        with pytest.raises(DatasetUploadError):
            upload_file(path, **base, **bad)
    assert session.calls == []


def test_upload_file_rejects_ft_intent_before_network(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"{}\n")
    session = FakeSession()

    with pytest.raises(DatasetUploadError, match="train or eval"):
        upload_file(
            path,
            project_id="project-1",
            api_key="key-1",
            api_url="https://api.example",
            intent="ft",
            session=session,
        )
    assert session.calls == []


def test_upload_file_checks_maximum_before_reading_state(tmp_path: Path):
    path = tmp_path / "rows.csv"
    path.write_bytes(b"1234")
    session = FakeSession(max_bytes=3)

    with pytest.raises(DatasetUploadError, match="server limit"):
        upload_file(
            path,
            project_id="project-1",
            api_key="key-1",
            api_url="https://api.example",
            session=session,
        )

    assert [call[0] for call in session.calls] == ["POST"]


def test_upload_file_surfaces_server_failure(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"{}\n")

    class FailingSession(FakeSession):
        def post(self, url, **kwargs):
            return FakeResponse({"detail": "service unavailable"}, 503)

    with pytest.raises(DatasetUploadError, match="HTTP 503: service unavailable"):
        upload_file(
            path,
            project_id="project-1",
            api_key="key-1",
            api_url="https://api.example",
            session=FailingSession(),
        )


def test_upload_command_prints_uuid_state_and_mcp_follow_up(tmp_path: Path, monkeypatch):
    file = tmp_path / "rows.jsonl"
    file.write_text("{}\n")
    config_path = tmp_path / "overmind.toml"
    dump(Config(api_key="toml-key", project_id="toml-project"), config_path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)

    monkeypatch.setattr(
        "overmind.dataset_cmd.upload_file",
        lambda *args, **kwargs: {
            "id": "dataset-1",
            "state": "landing",
            "next_mcp_actions": [
                {"tool": "get_job", "arguments": {"kind": "dataset_run"}},
                {"tool": "inspect_dataset", "arguments": {"dataset": "dataset-1"}},
            ],
        },
    )
    result = CliRunner().invoke(
        app,
        ["dataset", "upload", str(file), "--path", str(config_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    assert body["id"] == "dataset-1"
    assert body["state"] == "landing"
    assert body["next_mcp_actions"][0]["arguments"]["kind"] == "dataset_run"
    assert "commit_dataset_build" not in result.output
    assert "api-key" not in result.output.lower()

    human = CliRunner().invoke(app, ["dataset", "upload", str(file), "--path", str(config_path)])
    assert human.exit_code == 0, human.output
    assert "dataset-1" in human.output
    assert "landing" in human.output
    assert "get_job(kind=dataset_run, id=" in human.output


def test_upload_command_passes_split_flags_and_prints_the_eval_dataset(tmp_path: Path, monkeypatch):
    file = tmp_path / "rows.jsonl"
    file.write_text("{}\n")
    config_path = tmp_path / "overmind.toml"
    dump(Config(api_key="toml-key", project_id="toml-project"), config_path)
    seen = {}

    def fake_upload(*args, **kwargs):
        seen.update(kwargs)
        return {
            "id": "dataset-1",
            "state": "landing",
            "eval_id": "dataset-2",
            "eval_state": "landing",
            "next_mcp_actions": [],
        }

    monkeypatch.setattr("overmind.dataset_cmd.upload_file", fake_upload)
    result = CliRunner().invoke(
        app,
        ["dataset", "upload", str(file), "--path", str(config_path), "--split", "30", "--split-position", "random"],
    )
    assert result.exit_code == 0, result.output
    assert (seen["split"], seen["split_position"]) == (30, "random")
    assert "Eval dataset dataset-2 is landing." in result.output


def test_upload_command_rejects_removed_flags(tmp_path: Path):
    file = tmp_path / "rows.jsonl"
    file.write_text("{}\n")
    result = CliRunner().invoke(app, ["dataset", "upload", str(file), "--surface", "model"])
    assert result.exit_code != 0
    assert "no such option" in result.output.lower()


def test_export_dataset_streams_csv_to_explicit_path_with_cell(tmp_path: Path):
    response = FakeResponse(
        {},
        headers={
            "Content-Disposition": 'attachment; filename="server.csv"',
            "X-Overmind-Cell": "cell-9",
            "X-Overmind-Version": "1.2",
            "X-Overmind-Fingerprint": "fp-1",
        },
        chunks=[b"id,name\n", b"1,one\n"],
    )
    session = ExportSession(response)
    output = tmp_path / "rows.csv"

    result = export_dataset(
        "dataset-1",
        file_format="csv",
        cell="cell-9",
        output=output,
        api_key="secret-key",
        api_url="https://api.example/",
        session=session,
    )

    assert result == {
        "path": str(output),
        "dataset_id": "dataset-1",
        "format": "csv",
        "bytes_written": 14,
        "cell": "cell-9",
        "version": "1.2",
        "fingerprint": "fp-1",
    }
    assert output.read_bytes() == b"id,name\n1,one\n"
    assert session.headers == {"X-Api-Key": "secret-key"}
    assert session.calls[0][1] == "https://api.example/api/datasets/dataset-1/export/"
    assert session.calls[0][2]["params"] == {"fmt": "csv", "cell": "cell-9"}
    assert session.calls[0][2]["stream"] is True
    assert response.closed is True
    assert "workshop" not in session.calls[0][1]


def test_export_dataset_omits_cell_for_active_version(tmp_path: Path):
    response = FakeResponse(
        {},
        headers={"X-Overmind-Cell": "active-cell", "X-Overmind-Version": "1.0"},
        chunks=[b"{}\n"],
    )
    session = ExportSession(response)
    output = tmp_path / "rows.jsonl"

    result = export_dataset(
        "dataset-1",
        output=output,
        api_key="key-1",
        api_url="https://api.example",
        session=session,
    )

    assert session.calls[0][1] == "https://api.example/api/datasets/dataset-1/export/"
    assert session.calls[0][2]["params"] == {"fmt": "jsonl"}
    assert result["cell"] == "active-cell"
    assert result["version"] == "1.0"
    assert "fingerprint" not in result


def test_export_dataset_sanitizes_server_filename_and_refuses_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = FakeResponse(
        {},
        headers={"Content-Disposition": "attachment; filename*=UTF-8''..%2Fsafe.jsonl"},
        chunks=[b"{}\n"],
    )
    session = ExportSession(response)

    result = export_dataset(
        "dataset-1",
        api_key="key-1",
        api_url="https://api.example",
        session=session,
    )

    assert result["path"] == "safe.jsonl"
    assert (tmp_path / "safe.jsonl").read_bytes() == b"{}\n"

    with pytest.raises(DatasetExportError, match="already exists"):
        export_dataset(
            "dataset-1",
            api_key="key-1",
            api_url="https://api.example",
            output=tmp_path / "safe.jsonl",
            session=ExportSession(response),
        )


@pytest.mark.parametrize(
    ("server_filename", "safe_filename"),
    [
        ("report<2026>:final?.csv", "report_2026__final_.csv"),
        ("CON.csv", "_CON.csv"),
        ("com1.jsonl", "_com1.jsonl"),
        ("normal.csv", "normal.csv"),
    ],
)
def test_export_dataset_sanitizes_windows_filename_rules(
    tmp_path: Path, monkeypatch, server_filename: str, safe_filename: str
):
    monkeypatch.chdir(tmp_path)
    result = export_dataset(
        "dataset-1",
        file_format="csv",
        api_key="key-1",
        api_url="https://api.example",
        session=ExportSession(
            FakeResponse(
                {},
                headers={"Content-Disposition": f'attachment; filename="{server_filename}"'},
                chunks=[b"{}\n"],
            )
        ),
    )

    assert result["path"] == safe_filename
    assert Path(safe_filename).read_bytes() == b"{}\n"


def test_export_dataset_redacts_api_key_from_server_errors(tmp_path: Path):
    response = FakeResponse({"detail": "invalid secret-key"}, 401)

    with pytest.raises(DatasetExportError, match=r"HTTP 401: invalid \[redacted\]"):
        export_dataset(
            "dataset-1",
            api_key="secret-key",
            api_url="https://api.example",
            output=tmp_path / "rows.jsonl",
            session=ExportSession(response),
        )
    assert not (tmp_path / "rows.jsonl").exists()


def test_export_command_emits_machine_readable_success(tmp_path: Path, monkeypatch):
    captured: dict = {}

    def fake_export(*args, **kwargs):
        captured.update(kwargs)
        return {
            "path": str(tmp_path / "rows.jsonl"),
            "dataset_id": "dataset-1",
            "format": "jsonl",
            "bytes_written": 3,
            "cell": "cell-1",
            "version": "2.0",
            "fingerprint": "fp",
        }

    monkeypatch.setattr("overmind.dataset_cmd.export_dataset", fake_export)

    result = CliRunner().invoke(
        app,
        [
            "dataset",
            "export",
            "dataset-1",
            "--cell",
            "cell-1",
            "--api-key",
            "secret-key",
            "--api-url",
            "https://api.example",
            "--output",
            str(tmp_path / "rows.jsonl"),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["cell"] == "cell-1"
    assert json.loads(result.output) == {
        "path": str(tmp_path / "rows.jsonl"),
        "dataset_id": "dataset-1",
        "format": "jsonl",
        "bytes_written": 3,
        "cell": "cell-1",
        "version": "2.0",
        "fingerprint": "fp",
    }

    sha = CliRunner().invoke(app, ["dataset", "export", "dataset-1", "--sha", "abc"])
    assert sha.exit_code != 0
    assert "no such option" in sha.output.lower()


@pytest.mark.parametrize(
    ("config", "args", "message"),
    [
        (Config(project_id="project-1"), (), "Missing API key"),
        (Config(), ("--api-key", "key-1"), "Missing project-id"),
    ],
)
def test_upload_command_reports_missing_configuration(
    tmp_path: Path, monkeypatch, config: Config, args: tuple[str, ...], message: str
):
    file = tmp_path / "rows.jsonl"
    file.write_text("{}\n")
    config_path = tmp_path / "overmind.toml"
    dump(config, config_path)
    monkeypatch.delenv("OVERMIND_API_KEY", raising=False)

    result = CliRunner().invoke(
        app,
        ["dataset", "upload", str(file), "--path", str(config_path), "--json", *args],
    )

    assert result.exit_code == 1
    assert message in json.loads(result.output)["error"]


def test_wait_until_ready_polls_past_busy_states_and_raises_the_dataset_error(monkeypatch):
    from overmind import dataset_cmd

    class _Response:
        ok = True

        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class _Session:
        def __init__(self, states):
            self.states = list(states)
            self.headers = {}

        def get(self, *_args, **_kwargs):
            return _Response(self.states.pop(0))

    monkeypatch.setattr(dataset_cmd.time, "sleep", lambda _s: None)
    done = dataset_cmd.wait_until_ready(
        "d1",
        api_key="k",
        api_url="http://x",
        session=_Session([{"state": "landing"}, {"state": "diagnosing"}, {"state": "idle"}]),
    )
    assert done == {"state": "idle"}
    with pytest.raises(dataset_cmd.DatasetUploadError, match="Row 2 has 3 cells"):
        dataset_cmd.wait_until_ready(
            "d1",
            api_key="k",
            api_url="http://x",
            session=_Session([{"state": "error", "error": "Row 2 has 3 cells; the header has 2."}]),
        )
