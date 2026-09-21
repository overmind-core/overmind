import gzip
import json
import uuid
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from rest_framework.test import APIClient

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import files, paths, store


def _upload(name, content):
    upload_id, _ = files.begin_upload(name)
    files.append_chunk(upload_id, 0, content)
    return upload_id


@pytest.fixture
def client_project(db):
    user = User.objects.create_user(email="upload@test.com", password="pw", clerk_user_id="upload")
    project = Project.objects.create(name="Uploads", slug="uploads")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user)
    return client, project


@pytest.mark.parametrize(
    ("name", "content", "rows"),
    [
        ("quoted.csv", b'input,expected_output\n"two\nlines",one\nsecond,two\n', 2),
        ("table.tsv", b"input\texpected_output\nq1\ta1\nq2\ta2\n", 2),
        ("array.json", b'[{"input":"one"},{"input":"two"}]', 2),
        ("wrapper.json", b'{"data":[{"input":"one"},{"input":"two"}]}', 2),
        ("lines.jsonl", b'{"input":"one"}\n\n{"input":"two"}\n', 2),
        ("compressed.CSV.GZ", gzip.compress(b"input\none\ntwo\n", mtime=0), 2),
        pytest.param("large-field.csv", b"input\n" + b"a" * 150_000, 1, id="long-transcript"),
    ],
)
def test_inspection_counts_the_same_rows_that_land(name, content, rows):
    upload_id = _upload(name, content)
    inspection = files.inspect_upload(upload_id, size=len(content))
    assert inspection == {"filename": name, "bytes": len(content), "rows": rows}
    assert len(files.read_file_rows(files.upload_data_path(upload_id), filename=name)) == rows


def test_inspection_counts_parquet_metadata():
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist([{"input": "one"}, {"input": "two"}]), sink)
    content = sink.getvalue().to_pybytes()
    upload_id = _upload("data.parquet", content)
    assert files.inspect_upload(upload_id, size=len(content))["rows"] == 2


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("empty.csv", b"", "no rows"),
        ("header.csv", b"input,output\n", "no rows"),
        ("empty.json", b"[]", "no rows"),
        ("invalid.jsonl", b'{"input":', "not valid JSON"),
        ("invalid.parquet", b"not parquet", "Parquet"),
        ("truncated.csv.gz", b"\x1f\x8b", "not readable gzip"),
    ],
)
def test_inspection_rejects_empty_or_invalid_files(name, content, message):
    upload_id = _upload(name, content)
    with pytest.raises(files.FileError, match=message):
        files.inspect_upload(upload_id, size=len(content))


def test_inspection_requires_the_complete_upload():
    upload_id = _upload("data.csv", b"input\none\n")
    with pytest.raises(files.FileError, match="incomplete"):
        files.inspect_upload(upload_id, size=100)
    files.discard_upload(upload_id)
    with pytest.raises(files.FileError, match="expired"):
        files.inspect_upload(upload_id, size=10)


def test_inspection_endpoint_counts_rows_and_reports_validation_errors(client_project):
    client, _ = client_project
    reserved = client.post("/api/uploads/", {"filename": "data.csv"}, format="json")
    assert reserved.status_code == 201
    upload_id = reserved.data["upload_id"]
    content = b"input\none\ntwo\n"
    uploaded = client.put(
        f"/api/uploads/{upload_id}/chunk/?offset=0",
        content,
        content_type="application/octet-stream",
    )
    assert uploaded.status_code == 200
    url = f"/api/uploads/{upload_id}/inspect/"
    result = client.post(url, {"size": len(content)}, format="json")
    assert result.status_code == 200, result.data
    assert result.data == {"filename": "data.csv", "bytes": len(content), "rows": 2}
    assert client.post(url, {"size": -1}, format="json").status_code == 400
    assert client.post(url, {"size": 999}, format="json").status_code == 400
    assert client.post("/api/uploads/not-a-uuid/inspect/", {"size": 0}).status_code == 404
    assert APIClient().post(url, {"size": len(content)}, format="json").status_code == 401


@pytest.mark.parametrize("failure_type", [OSError, ValueError, pa.ArrowInvalid])
def test_inspection_does_not_expose_parser_or_storage_diagnostics(
    client_project, monkeypatch, caplog, failure_type
):
    client, _ = client_project
    content = b"input\none\n"
    upload_id = _upload("data.csv", content)
    private = "Traceback: /srv/private/uploads/data token=hidden"
    monkeypatch.setattr(files, "_open_text", Mock(side_effect=failure_type(private)))
    result = client.post(
        f"/api/uploads/{upload_id}/inspect/", {"size": len(content)}, format="json"
    )
    assert result.status_code == 400
    assert "could not be read" in result.data["detail"]
    assert private not in result.content.decode()
    assert private in caplog.text


@pytest.mark.parametrize("split", [False, True])
def test_multiple_files_land_in_order_and_split_across_the_combined_rows(client_project, split):
    client, project = client_project
    first = b"input,expected_output\n" + b"".join(f"q{i},a{i}\n".encode() for i in range(7))
    second = json.dumps(
        [{"input": f"q{i}", "expected_output": f"a{i}"} for i in range(7, 15)]
    ).encode()
    ids = [_upload("first.csv", first), _upload("second.json", second)]
    assert sum(files.inspect_upload(i, size=files.upload_received(i))["rows"] for i in ids) == 15
    body = {"project": str(project.id), "name": "Combined", "source": {"uploads": ids}}
    if split:
        body.update(eval_percent=30, position="tail")
    response = client.post(
        "/api/datasets/split/" if split else "/api/datasets/", body, format="json"
    )
    assert response.status_code == 201, response.data
    datasets = (
        [Dataset.objects.get(pk=response.data[role]["id"]) for role in ("train", "eval")]
        if split
        else [Dataset.objects.get(pk=response.data["id"])]
    )
    assert [dataset.source.rows for dataset in datasets] == ([10, 5] if split else [15])
    landed = []
    for dataset in datasets:
        assert dataset.state == Dataset.State.IDLE, dataset.error
        assert dataset.source_spec["files"] == [
            {"filename": "first.csv", "bytes": len(first), "rows": 7},
            {"filename": "second.json", "bytes": len(second), "rows": 8},
        ]
        landed.extend(
            store.read_frame(paths.cell_path(dataset.id, dataset.source.id))["input"].tolist()
        )
    assert landed == [f"q{i}" for i in range(15)]
    assert all(not files.upload_data_path(upload_id).exists() for upload_id in ids)


def test_invalid_multi_file_sources_are_rejected_before_creation(client_project):
    client, project = client_project
    upload_id = _upload("data.csv", b"input\none\n")
    empty_id, _ = files.begin_upload("empty.csv")
    for source in (
        {"uploads": []},
        {"uploads": [upload_id, upload_id]},
        {"uploads": ["invalid"]},
        {"uploads": [str(uuid.uuid4())]},
        {"uploads": [empty_id]},
        {"uploads": [str(uuid.uuid4()) for _ in range(101)]},
        {"uploads": [upload_id], "upload_id": upload_id},
        {"uploads": [upload_id], "rows": [{"input": "one"}]},
    ):
        response = client.post(
            "/api/datasets/", {"project": str(project.id), "source": source}, format="json"
        )
        assert response.status_code == 400, response.data
    assert not Dataset.objects.filter(project=project).exists()
