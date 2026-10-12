"""The dataset API: create lands v0, versions are read (never added), rows carry diff
marks against the parent, a raw export, runs are listed, and settings freeze once used."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import EVAL_ROWS, add_versions
from factories import make_project, member_client

from overbae.models import Capability, Dataset, Execution, Span
from overbae.services.datasets import executions, use

pytestmark = pytest.mark.django_db(transaction=True)

ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


def _create(client, project, rows=ROWS, **extra):
    res = client.post(
        "/api/datasets/",
        {"name": "ds", "project": str(project.id), "source": {"rows": rows}, **extra},
        format="json",
    )
    assert res.status_code == 201, res.content
    return Dataset.objects.get(pk=res.data["id"])


def test_create_lands_the_source_and_reads_back_with_versions():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project, rows=[dict(r) for r in EVAL_ROWS])
    res = client.get(f"/api/datasets/{dataset.id}/")
    assert res.status_code == 200
    body = res.data
    assert body["state"] == "idle" and body["intent"] == "eval"
    assert [(c["version"], c["parent"]) for c in body["cells"]] == [("v0", None)]
    assert body["cells"][0]["fits"] == {"ok": True, "reason": ""}
    assert body["active_version"] == "v0" and body["rows"] == 2
    assert "chat" not in body


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("choice", ["automatic", "none", "selected"])
def test_creation_distinguishes_no_capability_from_automatic_matching(split, choice):
    project = make_project()
    client = member_client(project)
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
    project = make_project()
    client = member_client(project)
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
    project = make_project()
    client = member_client(project)
    res = client.post(
        "/api/datasets/",
        {"name": "x", "project": str(project.id), "source": {"rows": ROWS, "text": "a,b"}},
        format="json",
    )
    assert res.status_code == 400
    other = Capability.objects.create(project=make_project(), name="Other", slug="other")
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


def test_versions_are_read_but_never_added_and_runs_are_listed():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project)
    add_versions(dataset, [("Keep", KEEP)])
    executions.run_now(
        dataset,
        mode=Execution.Mode.CELL,
        parent=dataset.active_cell,
        script="df = df['missing']\n" + "#" * 400,
        title="Broken",
    )
    base = f"/api/datasets/{dataset.id}"
    assert client.post(f"{base}/cells/", {"title": "T", "script": KEEP}).status_code == 404
    assert client.post(f"{base}/run/").status_code == 404
    assert client.post(f"{base}/chat/", {"message": "hi"}).status_code == 404

    res = client.get(f"{base}/executions/")
    assert res.status_code == 200
    failed, done = res.data
    assert (failed["mode"], failed["state"], failed["parent"], failed["cell"]) == (
        "cell",
        "failed",
        "v1",
        None,
    )
    assert "missing" in failed["error"] and len(failed["error"]) <= 300
    assert (done["state"], done["parent"], done["cell"], done["title"]) == (
        "ok",
        "v0",
        "v1",
        "Keep",
    )
    assert done["run_seconds"] is not None
    assert len(client.get(f"{base}/executions/", {"limit": 1}).data) == 1


def test_rows_carry_diff_marks_against_the_parent():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project)
    add_versions(dataset, [("Upper", "df['tag'] = df['tag'].str.upper()\n")])
    res = client.get(f"/api/datasets/{dataset.id}/rows/", {"cell": "v1", "diff": "1"})
    assert res.status_code == 200
    marks = res.data["marks"]
    assert marks[0] == {"before": {"tag": "keep"}}
    assert len(marks) == 3


def test_export_streams_a_version_raw_without_using_it():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project, intent="eval")
    add_versions(dataset, [("Keep", KEEP), ("Shape", SHAPE)])
    shape = dataset.active_cell
    keep = shape.parent
    res = client.get(f"/api/datasets/{dataset.id}/export/", {"fmt": "jsonl"})
    assert res.status_code == 200
    assert res["X-Overmind-Cell"] == str(shape.id) and res["X-Overmind-Version"] == "v2"
    body = b"".join(res).decode()
    assert body.count("\n") == 2 and '"input"' in body
    res = client.get(f"/api/datasets/{dataset.id}/export/", {"fmt": "csv", "cell": str(keep.id)})
    assert res.status_code == 200 and res["X-Overmind-Version"] == "v1"
    assert res["Content-Disposition"].endswith('.csv"')
    res = client.patch(f"/api/datasets/{dataset.id}/", {"intent": "train"}, format="json")
    assert res.status_code == 200


def test_patch_sets_capability_intent_and_the_active_version_with_a_conflict_check():
    project = make_project()
    client = member_client(project)
    capability = Capability.objects.create(project=project, name="KB", slug="kb")
    dataset = _create(client, project)
    add_versions(dataset, [("Keep", KEEP)])
    keep = dataset.active_cell
    stale = client.patch(
        f"/api/datasets/{dataset.id}/",
        {"active": str(dataset.source.id), "expected_active": str(dataset.source.id)},
        format="json",
    )
    assert stale.status_code == 409 and stale.data["code"] == "active_conflict"
    res = client.patch(
        f"/api/datasets/{dataset.id}/",
        {
            "capability": str(capability.id),
            "intent": "eval",
            "active": str(dataset.source.id),
            "expected_active": str(keep.id),
        },
        format="json",
    )
    assert res.status_code == 200, res.content
    assert str(res.data["capability"]) == str(capability.id) and res.data["intent"] == "eval"
    assert res.data["active_version"] == "v0"
    dataset.refresh_from_db()
    assert dataset.active_cell == dataset.source
    keep.refresh_from_db()
    assert keep.capability_report == {
        "ok": True,
        "rows": 2,
        "rows_ok": 2,
        "reason": "no input schema declared",
    }


def test_a_landing_dataset_refuses_settings_and_delete_and_resume_needs_a_blocked_import():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project)
    base = f"/api/datasets/{dataset.id}"
    assert client.post(f"{base}/resume-import/").status_code == 409
    Dataset.objects.filter(pk=dataset.pk).update(state="landing")
    res = client.patch(f"{base}/", {"intent": "train"}, format="json")
    assert res.status_code == 400 and res.data["code"] == "landing"
    assert client.delete(f"{base}/").status_code == 409
    assert Dataset.objects.get(pk=dataset.pk).intent != "train"


def test_list_filters_by_intent_and_shows_the_active_version():
    project = make_project()
    client = member_client(project)
    _create(client, project, rows=[dict(r) for r in EVAL_ROWS])
    _create(client, project, intent="train")
    res = client.get("/api/datasets/", {"project": str(project.id), "intent": "eval"})
    assert res.status_code == 200
    assert [d["intent"] for d in res.data["results"]] == ["eval"]
    assert res.data["results"][0]["active_version"] == "v0"
    assert res.data["results"][0]["cells"] == []


def test_delete_refused_while_a_version_is_used():
    project = make_project()
    client = member_client(project)
    dataset = _create(client, project, rows=[dict(r) for r in EVAL_ROWS], intent="eval")
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
    project = make_project()
    dataset = _create(member_client(project), project, intent="eval")
    add_versions(dataset, [("Keep", KEEP)])
    cell = dataset.active_cell
    outsider_project = make_project()
    outsider = member_client(outsider_project)
    own = _create(outsider, outsider_project)
    base = f"/api/datasets/{dataset.id}"
    calls = [
        ("get", f"{base}/"),
        ("patch", f"{base}/"),
        ("delete", f"{base}/"),
        ("post", f"{base}/resume-import/"),
        ("get", f"{base}/executions/"),
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
