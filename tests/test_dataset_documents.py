import asyncio
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from conftest import read_table
from fixtures.documents.generate import pdf
from rest_framework.test import APIClient

from overbae.models import APIToken, Dataset, Project, ProjectMembership, User
from overbae.services.datasets import files, paths
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

FIXTURES = Path(__file__).parent / "fixtures" / "documents"
needs_ocr = pytest.mark.skipif(not shutil.which("tesseract"), reason="Tesseract is not installed")
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client_project():
    user = User.objects.create_user(email="docs@test.com", password="pw", clerk_user_id="docs")
    project = Project.objects.create(name="Documents", slug="documents")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user)
    return client, project


def _upload(client, name, content):
    reserved = client.post("/api/uploads/", {"filename": name}, format="json")
    assert reserved.status_code == 201, reserved.data
    upload_id = reserved.data["upload_id"]
    stored = client.put(
        f"/api/uploads/{upload_id}/chunk/?offset=0",
        content,
        content_type="application/octet-stream",
    )
    assert stored.status_code == 200, stored.data
    return upload_id


def _land(client, project, uploads, **body):
    inspected = [
        client.post(
            f"/api/uploads/{upload_id}/inspect/",
            {"size": files.upload_received(upload_id)},
            format="json",
        )
        for upload_id in uploads
    ]
    for result in inspected:
        assert result.status_code == 200, result.data
    split = "eval_percent" in body
    response = client.post(
        "/api/datasets/split/" if split else "/api/datasets/",
        {
            "project": str(project.id),
            "name": "Docs",
            "source": {"uploads": uploads, **body.pop("source", {})},
            **body,
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    if split:
        return [Dataset.objects.get(pk=response.data[role]["id"]) for role in ("train", "eval")]
    return Dataset.objects.get(pk=response.data["id"])


def _rows(dataset):
    return read_table(paths.cell_path(dataset.id, dataset.source.id)).to_dict(orient="records")


def test_a_text_pdf_lands_one_row_per_element_with_pages_and_regions(client_project):
    client, project = client_project
    content = (FIXTURES / "native.pdf").read_bytes()
    upload_id = _upload(client, "native.pdf", content)
    inspected = client.post(
        f"/api/uploads/{upload_id}/inspect/", {"size": len(content)}, format="json"
    )
    assert inspected.data == {
        "filename": "native.pdf",
        "bytes": len(content),
        "rows": None,
        "sha256": hashlib.sha256(content).hexdigest(),
    }

    dataset = _land(client, project, [upload_id])

    assert dataset.state == Dataset.State.IDLE, dataset.error
    rows = _rows(dataset)
    digest = hashlib.sha256(content).hexdigest()
    assert [(row["text"], row["page"]) for row in rows] == [
        ("Invoice total: 1234.56 USD", 1),
        ("Refunds are issued within 30 days.", 2),
    ]
    assert {row["source_name"] for row in rows} == {"native.pdf"}
    assert {row["_overmind_document_id"] for row in rows} == {digest}
    evidence = rows[0]["_overmind_provenance"]["evidence"][0]
    assert evidence["document_id"] == digest
    assert evidence["regions"][0]["page"] == 1
    assert set(evidence["regions"][0]["bbox"]) >= {"l", "t", "r", "b"}
    (described,) = dataset.source_spec["files"]
    assert described["filename"] == "native.pdf"
    assert (described["bytes"], described["sha256"], described["rows"]) == (
        len(content),
        digest,
        2,
    )
    assert described["extraction"]["method"] == "docling-native-pdf"
    assert dataset.source_spec["extraction"]["limitations"] == [
        "Tables and multi-column layouts may lose their structure."
    ]
    assert not files.upload_data_path(upload_id).exists()


def test_inspect_names_each_document_and_what_extraction_could_not_guarantee(client_project):
    client, project = client_project
    content = (FIXTURES / "native.pdf").read_bytes()
    dataset = _land(client, project, [_upload(client, "native.pdf", content)])
    token = APIToken(
        scope={"scope": "project", "resourceIds": [str(project.id)], "permission": ["read"]}
    )
    context = MCPContext(user=User.objects.get(email="docs@test.com"), token=token, project=project)

    inspected = asyncio.run(
        CATALOG.call("inspect_dataset", {"dataset": str(dataset.id)}, context)
    ).structuredContent

    (described,) = inspected["source"]["files"]
    assert (described["filename"], described["bytes"], described["rows"]) == (
        "native.pdf",
        len(content),
        2,
    )
    assert described["extraction"]["method"] == "docling-native-pdf"
    assert inspected["source"]["limitations"] == [
        "Tables and multi-column layouts may lose their structure."
    ]


@pytest.mark.parametrize(
    ("name", "texts"),
    [
        ("policy.docx", ["Returns policy", "Customers may return items within 30 days."]),
        ("notes.md", ["Shipping", "Orders ship in two days."]),
        ("faq.txt", ["How do I reset my password?\nUse the reset link.", "Where is my order?"]),
    ],
)
def test_word_markdown_and_text_documents_land_their_text(client_project, name, texts):
    client, project = client_project
    upload_id = _upload(client, name, (FIXTURES / name).read_bytes())

    dataset = _land(client, project, [upload_id])

    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert [row["text"] for row in _rows(dataset)][: len(texts)] == texts


@needs_ocr
def test_a_scanned_pdf_lands_ocr_lines(client_project):
    client, project = client_project
    upload_id = _upload(client, "scanned.pdf", (FIXTURES / "scanned.pdf").read_bytes())

    dataset = _land(client, project, [upload_id])

    assert dataset.state == Dataset.State.IDLE, dataset.error
    (row,) = _rows(dataset)
    assert row["text"] == "Policy 7: refunds need a receipt."
    assert row["_overmind_provenance"]["extraction"]["method"] == "tesseract-ocr"
    assert (
        "Text read from images can contain errors. Only English is read."
        in (dataset.source_spec["extraction"]["limitations"])
    )


@pytest.mark.parametrize(
    ("name", "content", "error"),
    [
        (
            "password.pdf",
            (FIXTURES / "password.pdf").read_bytes(),
            "The PDF could not be read. Check that it is readable and not password protected.",
        ),
        (
            "long.pdf",
            pdf([""] * 2001),
            "The PDF has 2001 pages; the limit is 2000. Split it into smaller PDFs.",
        ),
        (
            "latin1.txt",
            (FIXTURES / "latin1.txt").read_bytes(),
            "The document is not UTF-8. Save it as UTF-8 and upload it again.",
        ),
        pytest.param(
            "blank.png",
            (FIXTURES / "blank.png").read_bytes(),
            "No text was found after OCR. Check that the document contains legible text.",
            marks=needs_ocr,
        ),
    ],
    ids=["password", "page-limit", "not-utf8", "no-text"],
)
def test_an_unreadable_document_fails_the_landing_with_no_rows(
    client_project, name, content, error
):
    client, project = client_project
    upload_id = _upload(client, name, content)

    dataset = _land(client, project, [upload_id])

    assert dataset.state == Dataset.State.ERROR
    assert dataset.error == error
    assert dataset.source is None
    assert files.upload_data_path(upload_id).exists()
    assert client.get(f"/api/datasets/{dataset.id}/").data["import_blocked"] is False
    retried = client.post(f"/api/datasets/{dataset.id}/resume-import/")
    assert (retried.status_code, retried.data["code"]) == (409, "not_resumable")


def test_a_refused_extraction_can_be_retried_once_the_sandbox_accepts_it(client_project, settings):
    client, project = client_project
    token = settings.SANDBOX_TOKEN
    settings.SANDBOX_TOKEN = "wrong"
    upload_id = _upload(client, "native.pdf", (FIXTURES / "native.pdf").read_bytes())

    dataset = _land(client, project, [upload_id])

    assert dataset.state == Dataset.State.ERROR
    assert dataset.error == "The document could not be read. Retry the import."
    assert client.get(f"/api/datasets/{dataset.id}/").data["import_blocked"] is True
    settings.SANDBOX_TOKEN = token
    assert client.post(f"/api/datasets/{dataset.id}/resume-import/").status_code == 202
    dataset.refresh_from_db()
    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert len(_rows(dataset)) == 2


def test_a_gzipped_document_is_refused_at_upload(client_project):
    client, _ = client_project

    response = client.post("/api/uploads/", {"filename": "report.pdf.gz"}, format="json")

    assert response.status_code == 400
    assert response.data["detail"] == "Upload documents uncompressed."


def test_a_document_is_capped_at_100_mib(client_project):
    client, _ = client_project
    upload_id = _upload(client, "big.pdf", b"%PDF")

    response = client.put(
        f"/api/uploads/{upload_id}/chunk/?offset={100 * 1024**2}",
        b"x",
        content_type="application/octet-stream",
    )

    assert response.status_code == 409
    assert response.data["detail"] == "Documents are capped at 100 MiB."


def test_tables_and_documents_in_one_batch_are_refused(client_project):
    client, project = client_project
    table = _upload(client, "rows.csv", b"input\none\n")
    document = _upload(client, "faq.txt", (FIXTURES / "faq.txt").read_bytes())
    for upload_id in (table, document):
        client.post(
            f"/api/uploads/{upload_id}/inspect/",
            {"size": files.upload_received(upload_id)},
            format="json",
        )

    response = client.post(
        "/api/datasets/",
        {"project": str(project.id), "name": "Mixed", "source": {"uploads": [table, document]}},
        format="json",
    )

    assert response.status_code == 400
    assert "Upload tables and documents as separate datasets." in str(response.data)
    assert not Dataset.objects.filter(project=project).exists()


def test_a_split_keeps_each_document_on_one_side(client_project):
    client, project = client_project
    uploads = [
        _upload(client, name, (FIXTURES / name).read_bytes())
        for name in ("native.pdf", "policy.docx", "notes.md", "faq.txt")
    ]

    train, evaluation = _land(client, project, uploads, eval_percent=50, position="random")

    assert train.state == evaluation.state == Dataset.State.IDLE, (train.error, evaluation.error)
    sides = [{row["_overmind_document_id"] for row in _rows(part)} for part in (train, evaluation)]
    assert sides[0] and sides[1]
    assert not sides[0] & sides[1]
    assert train.source_spec["split"]["group_by"] == ["_overmind_document_id"]


def test_a_split_of_one_document_is_refused_before_any_dataset_exists(client_project):
    client, project = client_project
    upload_id = _upload(client, "native.pdf", (FIXTURES / "native.pdf").read_bytes())
    client.post(
        f"/api/uploads/{upload_id}/inspect/",
        {"size": files.upload_received(upload_id)},
        format="json",
    )

    response = client.post(
        "/api/datasets/split/",
        {
            "project": str(project.id),
            "name": "One",
            "source": {"uploads": [upload_id]},
            "eval_percent": 30,
            "position": "random",
        },
        format="json",
    )

    assert response.status_code == 400
    assert "Two documents are needed to split." in str(response.data)
    assert not Dataset.objects.filter(project=project).exists()


def test_a_json_rows_field_lands_only_that_array(client_project):
    client, project = client_project
    content = json.dumps(
        {"data": [{"input": "ignored"}], "records": [{"input": "one"}, {"input": "two"}]}
    ).encode()
    upload_id = _upload(client, "export.json", content)
    inspected = client.post(
        f"/api/uploads/{upload_id}/inspect/",
        {"size": len(content), "json_rows_field": "records"},
        format="json",
    )
    assert inspected.data["rows"] == 2

    dataset = _land(client, project, [upload_id], source={"json_rows_field": "records"})

    assert dataset.state == Dataset.State.IDLE, dataset.error
    assert [row["input"] for row in _rows(dataset)] == ["one", "two"]
    assert dataset.source_spec["json_rows_field"] == "records"


def test_a_missing_json_rows_field_is_refused(client_project):
    client, _ = client_project
    content = json.dumps({"data": [{"input": "one"}]}).encode()
    upload_id = _upload(client, "export.json", content)

    response = client.post(
        f"/api/uploads/{upload_id}/inspect/",
        {"size": len(content), "json_rows_field": "records"},
        format="json",
    )

    assert response.status_code == 400
    assert response.data["detail"] == "The JSON has no top-level array named 'records'."
