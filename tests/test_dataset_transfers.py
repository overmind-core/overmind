import hashlib
import json
import uuid

import pytest
from rest_framework.test import APIClient

from overbae.models import APIToken, Dataset, Project, ProjectMembership, User
from overbae.services.datasets import files, paths, store

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def transfer_client():
    user = User.objects.create_user(email="transfers@test.com", password="pw")
    project = Project.objects.create(name="Transfers", slug="transfers")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user)
    return client, project


def reserve(client, project, data=b'[{"input":"one"},{"input":"two"}]', **changes):
    body = {
        "project": str(project.pk),
        "request_key": "file-test",
        "filename": "rows.json",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    body.update(changes)
    response = client.post("/api/dataset-transfers/", body, format="json")
    assert response.status_code == 201, response.data
    return response.data, data


def chunk(client, receipt, data, offset=0):
    return client.put(
        f"/api/dataset-transfers/{receipt['id']}/chunk/?offset={offset}",
        data,
        content_type="application/octet-stream",
    )


def complete(client, receipt):
    return client.post(f"/api/dataset-transfers/{receipt['id']}/complete/", {}, format="json")


def test_interrupted_upload_and_lost_publication_ack_recover_exact_source(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(client, project)
    assert chunk(client, receipt, data[:10]).status_code == 200
    recovered, _ = reserve(client, project)
    assert recovered["id"] == receipt["id"]
    assert recovered["received"] == 10
    assert chunk(client, recovered, data[10:], 10).status_code == 200
    published = complete(client, receipt)
    assert published.status_code == 200, published.data
    replay = complete(client, receipt)
    assert replay.status_code == 200, replay.data
    assert replay.data["result"] == published.data["result"]
    dataset = Dataset.objects.get(pk=published.data["result"]["id"])
    assert dataset.state == "idle", dataset.error
    assert dataset.cells.count() == 1
    assert dataset.source.rows == 2
    assert store.read_frame(paths.cell_path(dataset.id, dataset.source.id))["input"].tolist() == [
        "one",
        "two",
    ]
    after_cleanup, _ = reserve(client, project)
    assert after_cleanup["result"] == published.data["result"]
    assert after_cleanup["received"] == len(data)
    assert Dataset.objects.filter(project=project).count() == 1
    assert chunk(client, receipt, b"changed").status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        {"sha256": "f" * 64},
        {"size": 999},
        {"intent": "train"},
        {"brief": "different"},
    ],
)
def test_changed_file_or_recipe_conflicts_without_creating_another_transfer(
    transfer_client, change
):
    client, project = transfer_client
    _, data = reserve(client, project)
    body = {
        "project": str(project.pk),
        "request_key": "file-test",
        "filename": "rows.json",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **change,
    }
    response = client.post("/api/dataset-transfers/", body, format="json")
    assert response.status_code == 409, response.data
    assert not Dataset.objects.exists()


def test_retransmission_rejects_different_bytes_and_completion_verifies_hash(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(client, project)
    assert chunk(client, receipt, data[:10]).status_code == 200
    assert chunk(client, receipt, data[:10]).status_code == 200
    assert chunk(client, receipt, b"x" * 10).status_code == 409
    assert complete(client, receipt).status_code == 409
    assert chunk(client, receipt, b"x" * (len(data) - 10), 10).status_code == 200
    response = complete(client, receipt)
    assert response.status_code == 409
    assert response.data["code"] == "file_hash_mismatch"
    assert not Dataset.objects.exists()


def test_transfer_project_isolation_and_expired_bytes(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(client, project)
    other = User.objects.create_user(email="other-transfer@test.com", password="pw")
    stranger = APIClient()
    stranger.force_authenticate(other)
    assert stranger.get(f"/api/dataset-transfers/{receipt['id']}/").status_code == 404
    assert chunk(stranger, receipt, data).status_code == 404
    assert complete(stranger, receipt).status_code == 404
    files.discard_upload(receipt["upload_id"])
    inspected = client.get(f"/api/dataset-transfers/{receipt['id']}/")
    assert inspected.data["staging_available"] is False
    assert inspected.data["next_action"] == "start_new_transfer_with_new_request_key"
    response = complete(client, receipt)
    assert response.status_code == 409
    assert response.data["code"] == "transfer_bytes_missing"
    assert not Dataset.objects.exists()


def test_repeated_attachment_does_not_append_another_cell(transfer_client):
    client, project = transfer_client
    draft = Dataset.objects.create(
        project=project, name="Draft", brief="Keep this brief", state="idle"
    )
    receipt, data = reserve(client, project, dataset=str(draft.pk))
    assert chunk(client, receipt, data).status_code == 200
    result = complete(client, receipt)
    assert result.status_code == 200, result.data
    assert complete(client, receipt).status_code == 200
    draft.refresh_from_db()
    assert draft.brief == "Keep this brief"
    assert draft.cells.count() == 1
    assert Dataset.objects.count() == 1


def test_split_publication_returns_same_pair(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(client, project, split=50)
    assert chunk(client, receipt, data).status_code == 200
    first = complete(client, receipt)
    assert first.status_code == 200, first.data
    second = complete(client, receipt)
    assert first.data["result"] == second.data["result"]
    assert Dataset.objects.count() == 2
    assert sorted(d.source.rows for d in Dataset.objects.all()) == [1, 1]


def test_foreign_targets_and_oversized_input_rejected_before_reservation(transfer_client):
    client, project = transfer_client
    for values in ({"dataset": str(uuid.uuid4())}, {"size": files.MAX_UPLOAD_BYTES + 1}):
        response = client.post(
            "/api/dataset-transfers/",
            {
                "project": str(project.pk),
                "request_key": "invalid",
                "filename": "rows.json",
                "size": 10,
                "sha256": "0" * 64,
                **values,
            },
            format="json",
        )
        assert response.status_code in (400, 404), response.data


def test_document_limit_explains_rejection_without_reserving_bytes(transfer_client):
    client, project = transfer_client
    response = client.post(
        "/api/dataset-transfers/",
        {
            "project": str(project.pk),
            "request_key": "oversized-pdf",
            "filename": "large.pdf",
            "size": files.upload_byte_limit("large.pdf") + 1,
            "sha256": "0" * 64,
        },
        format="json",
    )
    assert response.status_code == 400
    assert response.data["code"] == ["file_too_large"]
    assert not project.datasettransfer_set.exists()


def test_lost_broker_acknowledgement_recovers_without_recreating_dataset(
    transfer_client, monkeypatch
):
    from overbae.tasks.datasets import land

    client, project = transfer_client
    receipt, data = reserve(client, project)
    assert chunk(client, receipt, data).status_code == 200
    original = land.apply_async

    def unavailable(**kwargs):
        raise ConnectionError("broker offline")

    monkeypatch.setattr(land, "apply_async", unavailable)
    first = complete(client, receipt)
    assert first.status_code == 200
    assert first.data["dispatch"] == "pending"
    monkeypatch.setattr(land, "apply_async", original)
    recovered = complete(client, receipt)
    assert recovered.data["dispatch"] == "acknowledged"
    assert recovered.data["result"] == first.data["result"]
    assert Dataset.objects.count() == 1
    assert Dataset.objects.get().state == "idle"


def test_managed_transfer_cannot_be_modified_or_published_through_unscoped_staging(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(client, project)
    response = client.put(
        f"/api/uploads/{receipt['upload_id']}/chunk/?offset=0",
        data,
        content_type="application/octet-stream",
    )
    assert response.status_code == 403
    response = client.post(
        "/api/datasets/",
        {
            "project": str(project.pk),
            "source": {"upload_id": receipt["upload_id"], "filename": "rows.json"},
        },
        format="json",
    )
    assert response.status_code == 400
    assert not Dataset.objects.exists()


def test_read_only_key_reports_partial_readiness_and_cannot_upload(transfer_client):
    client, project = transfer_client
    user = ProjectMembership.objects.get(project=project).user
    raw, token = APIToken.create_for_user(user, name="read transfer", permission=["read"])
    client.force_authenticate(None)
    client.credentials(HTTP_X_API_KEY=raw)
    response = client.get(f"/api/dataset-transfers/readiness/?project={project.pk}")
    assert response.status_code == 200, response.data
    assert response.data["can_export"] and not response.data["can_upload"]
    assert client.post("/api/dataset-transfers/", {}, format="json").status_code == 403


def test_mcp_transfer_receipt_is_passive_and_project_scoped(transfer_client):
    from asgiref.sync import async_to_sync

    from overbae.services.mcp.catalog import CATALOG
    from overbae.services.mcp.context import MCPContext

    client, project = transfer_client
    receipt, data = reserve(client, project)
    assert chunk(client, receipt, data[:10]).status_code == 200
    user = ProjectMembership.objects.get(project=project).user
    _, token = APIToken.create_for_user(user, name="inspect transfer")
    context = MCPContext(user=user, token=token, project=project)
    result = async_to_sync(CATALOG.call)(
        "get_job", {"kind": "dataset_transfer", "id": receipt["id"]}, context
    )
    assert not result.isError, result
    assert result.structuredContent["progress"]["bytes_received"] == 10
    assert not Dataset.objects.exists()
    other = Project.objects.create(name="Other", slug="other")
    ProjectMembership.objects.create(user=user, project=other)
    result = async_to_sync(CATALOG.call)(
        "get_job",
        {"kind": "dataset_transfer", "id": receipt["id"], "project_id": str(other.pk)},
        context,
    )
    assert result.isError


def test_explicit_json_row_selection_preserves_wrapper_bytes_and_nested_values(transfer_client):
    client, project = transfer_client
    original = {
        "metadata": {"meaning": "unknown"},
        "pairs": [
            {"left": {"id": "007"}, "right": {"id": "008"}, "judgement": {"weights": [0.2, 0.8]}},
            {"left": {"id": "007"}, "right": {"id": "008"}, "judgement": {"weights": [0.2, 0.8]}},
        ],
    }
    data = json.dumps(original).encode()
    receipt, _ = reserve(client, project, data, json_rows_field="pairs")
    assert chunk(client, receipt, data).status_code == 200
    response = complete(client, receipt)
    dataset = Dataset.objects.get(pk=response.data["result"]["id"])
    assert dataset.source.rows == 2
    artifact = dataset.source_spec["sources"][0]
    assert artifact["extraction"]["json_rows_field"] == "pairs"
    assert paths.source_path(dataset.pk, artifact["id"]).read_bytes() == data
    exported = list(store.iter_rows(paths.cell_path(dataset.pk, dataset.source.pk)))
    assert [
        {key: row[key] for key in ("left", "right", "judgement")} for row in exported
    ] == original["pairs"]


def test_missing_explicit_json_field_fails_landing_without_silently_using_wrapper(transfer_client):
    client, project = transfer_client
    receipt, data = reserve(
        client, project, b'{"metadata":{},"other":[{"a":1}]}', json_rows_field="pairs"
    )
    assert chunk(client, receipt, data).status_code == 200
    response = complete(client, receipt)
    dataset = Dataset.objects.get(pk=response.data["result"]["id"])
    assert dataset.state == "error"
    assert not dataset.cells.exists()
