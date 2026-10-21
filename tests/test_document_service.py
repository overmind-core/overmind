from pathlib import Path

import httpx
import pytest

from overbae.services.datasets import documents
from sandbox.protocol import header_bytes


def test_parser_refuses_scripts_and_mismatched_bytes(sandbox_server):
    url, token = sandbox_server
    for header, payload in [
        ({"mode": "cell", "script": "print('unsafe')", "input_bytes": 0}, b""),
        ({"mode": "extract", "filename": "note.txt", "input_bytes": 3}, b"ab"),
        ({"mode": "extract", "filename": "note.txt", "input_bytes": True}, b"x"),
    ]:
        response = httpx.post(
            f"{url}/v1/run",
            content=header_bytes(header) + payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        assert response.status_code == 400
    response = httpx.post(f"{url}/v1/run", content=b"", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_parser_reports_ocr_progress_and_retained_identity():
    path = Path(__file__).parent / "fixtures/documents/scanned.pdf"
    progress = []
    rows, metadata = documents.extract(path, filename="scanned.pdf", on_progress=progress.append)
    assert metadata["ocr"]["pages"] == [1]
    assert any(event.get("stage") == "ocr" for event in progress)
    assert rows[0]["_overmind_document_id"]
    assert rows[0]["_overmind_provenance"]["evidence"][0]["filename"] == "scanned.pdf"


def test_parser_configuration_has_no_in_process_fallback(settings, tmp_path):
    settings.SANDBOX_URL = ""
    path = tmp_path / "note.txt"
    path.write_text("Retained source")
    with pytest.raises(documents.DocumentError, match="not configured"):
        documents.extract(path, filename=path.name)


@pytest.mark.django_db(transaction=True)
def test_completed_ocr_pages_survive_interruption_and_stay_project_scoped():
    import uuid

    from overbae.models import DocumentExtractionPage, Project

    project = Project.objects.create(name="OCR recovery", slug=uuid.uuid4().hex)
    other = Project.objects.create(name="Other OCR", slug=uuid.uuid4().hex)
    path = Path(__file__).parent / "fixtures/documents/mixed.pdf"

    def interrupt(progress):
        if progress.get("ocr_pages_completed", 0) >= 1:
            raise RuntimeError("client interrupted")

    with pytest.raises(RuntimeError, match="client interrupted"):
        documents.extract(path, filename=path.name, project_id=project.pk, on_progress=interrupt)
    saved = DocumentExtractionPage.objects.filter(project=project).count()
    assert 1 <= saved <= 2
    rows, metadata = documents.extract(path, filename="renamed.pdf", project_id=project.pk)
    assert metadata["ocr"]["pages_reused"] == saved
    assert [row["page"] for row in rows] == sorted(row["page"] for row in rows)
    assert all(row["source_name"] == "renamed.pdf" for row in rows)
    again, reused = documents.extract(path, filename="renamed.pdf", project_id=project.pk)
    assert again == rows
    assert reused["ocr"]["pages_reused"] == 2
    _, isolated = documents.extract(path, filename=path.name, project_id=other.pk)
    assert isolated["ocr"]["pages_reused"] == 0
    _, explicit = documents.extract(
        path, filename=path.name, project_id=project.pk, ocr_languages=["eng"]
    )
    assert explicit["ocr"]["pages_reused"] == 0
    assert explicit["ocr"]["requested_languages"] == ["eng"]
    assert explicit["ocr"]["languages"] == ["eng"]
    assert explicit["ocr"]["recipe_sha256"] != metadata["ocr"]["recipe_sha256"]


def test_progress_throttle_preserves_boundaries_and_terminal_counters():
    from overbae.services.datasets.progress import ProgressThrottle

    published = []
    progress = ProgressThrottle(published.append, interval=3600)
    for completed in range(101):
        progress({"stage": "ocr", "ocr_pages_completed": completed, "ocr_pages_total": 100})
    progress({"stage": "publishing"})
    progress({"stage": "failed"})
    assert len(published) == 4
    assert [item.get("ocr_pages_completed") for item in published[:2]] == [0, 100]
    assert [item["stage"] for item in published[2:]] == ["publishing", "failed"]


def test_default_english_and_explicit_french_ocr(tmp_path):
    import textwrap

    from PIL import Image, ImageDraw, ImageFont

    text = (
        "Bonjour, veuillez vérifier votre facture avant de confirmer le paiement. "
        "Nous vous remercions pour votre confiance. Cette demande contient les informations "
        "nécessaires pour protéger votre compte et signaler un message suspect. "
        "Ne partagez jamais votre mot de passe avec une personne inconnue."
    )
    path = tmp_path / "message.png"
    with Image.new("RGB", (1200, 420), "white") as image:
        ImageDraw.Draw(image).multiline_text(
            (25, 25),
            textwrap.fill(text, 65),
            fill="black",
            font=ImageFont.load_default(size=28),
            spacing=14,
        )
        image.save(path)
    _, default = documents.extract(path, filename=path.name)
    assert default["ocr"]["languages"] == ["eng"]
    rows, metadata = documents.extract(path, filename=path.name, ocr_languages=["fra"])
    assert metadata["ocr"]["languages"] == ["fra"]
    assert "facture" in " ".join(row["text"] for row in rows)
    assert all(row["_overmind_provenance"]["extraction"]["languages"] == ["fra"] for row in rows)


def test_ocr_worker_budget_stays_bounded(monkeypatch):
    from sandbox.documents.ocr import worker_count

    monkeypatch.setenv("SANDBOX_OCR_WORKERS", "100")
    assert worker_count(1024**3) == 1
    assert 1 <= worker_count(2 * 1024**3) <= 2
    assert 1 <= worker_count(16 * 1024**3) <= 4


def test_checkpoint_page_limit_round_trips_with_jsonl_delimiter(tmp_path, monkeypatch):
    import json

    from sandbox.documents import checkpoints

    result = {"rows": [], "engine": {}}
    value = checkpoints.record("a" * 64, 1, result)
    monkeypatch.setattr(checkpoints, "MAX_PAGE_BYTES", len(json.dumps(value).encode()))
    value = checkpoints.record("a" * 64, 1, result)
    path = tmp_path / "checkpoints.jsonl"
    path.write_text(json.dumps(value) + "\n")
    assert checkpoints.read(path) == {1: value}
    with pytest.raises(ValueError, match="page exceeds"):
        checkpoints.record("a" * 64, 1, {"rows": [{"text": "larger"}], "engine": {}})


def test_progress_backpressure_preserves_checkpoint_and_does_not_block_watchdog(tmp_path):
    from sandbox.execute import read_progress

    line = '{"ocr_checkpoint": {"page": 1}}\n'
    (tmp_path / "progress.jsonl").write_text(line)
    assert read_progress(tmp_path, 0, lambda value: False) == (0, True)
    received = []
    assert read_progress(tmp_path, 0, received.append) == (len(line), False)
    assert received == [{"ocr_checkpoint": {"page": 1}}]


@pytest.mark.django_db(transaction=True)
def test_corrupt_checkpoint_is_recomputed_and_expiry_is_bounded():
    import uuid
    from datetime import timedelta

    from django.utils import timezone

    from overbae.models import DocumentExtractionPage, Project
    from overbae.services.datasets.ocr_cache import prune

    project = Project.objects.create(name="OCR integrity", slug=uuid.uuid4().hex)
    path = Path(__file__).parent / "fixtures/documents/text.png"
    original, metadata = documents.extract(path, filename=path.name, project_id=project.pk)
    assert metadata["ocr"]["pages_reused"] == 0
    DocumentExtractionPage.objects.filter(project=project).update(result_sha256="0" * 64)
    rows, metadata = documents.extract(path, filename=path.name, project_id=project.pk)
    assert rows == original
    assert metadata["ocr"]["pages_reused"] == 0
    DocumentExtractionPage.objects.filter(project=project).update(
        created_at=timezone.now() - timedelta(days=31)
    )
    assert prune() == 1
    assert not DocumentExtractionPage.objects.filter(project=project).exists()


def test_old_parser_clients_keep_english_ocr_without_internal_checkpoint_frames(sandbox_server):
    from sandbox.protocol import HeaderReader

    url, token = sandbox_server
    path = Path(__file__).parent / "fixtures/documents/scanned.pdf"
    payload = path.read_bytes()
    response = httpx.post(
        f"{url}/v1/run",
        content=header_bytes(
            {"mode": "extract", "filename": path.name, "input_bytes": len(payload)}
        )
        + payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    response.raise_for_status()
    pending = response.content
    progress = []
    while True:
        reader = HeaderReader()
        pending = reader.feed(pending)
        value = reader.finish()
        if "progress" not in value:
            assert value["ok"]
            assert value["extraction"]["ocr"]["languages"] == ["eng"]
            break
        progress.append(value["progress"])
    assert any(value.get("stage") == "ocr" for value in progress)
    assert not any("ocr_checkpoint" in value for value in progress)


def test_script_like_content_does_not_change_ocr_language():
    path = Path(__file__).parent / "fixtures/documents/unsupported-script.pdf"
    rows, metadata = documents.extract(path, filename=path.name)
    assert metadata["ocr"]["languages"] == ["eng"]
    assert "language_detection" not in metadata["ocr"]
    assert "Bono" in " ".join(row["text"] for row in rows)
