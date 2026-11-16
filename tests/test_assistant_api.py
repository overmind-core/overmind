import pytest
from conftest import frozen_dataset
from factories import auth_client, make_member, make_project, make_user

from overbae.models import AssistantThread
from overbae.services import workspaces
from overbae.services.assistant import log
from overbae.tasks.assistant import run_turn, stop_kernel

pytestmark = [pytest.mark.django_db, pytest.mark.covers("assistant-api")]


@pytest.fixture
def woken(monkeypatch):
    started = []
    monkeypatch.setattr(run_turn, "delay", started.append)
    return started


def opened(user, dataset):
    return auth_client(user).post("/api/assistant-threads/", {"dataset": dataset.pk}, format="json")


def test_a_member_opens_the_datasets_conversation_sends_and_reads_after_a_cursor(
    woken, django_capture_on_commit_callbacks
):
    user = make_user()
    project = make_project(member=user)
    dataset = frozen_dataset(project, user=user)
    client = auth_client(user)
    thread_id = opened(user, dataset).data["id"]
    assert opened(user, dataset).data["id"] == thread_id
    listed = client.get("/api/assistant-threads/", {"dataset": dataset.pk}).data
    assert [t["id"] for t in listed] == [thread_id]

    body = {"request_key": "first", "text": "Prepare this."}
    with django_capture_on_commit_callbacks(execute=True):
        sent = client.post(f"/api/assistant-threads/{thread_id}/messages/", body, format="json")
    assert sent.status_code == 202 and woken == [thread_id]

    thread = AssistantThread.objects.get(pk=thread_id)
    for text in ("one", "two", "three"):
        log.append(thread, "assistant", {"text": text, "calls": [], "usage": {"cost": 1}})
    shown = client.get(f"/api/assistant-threads/{thread_id}/events/?after=1").data
    assert [event["payload"]["text"] for event in shown] == ["two", "three"]
    # What the model alone reads stays on the server.
    assert all("usage" not in event["payload"] for event in shown)
    queue = client.get(f"/api/assistant-threads/{thread_id}/").data["queue"]
    assert [message["text"] for message in queue] == ["Prepare this."]


def test_a_tool_result_shows_only_its_output_and_error():
    user = make_user()
    thread = AssistantThread.objects.create(
        project=(project := make_project(member=user)), dataset=frozen_dataset(project, user=user)
    )
    content = {"output": "2\n", "rows": [{"secret": 1}] * 50, "error": {"message": "late"}}
    log.append(thread, "tool_result", {"call": "c", "name": "n", "ok": False, "content": content})
    (event,) = auth_client(user).get(f"/api/assistant-threads/{thread.pk}/events/").data
    assert event["payload"]["content"] == {"output": "2\n", "error": "late"}


def test_each_member_of_a_shared_conversation_is_told_which_messages_are_theirs(woken):
    first, second = make_user(), make_user()
    project = make_project(member=first)
    make_member(second, project)
    url = f"/api/assistant-threads/{opened(first, frozen_dataset(project, user=first)).data['id']}"
    for user, key in ((first, "a"), (second, "b")):
        message = {"request_key": key, "text": "Hello"}
        assert auth_client(user).post(f"{url}/messages/", message, format="json").status_code == 202

    thread = auth_client(second).get(f"{url}/").data
    assert [(who["email"], who["me"]) for who in thread["participants"]] == [
        (first.email, False),
        (second.email, True),
    ]
    assert [message["created_by"] for message in thread["queue"]] == [first.pk, second.pk]

    # The words of a queued message are its sender's; any member orders the queue.
    theirs = thread["queue"][0]
    change = {"message": theirs["id"], "revision": theirs["revision"]}
    for refused in ({"change": "edit", "text": "Changed"}, {"change": "remove"}):
        reply = auth_client(second).post(f"{url}/queue/", {**change, **refused}, format="json")
        assert reply.status_code == 400
    moved = {**change, "change": "move", "before": None}
    assert auth_client(second).post(f"{url}/queue/", moved, format="json").status_code == 200


def test_stop_during_a_turn_ends_the_kernel_at_once(
    monkeypatch, django_capture_on_commit_callbacks
):
    ended = []
    monkeypatch.setattr(stop_kernel, "delay", lambda *a: ended.append(a))
    user = make_user()
    project = make_project(member=user)
    thread = AssistantThread.objects.create(
        project=project, dataset=frozen_dataset(project, user=user), state="running"
    )
    with django_capture_on_commit_callbacks(execute=True):
        stopped = auth_client(user).post(f"/api/assistant-threads/{thread.pk}/stop/")
    assert stopped.status_code == 202 and stopped.data["stop_requested"]
    assert ended == [(str(project.pk), str(thread.pk), thread.turn)]

    # A stop that arrives after a later turn began leaves that turn's kernel alone.
    provider, conversation = workspaces.provider(), thread.pk.hex
    try:
        assert provider.run(str(project.pk), conversation, "x = 1", 60).ok
        AssistantThread.objects.filter(pk=thread.pk).update(turn=thread.turn + 1)
        stop_kernel(*ended[0])
        assert provider.run(str(project.pk), conversation, "x", 60).ok
    finally:
        provider.remove(str(project.pk))


def test_another_project_and_a_guest_cannot_use_a_conversation(woken):
    owner = make_user()
    project = make_project(member=owner)
    dataset = frozen_dataset(project, user=owner)
    thread = AssistantThread.objects.create(project=project, dataset=dataset)
    url = f"/api/assistant-threads/{thread.pk}"
    body = {"request_key": "k", "text": "Hello."}

    outsider = make_user()
    client = auth_client(outsider)
    assert client.get(f"{url}/").status_code == 404
    assert client.get(f"{url}/events/").status_code == 404
    assert client.post(f"{url}/messages/", body, format="json").status_code == 404
    assert client.post(f"{url}/stop/").status_code == 404
    assert opened(outsider, dataset).status_code == 404

    guest = make_user(is_guest=True)
    make_member(guest, project)
    assert auth_client(guest).post(f"{url}/messages/", body, format="json").status_code == 403
    assert woken == [] and not thread.inputs.exists()
