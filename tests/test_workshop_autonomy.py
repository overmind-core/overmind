import uuid

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import land, lifecycle, paths, review, store
from overbae.services.datasets.notebook import engines, run
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("failure", [False, True], ids=["complete", "provider_failure"])
@pytest.mark.parametrize("saved_suggestions", [False, True], ids=["fresh", "unfinished"])
def test_chat_applies_dependent_changes_once_and_retains_source(
    settings, tmp_path, monkeypatch, failure, saved_suggestions
):
    settings.MEDIA_ROOT = tmp_path
    project = Project.objects.create(name="Autonomous workshop", slug=uuid.uuid4().hex)
    user = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.test", password="test")
    ProjectMembership.objects.create(project=project, user=user)
    dataset = Dataset.objects.create(project=project, intent="eval")
    land.land_rows(
        dataset,
        [
            {"input": "one", "expected_output": "YES", "keep": True},
            {"input": "two", "expected_output": "NO", "keep": False},
        ],
    )
    dataset.refresh_from_db()
    source = dataset.source
    original = list(store.iter_rows(paths.cell_path(dataset.id, source.id)))
    calls = []
    if saved_suggestions:
        for title, script in (
            ("Normalise labels", "df['expected_output'] = df['expected_output'].str.lower()"),
            ("Keep selected rows", "df = df[df['keep']]"),
        ):
            cell = lifecycle.add_cell(dataset, title=title, script=script, proposed=True)
            preview = run.try_script(dataset, script, after=source)
            review.save_proposal(dataset, cell, source, preview.path, kind="semantic", note="")

    class Engine:
        name = "fixture"

        def run(self, current, message, tools, pending):
            calls.append(message)
            if saved_suggestions:
                assert "Normalise labels" in message and "Keep selected rows" in message
            first = tools.handlers()["add_cell"](
                {
                    "title": "Normalise labels",
                    "script": "df['expected_output'] = df['expected_output'].str.lower()",
                    "kind": "semantic",
                }
            )
            assert first["ok"] and not first.get("proposed"), first
            if not failure:
                second = tools.handlers()["add_cell"](
                    {
                        "title": "Keep selected rows",
                        "script": "df = df[df['keep']]",
                        "kind": "semantic",
                    }
                )
                assert second["ok"] and not second.get("proposed"), second
            while pending:
                yield pending.pop(0)
            return engines.Outcome(
                text="Applied the requested changes.",
                error="Provider disconnected" if failure else "",
            )

    monkeypatch.setattr(engines, "select", lambda user=None: Engine())
    queued = []
    monkeypatch.setattr(tasks.turn, "apply_async", lambda **kwargs: queued.append(kwargs))
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    response = client.post(
        f"/api/datasets/{dataset.id}/chat/",
        {"message": "Lowercase the labels, then keep only rows marked keep."},
        format="json",
    )
    assert response.status_code == 202, response.data
    assert len(queued) == 1
    task_id = str(uuid.uuid4())
    tasks.turn.apply(kwargs=queued[0]["kwargs"], task_id=task_id)
    dataset.refresh_from_db()
    assert len(calls) == 1
    assert dataset.chat[-1]["status"] == ("error" if failure else "complete")
    assert dataset.chat[-1]["error"] == ("Provider disconnected" if failure else "")
    assert not dataset.cells.filter(state="proposed").exists()
    assert dataset.cells.count() == (2 if failure else 3)
    active = dataset.active_cell
    records = list(store.iter_rows(paths.cell_path(dataset.id, active.id)))
    assert records[0]["expected_output"] == "yes"
    assert len(records) == (2 if failure else 1)
    assert list(store.iter_rows(paths.cell_path(dataset.id, source.id))) == original
    if not failure:
        previous = dataset.cells.get(position=1)
        assert active.review["input_fingerprint"] == previous.fingerprint
        assert active.review["rows_removed"] == 1
    saved = client.get(f"/api/datasets/{dataset.id}/").data
    assert saved["chat"][-1]["status"] == dataset.chat[-1]["status"]
    serialized = serialize_dataset_detail(dataset)
    assert all(action.tool != "run_dataset" for action in serialized.next_actions)
    tasks.turn.apply(kwargs=queued[0]["kwargs"], task_id=task_id)
    dataset.refresh_from_db()
    assert len(calls) == 1
    assert dataset.cells.count() == (2 if failure else 3)
