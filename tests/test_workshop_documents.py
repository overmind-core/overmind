from __future__ import annotations

import io
import subprocess
import uuid
from pathlib import Path

import pypdfium2 as pdfium
import pytest
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import ocr, paths, store
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db


@pytest.fixture
def workshop():
    project = Project.objects.create(name="Document workshop", slug=uuid.uuid4().hex)
    user = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.test", password="test")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client, project


def upload(client, filename, content):
    reserved = client.post("/api/uploads/", {"filename": filename}, format="json")
    assert reserved.status_code == 201, reserved.data
    upload_id = reserved.data["upload_id"]
    sent = client.put(
        f"/api/uploads/{upload_id}/chunk/?offset=0",
        content,
        content_type="application/octet-stream",
    )
    assert sent.status_code == 200, sent.data
    inspected = client.post(
        f"/api/uploads/{upload_id}/inspect/", {"size": len(content)}, format="json"
    )
    assert inspected.status_code == 200, inspected.data
    return upload_id, inspected.data


def test_intent_first_then_source_keeps_the_original_request(
    workshop, django_capture_on_commit_callbacks
):
    client, project = workshop
    brief = "Help me explore these documents before choosing a training task."
    created = client.post(
        "/api/datasets/", {"project": str(project.id), "brief": brief}, format="json"
    )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    assert dataset.brief == brief and dataset.capability_id is None
    assert dataset.source is None and dataset.intent == "pending"
    upload_id, inspection = upload(
        client, "handbook.md", b"# Delivery\n\nStandard delivery takes three days.\n"
    )
    assert inspection["rows"] is None
    with django_capture_on_commit_callbacks(execute=True):
        attached = client.post(
            f"/api/datasets/{dataset.id}/source/", {"uploads": [upload_id]}, format="json"
        )
    assert attached.status_code == 202, attached.data
    dataset.refresh_from_db()
    assert dataset.source.ran and dataset.brief == brief
    source = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert any("three days" in value for value in source["text"])
    assert all(source["_overmind_document_id"])
    assert all(source["_overmind_provenance"].map(lambda value: bool(value["evidence"])))
    again = client.post(
        f"/api/datasets/{dataset.id}/source/", {"rows": [{"text": "replacement"}]}, format="json"
    )
    assert again.status_code == 202
    artifact_id = dataset.source_spec["sources"][0]["id"]
    download = client.get(f"/api/datasets/{dataset.id}/sources/{artifact_id}/")
    assert download.status_code == 200
    assert b"three days" in b"".join(download.streaming_content)


def test_empty_creation_is_rejected_and_foreign_source_is_not_accessible(workshop):
    client, project = workshop
    empty = client.post("/api/datasets/", {"project": str(project.id)}, format="json")
    assert empty.status_code == 400
    foreign = Dataset.objects.create(
        project=Project.objects.create(name="Other", slug=uuid.uuid4().hex),
        name="Private",
        state="idle",
    )
    attached = client.post(
        f"/api/datasets/{foreign.id}/source/", {"rows": [{"text": "test"}]}, format="json"
    )
    assert attached.status_code == 404
    assert client.get(f"/api/datasets/{foreign.id}/sources/{'a' * 64}/").status_code == 404


def test_document_rows_remain_evidence_not_fabricated_training_answers(workshop):
    client, project = workshop
    content = b"Service handbook\n\nReturns are accepted for thirty days.\n"
    upload_id, _ = upload(client, "handbook.txt", content)
    created = client.post(
        "/api/datasets/",
        {
            "project": str(project.id),
            "name": "Returns",
            "brief": "Prepare grounded question answering examples.",
            "intent": "train",
            "source": {"uploads": [upload_id]},
        },
        format="json",
    )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert "messages" not in frame and "expected_output" not in frame
    assert dataset.source_spec["sources"][0]["sha256"] == frame.iloc[0]["_overmind_document_id"]
    assert not dataset.source.fits("train")[0]


def land_pdf(workshop, name):
    client, project = workshop
    content = (Path(__file__).parent / "fixtures" / "documents" / f"{name}.pdf").read_bytes()
    upload_id, inspection = upload(client, f"{name}.pdf", content)
    assert inspection["rows"] is None
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"uploads": [upload_id]}},
        format="json",
    )
    assert created.status_code == 201, created.data
    return Dataset.objects.get(pk=created.data["id"]), content


def test_type3_native_text_survives_landing_without_ocr_replacement(workshop):
    dataset, content = land_pdf(workshop, "type3")
    assert dataset.source is not None, dataset.error
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert set(frame["page"]) == {1, 2, 3, 4}
    for page in range(1, 5):
        rows = frame[frame["page"] == page].to_dict("records")
        assert "AI Security: Model Processing 123" in " ".join(row["text"] for row in rows)
        native = [
            row
            for row in rows
            if row["_overmind_provenance"]["extraction"]["method"] == "pdfium-native-text"
        ]
        assert native
        width, height = (612, 792) if page in (1, 3) else (792, 612)
        for row in native:
            box = row["_overmind_provenance"]["evidence"][0]["regions"][0]["bbox"]
            assert box["coord_origin"] == "TOPLEFT"
            assert 0 <= box["l"] < box["r"] <= width
            assert 0 <= box["t"] < box["b"] <= height
    source = dataset.source_spec["sources"][0]
    assert source["extraction"]["native_text_recovery"]["pages"] == [1, 2, 3, 4]
    assert source["extraction"]["native_text_recovery"]["control_characters"] == 4
    assert any("font-encoding" in value for value in source["extraction"]["limitations"])
    downloaded = workshop[0].get(f"/api/datasets/{dataset.id}/sources/{source['id']}/")
    assert b"".join(downloaded.streaming_content) == content


@pytest.mark.parametrize("name,ocr_pages", [("scanned", [1]), ("mixed", [2, 3])])
def test_scanned_pdf_upload_lands_with_page_evidence_and_original_bytes(workshop, name, ocr_pages):
    dataset, content = land_pdf(workshop, name)
    assert dataset.source is not None, dataset.error
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert "messages" not in frame and "expected_output" not in frame
    artifact = dataset.source_spec["sources"][0]
    assert artifact["extraction"]["ocr"]["pages"] == ocr_pages
    assert artifact["extraction"]["ocr"]["engine"] == "tesseract"
    assert artifact["extraction"]["ocr"]["version"]
    assert artifact["extraction"]["ocr"]["languages"] == ["eng"]
    for page in ocr_pages:
        rows = frame[frame["page"] == page]
        assert "Returns are accepted for thirty days." in " ".join(rows["text"])
        for _, row in rows.iterrows():
            evidence = row["_overmind_provenance"]["evidence"][0]
            assert evidence["document_id"] == artifact["sha256"]
            assert evidence["page"] == page
            assert evidence["regions"][0]["page"] == page
            bbox = evidence["regions"][0]["bbox"]
            assert 0 <= bbox["l"] < bbox["r"] <= 612
            assert bbox["coord_origin"] in ("TOPLEFT", "BOTTOMLEFT")
            assert 0 <= min(bbox["t"], bbox["b"]) < max(bbox["t"], bbox["b"]) <= 792
        ocr_rows = [
            row
            for row in rows.to_dict("records")
            if row["_overmind_provenance"]["extraction"]["method"] == "tesseract-ocr"
        ]
        assert ocr_rows
        assert all(
            0 <= row["_overmind_provenance"]["extraction"]["confidence"] <= 100 for row in ocr_rows
        )
    if name == "mixed":
        assert list(frame["page"]) == sorted(frame["page"])
        assert list(frame["text"]).count("Invoice total: 1234.56 USD") == 1
        assert list(frame["text"]).count("Shipping policy revision 7") == 1
        assert " ".join(frame[frame["page"] == 3]["text"]).count("Shipping policy") == 1
    downloaded = workshop[0].get(f"/api/datasets/{dataset.id}/sources/{artifact['id']}/")
    assert downloaded.status_code == 200
    assert b"".join(downloaded.streaming_content) == content


def image_bytes(format, *, variant="plain"):
    path = Path(__file__).parent / "fixtures" / "documents" / "scanned.pdf"
    with pdfium.PdfDocument(path) as pdf:
        page = pdf[0]
        bitmap = page.render(scale=2)
        image = bitmap.to_pil().convert("RGB")
        bitmap.close()
        page.close()
    options = {}
    if variant == "rotated":
        image = image.transpose(Image.Transpose.ROTATE_90)
        exif = Image.Exif()
        exif[274] = 6
        options["exif"] = exif
    elif variant == "transparent":
        image = image.convert("RGBA")
        image.putalpha(image.convert("L").point(lambda value: 255 - value))
    elif variant == "blank":
        image = Image.new("RGB", image.size, "white")
    elif variant == "animated":
        options = {"save_all": True, "append_images": [Image.new("RGB", image.size, "black")]}
    buffer = io.BytesIO()
    image.save(buffer, format=format, **options)
    image.close()
    return buffer.getvalue()


@pytest.mark.parametrize(
    "extension,format,variant",
    [
        ("png", "PNG", "transparent"),
        ("jpg", "JPEG", "rotated"),
        ("jpeg", "JPEG", "plain"),
        ("webp", "WEBP", "plain"),
    ],
)
def test_image_upload_extracts_text_with_upright_evidence_and_original_download(
    workshop, monkeypatch, extension, format, variant
):
    client, project = workshop
    content = image_bytes(format, variant=variant)
    if format == "WEBP":
        monkeypatch.setattr(ocr, "MAX_PIXELS", 1_000_000)
    upload_id, inspection = upload(client, f"policy.{extension}", content)
    assert inspection["rows"] is None
    progress = []
    publish = tasks._emit

    def capture(dataset_id, event):
        if event["type"] == "land_progress":
            progress.append((event, Dataset.objects.get(pk=dataset_id).source_spec.copy()))
        publish(dataset_id, event)

    monkeypatch.setattr(tasks, "_emit", capture)
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"uploads": [upload_id]}},
        format="json",
    )
    assert created.status_code == 201, created.data
    dataset = Dataset.objects.get(pk=created.data["id"])
    assert dataset.source is not None, dataset.error
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert "Returns are accepted for thirty days." in " ".join(frame["text"])
    assert "messages" not in frame
    artifact = dataset.source_spec["sources"][0]
    extraction = artifact["extraction"]
    assert extraction["method"] == "tesseract-ocr"
    assert extraction["ocr"]["image"]["width"] == 1224
    assert extraction["ocr"]["image"]["height"] == 1584
    assert extraction["ocr"]["image"]["coordinate_space"] == "exif-oriented-pixels"
    if format == "WEBP":
        assert extraction["ocr"]["image"]["ocr_width"] < 1224
    for provenance in frame["_overmind_provenance"]:
        evidence = provenance["evidence"][0]
        assert evidence["document_id"] == artifact["sha256"]
        assert evidence["filename"] == f"policy.{extension}"
        bbox = evidence["regions"][0]["bbox"]
        assert bbox["coord_origin"] == "TOPLEFT"
        assert 0 <= bbox["l"] < bbox["r"] <= 1224
        assert 0 <= bbox["t"] < bbox["b"] <= 1584
        assert 0 <= provenance["extraction"]["confidence"] <= 100
    assert progress
    assert progress[0][1]["landing_progress"]["filename"] == f"policy.{extension}"
    assert progress[0][1]["landing_progress"]["completed"] == 0
    assert "landing_progress" not in dataset.source_spec
    download = client.get(f"/api/datasets/{dataset.id}/sources/{artifact['id']}/")
    assert download.status_code == 200
    assert b"".join(download.streaming_content) == content


@pytest.mark.parametrize("failure", ["corrupt", "blank", "animated", "disguised", "oversized"])
def test_invalid_image_does_not_partially_append_a_batch(
    workshop, django_capture_on_commit_callbacks, monkeypatch, failure
):
    client, project = workshop
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"rows": [{"text": "original"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    original = paths.cell_path(dataset.id, dataset.source.id).read_bytes()
    if failure == "oversized":
        monkeypatch.setattr(ocr, "MAX_INPUT_PIXELS", 1000)
    content = (
        b"not an image"
        if failure == "corrupt"
        else image_bytes("GIF")
        if failure == "disguised"
        else image_bytes("WEBP", variant="animated")
        if failure == "animated"
        else image_bytes("PNG", variant="blank" if failure == "blank" else "plain")
    )
    name = "broken.webp" if failure == "animated" else "broken.png"
    good, _ = upload(client, "more.txt", b"This must not land on its own.")
    bad, _ = upload(client, name, content)
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/datasets/{dataset.id}/source/",
            {"uploads": [good, bad]},
            format="json",
        )
    assert response.status_code == 202, response.data
    dataset.refresh_from_db()
    assert dataset.state == "error"
    assert dataset.cells.count() == 1
    assert paths.cell_path(dataset.id, dataset.source.id).read_bytes() == original
    assert not dataset.source_spec.get("sources")
    expected = {
        "corrupt": "image could not be read",
        "blank": "No text was found after OCR",
        "animated": "Animated images",
        "disguised": "PNG, JPEG or WebP",
        "oversized": "64 megapixels",
    }
    assert expected[failure] in dataset.error


def test_image_can_be_added_to_an_existing_workshop(workshop, django_capture_on_commit_callbacks):
    client, project = workshop
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"rows": [{"text": "existing"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    original = paths.cell_path(dataset.id, dataset.source.id).read_bytes()
    upload_id, _ = upload(client, "policy.png", image_bytes("PNG"))
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/datasets/{dataset.id}/source/",
            {"uploads": [upload_id]},
            format="json",
        )
    assert response.status_code == 202
    dataset.refresh_from_db()
    added = dataset.chain[-1]
    frame = store.read_frame(paths.cell_path(dataset.id, added.id))
    assert added.review["kind"] == "attachment"
    assert frame.iloc[0]["text"] == "existing"
    assert "Returns are accepted for thirty days." in " ".join(frame["text"])
    assert frame["source_row"].is_unique
    assert paths.cell_path(dataset.id, dataset.source.id).read_bytes() == original
    assert "landing_progress" not in dataset.source_spec


def test_native_pdf_does_not_require_ocr(workshop, monkeypatch):
    def unavailable(*args, **kwargs):
        raise FileNotFoundError("tesseract")

    monkeypatch.setattr(subprocess, "run", unavailable)
    dataset, _ = land_pdf(workshop, "native")
    assert dataset.source is not None, dataset.error
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.source.id))
    assert list(frame["text"]) == ["Invoice total: 1234.56 USD"]
    assert dataset.source_spec["sources"][0]["extraction"]["method"] == "docling-native-pdf"


def test_blank_pdf_reports_no_text_after_ocr(workshop):
    dataset, _ = land_pdf(workshop, "blank")
    assert dataset.state == "error" and dataset.source is None
    assert "No text was found after OCR" in dataset.error


@pytest.mark.parametrize("failure", ["missing", "timeout", "failed"])
def test_ocr_failure_never_lands_partial_native_rows(workshop, monkeypatch, failure):
    run = subprocess.run

    def fail(command, *args, **kwargs):
        if command[0] == "tesseract" and "--version" not in command:
            if failure == "missing":
                raise FileNotFoundError("tesseract")
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 60)
            raise subprocess.CalledProcessError(1, command)
        return run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail)
    dataset, _ = land_pdf(workshop, "mixed")
    assert dataset.state == "error" and dataset.source is None
    assert "OCR" in dataset.error
    assert "No partial source was landed" in dataset.error


def test_source_upload_merges_after_existing_cells_and_preserves_frozen_versions(
    workshop, django_capture_on_commit_callbacks
):
    client, project = workshop
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"rows": [{"text": "original"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    from conftest import import_version

    transformed = import_version(dataset, [{"text": "cleaned", "source_row": 0}], name="Cleaned")
    transformed.refresh_from_db()
    transformed.used_at = timezone.now()
    transformed.save(update_fields=["used_at"])
    original_bytes = paths.cell_path(dataset.id, dataset.source.id).read_bytes()
    frozen_bytes = paths.cell_path(dataset.id, transformed.id).read_bytes()
    dataset.cells.create(
        position=transformed.position + 1, title="Unpublished history", state="failed"
    )
    dataset.active = dataset.source
    dataset.save(update_fields=["active"])
    upload_id, _ = upload(client, "more.csv", b"text,category\nnew,example\n")
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/datasets/{dataset.id}/source/",
            {"uploads": [upload_id]},
            format="json",
        )
    assert response.status_code == 202, response.data
    dataset.refresh_from_db()
    merged = dataset.chain[-1]
    assert merged.review["kind"] == "attachment"
    assert merged.position == transformed.position + 2
    assert merged.rows == 2 and dataset.active_cell.id == merged.id
    frame = store.read_frame(paths.cell_path(dataset.id, merged.id))
    assert frame["text"].tolist() == ["cleaned", "new"]
    assert frame["source_row"].is_unique
    assert frame.iloc[1]["category"] == "example"
    assert frame.iloc[1]["_overmind_provenance"]["file"]["filename"] == "more.csv"
    assert paths.cell_path(dataset.id, dataset.source.id).read_bytes() == original_bytes
    assert paths.cell_path(dataset.id, transformed.id).read_bytes() == frozen_bytes
    artifact = dataset.source_spec["sources"][0]
    downloaded = client.get(f"/api/datasets/{dataset.id}/sources/{artifact['id']}/")
    assert b"".join(downloaded.streaming_content) == b"text,category\nnew,example\n"
    edited = client.patch(
        f"/api/datasets/{dataset.id}/cells/{merged.id}/",
        {"script": "df = df.head(0)"},
        format="json",
    )
    assert edited.status_code == 404


def test_source_attachment_batch_is_atomic_and_can_be_retried(
    workshop, django_capture_on_commit_callbacks
):
    client, project = workshop
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"rows": [{"text": "kept"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    valid, _ = upload(client, "valid.txt", b"A readable document.\n")
    bad = client.post("/api/uploads/", {"filename": "broken.json"}, format="json").data["upload_id"]
    client.put(
        f"/api/uploads/{bad}/chunk/?offset=0", b"{invalid", content_type="application/octet-stream"
    )
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/datasets/{dataset.id}/source/",
            {"uploads": [valid, bad]},
            format="json",
        )
    assert response.status_code == 202, response.data
    dataset.refresh_from_db()
    assert dataset.state == "error" and dataset.cells.count() == 1
    assert dataset.active_cell.rows == 1
    fresh, _ = upload(client, "fresh.txt", b"New document evidence.\n")
    with django_capture_on_commit_callbacks(execute=True):
        retried = client.post(
            f"/api/datasets/{dataset.id}/source/", {"uploads": [fresh]}, format="json"
        )
    assert retried.status_code == 202, retried.data
    dataset.refresh_from_db()
    assert dataset.active_cell.rows == 2
    frame = store.read_frame(paths.cell_path(dataset.id, dataset.active_cell.id))
    assert frame.iloc[1]["_overmind_provenance"]["evidence"]


def test_source_attachment_busy_scope_and_duplicate_delivery(
    workshop, monkeypatch, django_capture_on_commit_callbacks
):
    client, project = workshop
    created = client.post(
        "/api/datasets/",
        {"project": str(project.id), "source": {"rows": [{"text": "kept"}]}},
        format="json",
    )
    dataset = Dataset.objects.get(pk=created.data["id"])
    queued = []
    monkeypatch.setattr(tasks.land, "apply_async", lambda kwargs, **_: queued.append(kwargs))
    upload_id, _ = upload(client, "more.csv", b"text\nnew\n")
    with django_capture_on_commit_callbacks(execute=True):
        accepted = client.post(
            f"/api/datasets/{dataset.id}/source/", {"uploads": [upload_id]}, format="json"
        )
    assert accepted.status_code == 202
    busy = client.post(
        f"/api/datasets/{dataset.id}/source/", {"uploads": [upload_id]}, format="json"
    )
    assert busy.status_code == 409
    assert tasks.land(**queued[0])["status"] == "ok"
    tasks.land(**queued[0])
    dataset.refresh_from_db()
    assert dataset.cells.count() == 2 and dataset.active_cell.rows == 2
    foreign = Dataset.objects.create(
        project=Project.objects.create(name="Other", slug=uuid.uuid4().hex)
    )
    denied = client.post(
        f"/api/datasets/{foreign.id}/source/", {"rows": [{"text": "no"}]}, format="json"
    )
    assert denied.status_code == 404
