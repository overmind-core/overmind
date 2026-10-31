"""The dataset API: create lands cell 0, the chain is edited and run, rows carry
diff marks, a raw export, and the settings freeze once a version is used."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import EVAL_ROWS, import_version, review_fixture
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Capability,
    Dataset,
    OptimizerExperiment,
    Project,
    ProjectMembership,
    Span,
    User,
)
from overbae.services.datasets import paths, store, use

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.covers("lineage")]

ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _client(project) -> APIClient:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client


def _create(client, project, rows=ROWS, **extra):
    res = client.post(
        "/api/datasets/",
        {"name": "ds", "project": str(project.id), "source": {"rows": rows}, **extra},
        format="json",
    )
    assert res.status_code == 201, res.content
    return Dataset.objects.get(pk=res.data["id"])


def test_create_from_traces_validates_the_selection_before_creating():
    project = _project()
    client = _client(project)
    trace = uuid.uuid4().hex
    Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project=project,
        span_type="entry_point",
        name="run",
        start_time_ns=1,
        end_time_ns=2,
        duration_ns=1,
    )

    def post(traces):
        return client.post(
            "/api/datasets/",
            {"name": "t", "project": str(project.id), "source": {"traces": traces}},
            format="json",
        )

    unknown = post({"filters": {"capability_name": "x"}})
    assert unknown.status_code == 400 and "Allowed:" in str(unknown.data["source"])
    empty = post({"trace_ids": [uuid.uuid4().hex]})
    assert empty.status_code == 400 and "No traces match" in str(empty.data["source"])
    assert Dataset.objects.filter(project=project).count() == 0
    with patch("overbae.tasks.datasets.land.apply_async") as queued:
        created = post({"trace_ids": [trace, trace], "grain": "turn"})
    assert created.status_code == 201, created.content
    assert queued.call_args.kwargs["kwargs"]["source"] == {"traces": {"trace_ids": [trace]}}


@pytest.mark.covers("tenancy")
def test_create_rejects_two_sources_and_a_foreign_capability():
    project = _project()
    client = _client(project)
    res = client.post(
        "/api/datasets/",
        {"name": "x", "project": str(project.id), "source": {"rows": ROWS, "text": "a,b"}},
        format="json",
    )
    assert res.status_code == 400
    other = Capability.objects.create(project=_project(), name="Other", slug="other")
    res = client.post(
        "/api/datasets/",
        {
            "name": "x",
            "project": str(project.id),
            "capability": str(other.id),
            "source": {"rows": ROWS},
        },
        format="json",
    )
    assert res.status_code == 404


def test_row_pages_cross_storage_groups_without_changing_values_or_positions(monkeypatch):
    monkeypatch.setattr(store, "ROW_GROUP_SIZE", 3)
    project = _project()
    client = _client(project)
    values = [2**63 + 3, None, 0.25, False, "", {"p": [0.2, 0.8]}, [], "tail"]
    dataset = _create(client, project, rows=[{"value": value} for value in values])
    url = f"/api/datasets/{dataset.id}/rows/"
    for offset, limit, sort, direction, indices in [
        (0, 2, "", "asc", [0, 1]),
        (2, 4, "", "asc", [2, 3, 4, 5]),
        (6, 4, "_index", "asc", [6, 7]),
        (2, 4, "_index", "desc", [5, 4, 3, 2]),
        (7, 4, "_index", "desc", [0]),
        (8, 10, "", "asc", []),
        (20, 10, "_index", "desc", []),
    ]:
        response = client.get(
            url, {"offset": offset, "limit": limit, "sort": sort, "dir": direction}
        )
        assert response.status_code == 200, response.content
        assert response.data["total"] == len(values)
        assert [r["_index"] for r in response.data["rows"]] == indices
        assert [r["value"] for r in response.data["rows"]] == [values[i] for i in indices]
        assert [r["source_row"] for r in response.data["rows"]] == indices


def test_export_streams_a_version_raw_without_using_it():
    project = _project()
    client = _client(project)
    dataset = _create(client, project, intent="eval")
    keep = import_version(
        dataset,
        [dict(r, source_row=i) for i, r in enumerate(ROWS) if r["tag"] == "keep"],
        name="Keep",
    )
    shape = import_version(
        dataset,
        [
            dict(r, input=r["question"], expected_output=r["answer"])
            for r in store.iter_rows(paths.cell_path(dataset.pk, keep.pk))
        ],
        name="Shape",
    )
    res = client.get(f"/api/datasets/{dataset.id}/export/", {"fmt": "jsonl"})
    assert res.status_code == 200
    assert res["X-Overmind-Cell"] == str(shape.id) and res["X-Overmind-Version"] == "1.2"
    body = b"".join(res).decode()
    assert body.count("\n") == 2 and '"input"' in body
    res = client.get(f"/api/datasets/{dataset.id}/export/", {"fmt": "csv", "cell": str(keep.id)})
    assert res.status_code == 200 and res["X-Overmind-Version"] == "1.1"
    assert res["Content-Disposition"].endswith('.csv"')
    res = client.patch(f"/api/datasets/{dataset.id}/", {"intent": "train"}, format="json")
    assert res.status_code == 200


def test_patch_sets_capability_intent_and_active_cell():
    project = _project()
    client = _client(project)
    capability = Capability.objects.create(project=project, name="KB", slug="kb")
    dataset = _create(client, project)
    keep = import_version(
        dataset,
        [dict(r, source_row=i) for i, r in enumerate(ROWS) if r["tag"] == "keep"],
        name="Keep",
    )
    res = client.patch(
        f"/api/datasets/{dataset.id}/",
        {"capability": str(capability.id), "intent": "eval", "active": str(dataset.source.id)},
        format="json",
    )
    assert res.status_code == 200, res.content
    assert str(res.data["capability"]) == str(capability.id) and res.data["intent"] == "eval"
    assert res.data["active_version"] == "1.0"
    dataset.refresh_from_db()
    assert dataset.active_cell == dataset.source
    keep.refresh_from_db()
    assert keep.capability_report == {
        "ok": True,
        "rows": 2,
        "rows_ok": 2,
        "reason": "no input schema declared",
    }


def test_preparation_names_an_optimizer_experiment_by_its_capability():
    project = _project()
    client = _client(project)
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    dataset = _create(
        client,
        project,
        rows=[dict(r) for r in EVAL_ROWS],
        intent="eval",
        capability=str(capability.id),
    )
    review_fixture(dataset)
    cell = use.use(dataset, "eval")
    experiment = OptimizerExperiment.objects.create(
        project=project, capability=capability, dataset=dataset, cell=cell, entrypoint="answer"
    )
    res = client.get(f"/api/datasets/{dataset.id}/preparation/")
    assert res.status_code == 200, res.content
    usage = next(
        node["cell"]["usage"] for node in res.data["nodes"] if node["cell"]["id"] == str(cell.id)
    )
    assert usage["optimizer_experiments"][0]["id"] == str(experiment.id)
    assert usage["optimizer_experiments"][0]["name"] == "Support"
    assert usage["optimizer_experiments"][0]["status"] == experiment.status
    detail = client.get(f"/api/datasets/{dataset.id}/")
    assert detail.status_code == 200
    assert detail.data["cells"][0]["usage"]["optimizer_experiments"][0]["name"] == "Support"


def test_delete_refused_while_a_version_is_used():
    project = _project()
    client = _client(project)
    dataset = _create(client, project, rows=[dict(r) for r in EVAL_ROWS], intent="eval")
    review_fixture(dataset)
    use.use(dataset, "eval")
    res = client.delete(f"/api/datasets/{dataset.id}/")
    assert res.status_code == 409 and res.data["code"] == "dataset_referenced"
    Dataset.objects.filter(pk=dataset.pk).update()
    for cell in dataset.cells.all():
        cell.used_at = None
        cell.save(update_fields=["used_at"])
    res = client.delete(f"/api/datasets/{dataset.id}/")
    assert res.status_code == 204


@pytest.mark.covers("tenancy")
def test_another_project_reads_and_writes_nothing():
    project = _project()
    dataset = _create(_client(project), project, intent="eval")
    cell = import_version(
        dataset, [dict(r, source_row=i) for i, r in enumerate(ROWS) if r["tag"] == "keep"]
    )
    outsider_project = _project()
    outsider = _client(outsider_project)
    own = _create(outsider, outsider_project)
    base = f"/api/datasets/{dataset.id}"
    calls = [
        ("get", f"{base}/"),
        ("patch", f"{base}/"),
        ("delete", f"{base}/"),
        ("post", f"{base}/cells/"),
        ("patch", f"{base}/cells/{cell.id}/"),
        ("delete", f"{base}/cells/{cell.id}/"),
        ("post", f"{base}/cells/{cell.id}/accept/"),
        ("post", f"{base}/run/"),
        ("post", f"{base}/chat/"),
        ("get", f"{base}/rows/"),
        ("get", f"{base}/rows/0/"),
        ("get", f"{base}/columns/"),
        ("get", f"{base}/export/"),
        ("get", f"{base}/events/"),
    ]
    for method, url in calls:
        res = getattr(outsider, method)(url, {"name": "x", "message": "hi"}, format="json")
        assert res.status_code == 404, (method, url, res.status_code)
    res = outsider.get(f"/api/datasets/{own.id}/rows/", {"cell": str(cell.id)})
    assert res.status_code == 404
    res = outsider.get("/api/datasets/", {"project": str(project.id)})
    assert res.status_code == 200 and res.data["results"] == []
    assert Dataset.objects.filter(pk=dataset.pk, name="ds").exists()
