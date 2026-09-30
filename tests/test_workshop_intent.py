import uuid

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Dataset, Project, ProjectMembership, User
from overbae.services.datasets import land
from overbae.services.datasets.notebook import agent, engines
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db


@pytest.fixture
def workshop():
    project = Project.objects.create(name="Intent workshop", slug=uuid.uuid4().hex)
    user = User.objects.create_user(email=f"{uuid.uuid4().hex}@example.test", password="test")
    ProjectMembership.objects.create(project=project, user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client, project


def dataset_for(project, **fields):
    dataset = Dataset.objects.create(project=project, name="Examples", **fields)
    land.land_rows(dataset, [{"input": "source fact", "expected_output": "answer"}])
    return dataset


def pause(dataset, monkeypatch):
    monkeypatch.setattr(engines, "select", lambda user=None: None)
    list(agent.diagnose(dataset.id))
    dataset.refresh_from_db()
    return dataset.chat[-1]


@pytest.mark.parametrize(
    "rows",
    [
        [{"input": "source fact", "expected_output": "answer"}],
        [{"messages": [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]}],
    ],
)
def test_landing_never_chooses_a_purpose_from_the_rows(workshop, rows):
    _, project = workshop
    dataset = Dataset.objects.create(project=project)
    land.land_rows(dataset, rows)
    dataset.refresh_from_db()
    assert dataset.intent == "pending"
    assert dataset.cells.count() == 1


def test_missing_intent_pauses_without_preparing_even_if_engine_attempts_it(workshop, monkeypatch):
    _, project = workshop
    dataset = dataset_for(project, brief="Clean these examples")

    class Engine:
        name = "fixture"

        def run(self, dataset, message, tools, pending):
            result = tools.handlers()["add_cell"]({"title": "Shape", "script": "df['x'] = 1"})
            assert result["ok"] is False
            result = tools.handlers()["set_intent"]({"intent": "eval"})
            assert result["ok"] is False
            while pending:
                yield pending.pop(0)
            return engines.Outcome(text="I assumed evaluation.")

    monkeypatch.setattr(engines, "select", lambda user=None: Engine())
    list(agent.diagnose(dataset.id))
    dataset.refresh_from_db()
    assert dataset.state == "idle" and dataset.intent == "pending"
    assert dataset.cells.count() == 1
    assert dataset.chat[-1]["status"] == "awaiting_intent"
    assert dataset.chat[-1]["text"] == "What will you use this data for?"


@pytest.mark.parametrize(
    "intent,brief",
    [
        ("train", "Prepare this for training"),
        ("eval", "Prepare this for evaluation"),
        ("explore", "I want data exploration"),
    ],
)
def test_explicit_prompt_intent_continues_without_a_question(workshop, monkeypatch, intent, brief):
    _, project = workshop
    dataset = dataset_for(project, brief=brief)

    class Engine:
        name = "fixture"

        def run(self, dataset, message, tools, pending):
            result = tools.handlers()["set_intent"]({"intent": intent, "evidence": brief})
            assert result["ok"]
            while pending:
                yield pending.pop(0)
            return engines.Outcome(text="Intent recorded.")

    monkeypatch.setattr(engines, "select", lambda user=None: Engine())
    list(agent.diagnose(dataset.id))
    dataset.refresh_from_db()
    assert dataset.intent == intent
    assert dataset.chat[-1]["status"] == "complete"


@pytest.mark.parametrize("intent", ["train", "eval", "explore"])
def test_answer_survives_reload_and_resumes_original_request_once(workshop, monkeypatch, intent):
    client, project = workshop
    dataset = dataset_for(project, brief="Clean these examples")
    question = pause(dataset, monkeypatch)
    saved = client.get(f"/api/datasets/{dataset.id}/").data["chat"][-1]
    assert saved["status"] == "awaiting_intent" and saved["id"] == question["id"]
    queued = []
    monkeypatch.setattr(tasks.turn, "apply_async", lambda **kwargs: queued.append(kwargs))
    body = {"intent_choice": intent, "intent_turn_id": question["id"]}
    with pytest.MonkeyPatch.context() as patch:
        # Run on_commit immediately so the test inspects the continuation receipt.
        patch.setattr("overbae.services.datasets.dispatch.transaction.on_commit", lambda f: f())
        result = client.post(f"/api/datasets/{dataset.id}/chat/", body, format="json")
    assert result.status_code == 202, result.data
    dataset.refresh_from_db()
    assert dataset.intent == intent and dataset.state == "diagnosing"
    assert dataset.chat[-1]["status"] == "resolved"
    assert len(queued) == 1
    assert "Clean these examples" in queued[0]["kwargs"]["message"]
    assert dataset.cells.count() == 1
    repeated = client.post(f"/api/datasets/{dataset.id}/chat/", body, format="json")
    assert repeated.status_code == 409
    assert len(queued) == 1

    class ContinueEngine:
        name = "fixture"

        def run(self, current, message, tools, pending):
            assert current.intent == intent
            assert "Clean these examples" in message
            assert tools.handlers()["query"]({"sql": "SELECT count(*) AS n FROM t"})["rows"] == [
                {"n": 1}
            ]
            while pending:
                yield pending.pop(0)
            return engines.Outcome(text="Continued the original request.")

    monkeypatch.setattr(engines, "select", lambda user=None: ContinueEngine())
    tasks.turn.run(**queued[0]["kwargs"])
    dataset.refresh_from_db()
    assert dataset.state == "idle" and dataset.intent == intent
    assert dataset.chat[-1]["status"] == "complete"
    assert dataset.chat[-1]["text"] == "Continued the original request."


def test_invalid_or_foreign_question_does_not_change_intent(workshop, monkeypatch):
    client, project = workshop
    dataset = dataset_for(project, brief="Clean these examples")
    question = pause(dataset, monkeypatch)
    for body in (
        {"intent_choice": "train"},
        {"intent_choice": "pending", "intent_turn_id": question["id"]},
        {"intent_choice": "train", "intent_turn_id": str(uuid.uuid4())},
    ):
        response = client.post(f"/api/datasets/{dataset.id}/chat/", body, format="json")
        assert response.status_code in (400, 409)
    dataset.refresh_from_db()
    assert dataset.intent == "pending" and dataset.state == "idle"
