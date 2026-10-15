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
