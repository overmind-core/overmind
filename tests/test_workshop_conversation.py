import uuid

import pytest
from django.db import connections
from fakes.daytona import DaytonaAPI
from fakes.responses_api import ResponsesAPI, response
from mcp_fixtures import call_tool, mcp_context
from rest_framework.test import APIClient

from overbae.models import Cell, Dataset, WorkshopWorkspace
from overbae.services.datasets import land, paths
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import (
    conversation,
    lifecycle,
    model_stream,
    resident,
    workspace,
)
from overbae.services.workshop_agent.contracts import ControlMessage, SendMessage, StartTask
from workshop_runtime.environment import identity

pytestmark = [pytest.mark.django_db, pytest.mark.covers("work-once")]


def task_fixture():
    grant = mcp_context(["read", "write"])
    WorkshopWorkspace.objects.create(project=grant.project, sandbox_id=str(uuid.uuid4()))
    dataset = Dataset.objects.create(project=grant.project, name="Conversation")
    task = lifecycle.start(
        grant, StartTask(dataset=dataset.pk, objective="Inspect the data", request_key="initial")
    )
    return grant, task


def test_followup_is_idempotent_delivered_once_and_keeps_cell_context():
    grant, task = task_fixture()
    cell = Cell.objects.create(
        dataset=task.dataset, position=0, title="Source", state="ok", rows=3, fingerprint="a" * 64
    )
    body = dict(
        dataset=str(task.dataset_id),
        text="Why are these values missing?",
        cell=str(cell.pk),
        request_key="followup",
    )
    first = call_tool("send_workshop_message", body, grant)
    assert not first.isError, first
    assert conversation.send(grant, SendMessage(**body)).pk == task.pk
    incoming = conversation.inbox(task)
    assert len(incoming) == 1 and incoming[0]["cells"][0]["id"] == str(cell.pk)
    with pytest.raises(DatasetError, match="conflicts"):
        conversation.send(grant, SendMessage(**{**body, "text": "Change the data"}))
    conversation.project_snapshot(task, {"accepted_messages": [incoming[0]["id"]]})
    replay = conversation.page(grant.project, task.dataset_id)
    assert replay["events"][-1]["message"]["status"] == "completed"
    assert conversation.page(grant.project, task.dataset_id, replay["cursor"])["events"] == []


@pytest.mark.covers("tenancy")
def test_history_and_followup_cannot_cross_projects():
    grant, task = task_fixture()
    other = mcp_context(["read", "write"])
    client = APIClient()
    client.force_authenticate(other.user, other.token)
    assert (
        client.get(
            "/api/workshop-tasks/history/",
            {"project": str(other.project.pk), "dataset": str(task.dataset_id)},
        ).status_code
        == 404
    )
    response = client.post(
        "/api/workshop-tasks/messages/",
        {
            "project": str(other.project.pk),
            "dataset": str(task.dataset_id),
            "text": "Show data",
            "request_key": "foreign",
        },
        format="json",
    )
    assert response.status_code == 400
    assert conversation.page(other.project, task.dataset_id)["events"] == []


def test_followup_at_completion_is_not_lost_or_allowed_to_repeat_publication():
    grant, task = task_fixture()
    conversation.send(
        grant, SendMessage(dataset=task.dataset_id, text="Explain the result", request_key="late")
    )
    state = {
        "state": "completed",
        "turn": 1,
        "answered": True,
        "reason": "The values were inspected.",
        "events": [{"tool": "finish", "error": None}],
    }
    assert resident.project_state(task, {"state": state})["state"] == "continuing"
    task.refresh_from_db()
    assert task.state == "queued"
    state["accepted_messages"] = [message["id"] for message in conversation.inbox(task)]
    assert resident.project_state(task, {"state": state})["state"] == "completed"
    task.refresh_from_db()
    continuation = conversation.send(
        grant,
        SendMessage(dataset=task.dataset_id, text="What about duplicates?", request_key="next"),
    )
    assert continuation.pk != task.pk
    assert any(
        message["text"] == "Explain the result" for message in conversation.history(continuation)
    )


def test_cancellation_marks_unread_followups_undelivered_and_excludes_them_from_new_tasks():
    grant, task = task_fixture()
    conversation.send(
        grant, SendMessage(dataset=task.dataset_id, text="Remove duplicates", request_key="unread")
    )
    lifecycle.control(grant.project, task.pk, "cancel")
    resident.project_state(task, {"state": {"state": "cancelled", "turn": 0}})
    messages = conversation.page(grant.project, task.dataset_id)["events"]
    assert messages[-1]["message"]["status"] == "cancelled"
    continuation = conversation.send(
        grant, SendMessage(dataset=task.dataset_id, text="Inspect only", request_key="next")
    )
    assert not any(
        message["text"] == "Remove duplicates" for message in conversation.history(continuation)
    )


def test_queued_edits_are_idempotent_and_delivery_freezes_the_last_revision():
    grant, task = task_fixture()
    conversation.send(
        grant, SendMessage(dataset=task.dataset_id, text="Keep 10 rows", request_key="queued")
    )
    payload = ControlMessage(
        task=task.pk,
        message="user:queued",
        command="edit",
        text="Keep all rows",
        revision=0,
        request_key="edit",
    )
    edited = conversation.control_message(grant, payload)
    assert edited["revision"] == 1
    assert conversation.control_message(grant, payload) == edited
    with pytest.raises(DatasetError, match="changed"):
        conversation.control_message(grant, payload.model_copy(update={"request_key": "stale"}))
    lifecycle.control(grant.project, task.pk, "pause")
    assert conversation.claim_inbox(task) == []
    lifecycle.control(grant.project, task.pk, "resume")
    delivered = conversation.claim_inbox(task)
    assert [item["text"] for item in delivered] == ["Keep all rows"]
    with pytest.raises(DatasetError, match="no longer"):
        conversation.control_message(
            grant, payload.model_copy(update={"revision": 1, "request_key": "late"})
        )
    assert conversation.claim_inbox(task) == delivered
    conversation.project_snapshot(task, {"accepted_messages": [delivered[0]["id"]]})
    assert (
        conversation.page(grant.project, task.dataset_id)["events"][-1]["message"]["text"]
        == "Keep all rows"
    )


@pytest.mark.covers("tenancy")
def test_queued_removal_is_project_scoped_and_never_reaches_the_agent():
    grant, task = task_fixture()
    conversation.send(
        grant, SendMessage(dataset=task.dataset_id, text="Remove rows", request_key="queued")
    )
    body = dict(
        task=str(task.pk), message="user:queued", command="remove", revision=0, request_key="remove"
    )
    assert call_tool("control_workshop_message", body, mcp_context(["read", "write"])).isError
    assert not call_tool("control_workshop_message", body, grant).isError
    assert not call_tool("control_workshop_message", body, grant).isError
    assert conversation.claim_inbox(task) == []


@pytest.mark.django_db(transaction=True)
def test_stop_during_silent_provider_stream_retains_interruption_without_replaying(monkeypatch):
    grant, task = task_fixture()
    monkeypatch.setenv("WORKSHOP_AGENT_MODEL", "test")

    def cancel(socket, request):
        try:
            lifecycle.control(grant.project, task.pk, "cancel")
        finally:
            connections.close_all()

    request = {
        "id": str(uuid.uuid4()),
        "turn": 0,
        "kind": "model",
        "arguments": {"model": "test", "input": [{"role": "user", "content": "Inspect"}]},
    }
    with (
        ResponsesAPI(cancel) as api,
        model_stream.ModelStream(url=api.url, api_key="test") as model,
    ):
        result = resident.relay(task, request, model)
        assert result == {"cancelled": True}
        with pytest.raises(DatasetError, match="not running"):
            resident.relay(task, request, model)
        assert len(api.requests) == 1
    assert (
        conversation.page(grant.project, task.dataset_id)["events"][-1]["message"]["status"]
        == "interrupted"
    )
    with pytest.raises(DatasetError, match="cancellation"):
        lifecycle.control(grant.project, task.pk, "resume")


def test_provider_stream_retains_public_text_and_complete_tool_call_without_private_reasoning(
    monkeypatch,
):
    grant, task = task_fixture()
    monkeypatch.setenv("WORKSHOP_AGENT_MODEL", "test")
    answer = response()
    request = {
        "id": str(uuid.uuid4()),
        "turn": 0,
        "kind": "model",
        "arguments": {"model": "test", "input": [{"role": "user", "content": "Inspect values"}]},
    }
    with (
        ResponsesAPI(answer) as api,
        model_stream.ModelStream(url=api.url, api_key="test") as model,
    ):
        result = resident.relay(task, request, model)
        assert result["status"] == 200, result
        assert result["body"]["output"][-1]["arguments"] == '{"code": "print(3)"}'
        assert resident.relay(task, request, model) == result
        assert len(api.requests) == 1
    events = conversation.page(grant.project, task.dataset_id)["events"]
    assert events[-1]["message"]["text"] == "Inspecting the values."
    assert "Private provider reasoning" not in str(events)
    preparing = [event for event in events if event["message"].get("tool") == "python"]
    assert preparing and preparing[0]["id"] < events[-1]["id"]
    assert preparing[-1]["message"]["status"] == "preparing"
    assert preparing[-1]["message"]["code"] == "print(3)"
    assert {event["message"]["id"] for event in preparing} == {"action:0"}
    conversation.project_snapshot(
        task,
        {
            "public_messages": [
                {**preparing[-1]["message"], "status": "completed", "text": "Checked values"}
            ]
        },
    )
    retained = {
        event["message"]["id"]: event["message"]
        for event in conversation.page(grant.project, task.dataset_id)["events"]
    }
    assert retained["action:0"]["status"] == "completed"
    assert retained["action:0"]["code"] == "print(3)"


def test_gateway_retains_full_context_and_never_retries_unknown_generation(monkeypatch):
    grant, task = task_fixture()
    monkeypatch.setenv("WORKSHOP_AGENT_MODEL", "test")
    answers = [response() for _ in range(4)]

    def disconnect(socket, request):
        socket({"type": "response.created", "response": {"id": "resp_lost"}})
        raise ConnectionResetError("stream lost")

    items = [{"role": "user", "content": "Inspect values"}]
    with ResponsesAPI(answers[0], answers[1], answers[2], answers[3], disconnect) as api:
        with model_stream.ModelStream(url=api.url, api_key="test") as model:
            for turn in range(3):
                request = {
                    "id": str(uuid.uuid4()),
                    "turn": turn,
                    "kind": "model",
                    "arguments": {"model": "test", "input": list(items)},
                }
                result = resident.relay(task, request, model)
                assert result["body"] == answers[turn]
                items.extend(answers[turn]["output"])
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": answers[turn]["output"][-1]["call_id"],
                        "output": "3",
                    }
                )
            assert len(api.requests[1]["input"]) > 1
            assert all("previous_response_id" not in r for r in api.requests)
        # A replacement coordinator sends complete context, including provider-only fields.
        for item in items:
            item.pop("phase", None)
            for content in item.get("content", []) if isinstance(item.get("content"), list) else []:
                content.pop("logprobs", None)
        with model_stream.ModelStream(url=api.url, api_key="test") as model:
            request = {
                "id": str(uuid.uuid4()),
                "turn": 3,
                "kind": "model",
                "arguments": {"model": "test", "input": list(items)},
            }
            result = resident.relay(task, request, model)
            assert "previous_response_id" not in api.requests[3]
            assert api.requests[3]["input"][2]["phase"] == "commentary"
            assert api.requests[3]["input"][2]["content"][0]["logprobs"] == []
            assert api.requests[3]["input"][1]["encrypted_content"] == "opaque-reasoning"
            request = {**request, "id": str(uuid.uuid4()), "turn": 4}
            assert resident.relay(task, request, model) == {"unknown": True}
            assert resident.relay(task, request, model)["unknown"]
            assert len(api.requests) == 5
        assert all(r["service_tier"] == "default" and r["store"] is False for r in api.requests)


def test_selected_cell_bytes_are_frozen_and_staged_before_followup_delivery(monkeypatch, scripted):
    grant, task = task_fixture()
    land.land_rows(task.dataset, [{"value": 7}])
    task.dataset.refresh_from_db()
    selected = task.dataset.active_cell
    expected = paths.cell_path(task.dataset_id, selected.pk).read_bytes()
    conversation.send(
        grant,
        SendMessage(
            dataset=task.dataset_id,
            cell=selected.pk,
            text="Inspect this cell",
            request_key="inspect",
        ),
    )
    selected.refresh_from_db()
    assert selected.used_at is not None
    remote = DaytonaAPI(
        WorkshopWorkspace.objects.get(project=grant.project).sandbox_id,
        {"ready": True, "identity": identity()},
    )
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona")
    workspace.stage_chat_cells(task)
    reference = conversation.inbox(task)[0]["cells"][0]
    assert reference["path"] == f"data/chat-cells/{selected.pk}.parquet"
    assert (
        remote.files[workspace.task_root(task) + f"/inputs/chat-cells/{selected.pk}.parquet"]
        == expected
    )
    workspace.stage_chat_cells(task)
    assert task.chat_events.filter(key=f"staged:{selected.pk}").count() == 1


def test_queue_order_survives_replay_and_claim_prevents_late_reordering():
    grant, task = task_fixture()
    for key in ("first", "second", "third"):
        conversation.send(grant, SendMessage(dataset=task.dataset_id, text=key, request_key=key))
    move = ControlMessage(
        task=task.pk,
        message="user:third",
        command="move",
        position=0,
        revision=0,
        request_key="move-third",
    )
    conversation.control_message(grant, move)
    conversation.control_message(grant, move)
    assert [item["text"] for item in conversation.inbox(task)] == ["third", "first", "second"]
    claimed = conversation.claim_inbox(task)
    assert [item["text"] for item in claimed] == ["third", "first", "second"]
    with pytest.raises(DatasetError, match="no longer"):
        conversation.control_message(
            grant, move.model_copy(update={"revision": 1, "request_key": "late-move"})
        )


def test_cancel_between_model_completion_and_execution_settles_preparing_tools():
    grant, task = task_fixture()
    conversation.emit(
        task,
        "draft",
        {
            "id": "action:0",
            "role": "activity",
            "status": "preparing",
            "tool": "python",
            "text": "Inspect values",
            "code": "print(3)",
        },
    )
    cursor = conversation.page(grant.project, task.dataset_id)["cursor"]
    conversation.project_snapshot(task, {"state": "cancelled"})
    events = conversation.page(grant.project, task.dataset_id, after=cursor)["events"]
    assert events[-1]["message"]["id"] == "action:0"
    assert events[-1]["message"]["status"] == "interrupted"
    conversation.project_snapshot(task, {"state": "cancelled"})
    assert conversation.page(grant.project, task.dataset_id, after=cursor)["events"] == events
