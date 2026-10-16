import hashlib

import pytest
import requests
from factories import make_project, make_user

from overbae.models import APIToken
from overbae.services.datasets import files

pytestmark = pytest.mark.django_db(transaction=True)


def test_interrupted_chunk_sequence_resumes_without_duplicating_bytes(live_api):
    user = make_user()
    project = make_project(member=user)
    key, _ = APIToken.create_for_user(user, project=project)
    headers = {"X-Api-Key": key}
    content = b"input\n" + b"".join(f"{row}-{'x' * 1024}\n".encode() for row in range(16384))
    reserved = requests.post(
        live_api.url + "/api/uploads/", headers=headers, json={"filename": "resume.csv"}, timeout=10
    )
    assert reserved.status_code == 201
    upload_id = reserved.json()["upload_id"]
    chunk = 1024 * 1024
    first = requests.put(
        f"{live_api.url}/api/uploads/{upload_id}/chunk/?offset=0",
        headers=headers,
        data=content[:chunk],
        timeout=10,
    )
    assert first.status_code == 200 and first.json()["received"] == chunk
    incomplete = requests.post(
        f"{live_api.url}/api/uploads/{upload_id}/inspect/",
        headers=headers,
        json={"size": len(content)},
        timeout=10,
    )
    assert incomplete.status_code == 400
    # The client lost the acknowledgement and resends the same offset after reconnecting.
    for offset in range(0, len(content), chunk):
        response = requests.put(
            f"{live_api.url}/api/uploads/{upload_id}/chunk/?offset={offset}",
            headers=headers,
            data=content[offset : offset + chunk],
            timeout=10,
        )
        assert response.status_code == 200
        assert response.json()["received"] == min(offset + chunk, len(content))
    inspected = requests.post(
        f"{live_api.url}/api/uploads/{upload_id}/inspect/",
        headers=headers,
        json={"size": len(content)},
        timeout=30,
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json()["rows"] == 16384
    with files.upload_data_path(upload_id).open("rb") as source:
        assert hashlib.file_digest(source, "sha256").digest() == hashlib.sha256(content).digest()
