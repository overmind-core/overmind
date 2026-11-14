import json

import pytest
from conftest import drain_stream, frozen_dataset
from factories import auth_client, make_member, make_project, make_user

from overbae.models import AssistantThread
from overbae.services.assistant import log
from overbae.tasks.assistant import advance

pytestmark = [pytest.mark.django_db, pytest.mark.covers("assistant-api")]


@pytest.fixture
def queued(monkeypatch):
    steps = []
    monkeypatch.setattr(advance, "delay", steps.append)
    return steps


def frames(response):
    body = drain_stream(response).decode()
    return [json.loads(line.removeprefix("data: ")) for line in body.split("\n\n") if line]


def test_a_member_opens_a_dataset_thread_sends_and_reads_the_stream_from_a_cursor(
    queued, django_capture_on_commit_callbacks
):
    user = make_user()
    project = make_project(member=user)
    dataset = frozen_dataset(project, user=user)
    client = auth_client(user)

    subject = {"kind": "dataset", "record": str(dataset.pk)}
    body = {"project": project.pk, "subject": subject}
    opened = client.post("/api/assistant-threads/", body, format="json")
    again = client.post("/api/assistant-threads/", body, format="json")
    thread_id = opened.data["id"]
    assert again.data["id"] == thread_id
    assert [t["id"] for t in client.get("/api/assistant-threads/", subject).data] == [thread_id]

    body = {"request_key": "first", "text": "Prepare this."}
    with django_capture_on_commit_callbacks(execute=True):
        sent = client.post(f"/api/assistant-threads/{thread_id}/messages/", body, format="json")
    assert sent.status_code == 202 and queued == [thread_id]

    thread = AssistantThread.objects.get(pk=thread_id)
    for text in ("one", "two", "three"):
        log.append(thread, "assistant", {"text": text, "calls": [], "usage": {}})
    stream = frames(client.get(f"/api/assistant-threads/{thread_id}/events/?after=1"))
    assert [f["event"]["payload"]["text"] for f in stream if f["type"] == "event"] == [
        "two",
        "three",
    ]
    assert stream[-1]["type"] == "thread"
    assert [m["text"] for m in stream[-1]["thread"]["queue"]] == ["Prepare this."]


def test_another_project_and_a_guest_cannot_use_a_thread(queued):
    owner = make_user()
    project = make_project(member=owner)
    thread = AssistantThread.objects.create(project=project)
    url = f"/api/assistant-threads/{thread.pk}"
    body = {"request_key": "k", "text": "Hello."}

    outsider = auth_client(make_user())
    assert outsider.get(f"{url}/").status_code == 404
    assert outsider.get(f"{url}/events/").status_code == 404
    assert outsider.post(f"{url}/messages/", body, format="json").status_code == 404
    assert outsider.post(f"{url}/stop/").status_code == 404
    opened = outsider.post("/api/assistant-threads/", {"project": project.pk}, format="json")
    assert opened.status_code == 404

    guest = make_user(is_guest=True)
    make_member(guest, project)
    assert auth_client(guest).post(f"{url}/messages/", body, format="json").status_code == 403
    assert queued == [] and not thread.inputs.exists()
