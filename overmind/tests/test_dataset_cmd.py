from __future__ import annotations

import hashlib
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


@pytest.mark.parametrize("corrupt", [False, True])
def test_original_source_export_verifies_bytes_and_removes_corrupt_download(tmp_path, corrupt):
    original = b"%PDF original retained source bytes"
    fingerprint = hashlib.sha256(original).hexdigest()
    destination = tmp_path / "source.pdf"
    session = ExportSession(FakeResponse({}, chunks=[original + b"changed" if corrupt else original]))
    arguments = dict(
        source=fingerprint, output=destination, api_key="secret", api_url="http://localhost:8000", session=session
    )
    if corrupt:
        with pytest.raises(DatasetExportError, match="checksum"):
            export_dataset("dataset-1", **arguments)
        assert not destination.exists()
    else:
        result = export_dataset("dataset-1", **arguments)
        assert destination.read_bytes() == original
        assert result["sha256"] == fingerprint
        assert result["format"] == "original"
    assert session.calls[0][1] == f"http://localhost:8000/api/datasets/dataset-1/sources/{fingerprint}/"
    assert session.calls[0][2]["allow_redirects"] is False


def test_original_source_rejects_cell_selection_and_invalid_identity_before_network(tmp_path):
    session = ExportSession(FakeResponse({}))
    for source, cell in (("../escape", None), ("a" * 64, "cell-id")):
        with pytest.raises(DatasetExportError):
            export_dataset(
                "dataset-1",
                source=source,
                cell=cell,
                output=tmp_path / "source",
                api_key="secret",
                api_url="http://localhost:8000",
                session=session,
            )
    assert session.calls == []


def test_upload_file_rejects_a_bad_split_before_network(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"ab")
    session = ExportSession(FakeResponse({}))
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
    session = ExportSession(FakeResponse({}))

    with pytest.raises(DatasetUploadError, match="train, eval or explore"):
        upload_file(
            path,
            project_id="project-1",
            api_key="key-1",
            api_url="https://api.example",
            intent="ft",
            session=session,
        )
    assert session.calls == []


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
    assert "Transfer published" in human.output
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
    assert "Eval dataset dataset-2." in result.output


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


def test_export_refuses_redirects_without_forwarding_account_credentials(tmp_path):
    session = ExportSession(FakeResponse({}, 302, headers={"Location": "https://other.invalid"}))
    with pytest.raises(DatasetExportError, match="redirect"):
        export_dataset(
            "dataset-1", api_key="key", api_url="http://localhost:8000", output=tmp_path / "rows.jsonl", session=session
        )
    assert session.calls[0][2]["allow_redirects"] is False
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
    assert message in str(json.loads(result.output)["error"])


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
        session=_Session([{"state": "landing"}, {"state": "running"}, {"state": "idle"}]),
    )
    assert done == {"state": "idle"}
    with pytest.raises(dataset_cmd.DatasetUploadError, match="Row 2 has 3 cells"):
        dataset_cmd.wait_until_ready(
            "d1",
            api_key="k",
            api_url="http://x",
            session=_Session([{"state": "error", "error": "Row 2 has 3 cells; the header has 2."}]),
        )
