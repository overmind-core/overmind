"""The dataset API: create lands cell 0, the chain is edited and run, rows carry
diff marks, a raw export, and the settings freeze once a version is used."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import EVAL_ROWS, import_version, review_fixture
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Capability, Dataset, Project, ProjectMembership, Span, User
from overbae.services.datasets import paths, store, use

pytestmark = pytest.mark.django_db(transaction=True)

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


def test_create_lands_the_source_and_reads_back_with_cells():
    project = _project()
    client = _client(project)
    dataset = _create(client, project, rows=[dict(r) for r in EVAL_ROWS], intent="eval")
    res = client.get(f"/api/datasets/{dataset.id}/")
    assert res.status_code == 200
    body = res.data
    assert body["state"] == "idle" and body["intent"] == "eval"
    assert [c["version"] for c in body["cells"]] == ["1.0"]
    assert body["cells"][0]["fits"] == {"ok": True, "reason": ""}
    assert body["active_version"] == "1.0" and body["rows"] == 2
    assert "chat" not in body


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("choice", ["automatic", "none", "selected"])
def test_creation_distinguishes_no_capability_from_automatic_matching(split, choice):
    project = _project()
    client = _client(project)
    matched = Capability.objects.create(project=project, name="Matched", slug="matched")
    selected = Capability.objects.create(project=project, name="Selected", slug="selected")
    rows = [{**row, "capability_id": str(matched.id)} for row in EVAL_ROWS * 2]
    body = {"name": "Choice", "project": str(project.id), "source": {"rows": rows}}
    if choice != "automatic":
        body["capability"] = None if choice == "none" else str(selected.id)
    if split:
        body.update(eval_percent=30, position="tail")
    else:
        body["intent"] = "train"
    response = client.post(
        "/api/datasets/split/" if split else "/api/datasets/", body, format="json"
    )
    assert response.status_code == 201, response.data
    ids = (
        [response.data[role]["id"] for role in ("train", "eval")]
        if split
        else [response.data["id"]]
    )
    expected = {"automatic": matched.id, "none": None, "selected": selected.id}[choice]
    for dataset in Dataset.objects.filter(pk__in=ids):
        assert dataset.state == Dataset.State.IDLE, dataset.error
        assert dataset.capability_rank[0]["capability_id"] == str(matched.id)
        assert dataset.capability_id == expected
        if not split:
            assert dataset.intent == "train"


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


def test_rows_carry_diff_marks_against_the_cell_before():
    project = _project()
    client = _client(project)
    dataset = _create(client, project)
    shape = import_version(
        dataset, [dict(r, tag=r["tag"].upper(), source_row=i) for i, r in enumerate(ROWS)]
    )
    res = client.get(f"/api/datasets/{dataset.id}/rows/", {"cell": str(shape.id), "diff": "1"})
    assert res.status_code == 200
    marks = res.data["marks"]
    assert marks[0] == {"before": {"tag": "keep"}}
    assert len(marks) == 3


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


def test_list_filters_by_intent_and_shows_the_active_version():
    project = _project()
    client = _client(project)
    _create(client, project, rows=[dict(r) for r in EVAL_ROWS], intent="eval")
    _create(client, project, intent="train")
    res = client.get("/api/datasets/", {"project": str(project.id), "intent": "eval"})
    assert res.status_code == 200
    assert [d["intent"] for d in res.data["results"]] == ["eval"]
    assert res.data["results"][0]["active_version"] == "1.0"
    assert res.data["results"][0]["cells"] == []


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
