import uuid

import pytest
from mcp_fixtures import mcp_context
from rest_framework.test import APIClient

from overbae.models import Cell, Dataset, WorkshopWorkspace
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import conversation, lifecycle, model_stream, resident
from overbae.services.workshop_agent.contracts import ControlMessage, SendMessage, StartTask
from workshop_runtime import presentation

pytestmark = [pytest.mark.django_db, pytest.mark.covers("work-once")]


def fixture():
    grant = mcp_context(["read", "write"])
    WorkshopWorkspace.objects.create(project=grant.project, sandbox_id=str(uuid.uuid4()))
    dataset = Dataset.objects.create(project=grant.project, name="Presentation")
    task = lifecycle.start(
        grant, StartTask(dataset=dataset.pk, objective="Inspect", request_key="initial")
    )
    cell = Cell.objects.create(
        dataset=dataset, position=0, title="Source", state="ok", rows=3, fingerprint="b" * 64
    )
    document = {
        "root": {
            "component": "Stack",
            "props": {
                "children": [
                    {
                        "component": "Chart",
                        "props": {
                            "data": "counts",
                            "kind": "horizontal-bar",
                            "title": "Labels",
                            "x": "label",
                            "y": ["count"],
                        },
                    },
                    {
                        "component": "Question",
                        "props": {
                            "id": "target",
                            "prompt": "Choose the target",
                            "fields": [
                                {
                                    "name": "target",
                                    "label": "Target",
                                    "type": "select",
                                    "required": True,
                                    "options": [{"value": "observed", "label": "Observed outcome"}],
                                }
                            ],
                        },
                    },
                ]
            },
        },
        "data": {
            "counts": {
                "path": "counts.json",
                "cells": [str(cell.pk)],
                "scope": "All three source rows",
            }
        },
    }
    value = presentation.prepare(
        document, lambda path: b'[{"label":"A","count":2},{"label":"B","count":1}]'
    )
    return grant, task, cell, value


def test_presentation_relay_replays_exact_data_and_validated_answer_once():
    grant, task, cell, value = fixture()
    request = {"id": str(uuid.uuid4()), "turn": 1, "kind": "present", "arguments": value}
    with model_stream.ModelStream(api_key="test") as model:
        receipt = resident.relay(task, request, model)
        assert "error" not in receipt, receipt
        assert resident.relay(task, request, model) == receipt
    client = APIClient()
    client.force_authenticate(grant.user, grant.token)
    response = client.get(
        "/api/workshop-tasks/history/",
        {"project": str(grant.project.pk), "dataset": str(task.dataset_id)},
    )
    assert response.status_code == 200, response.data
    message = response.data["events"][-1]["message"]
    assert message["presentation"]["data"]["counts"]["rows"][0]["count"] == 2
    assert message["cells"][0]["fingerprint"] == cell.fingerprint
    cell.refresh_from_db()
    assert cell.used_at is not None
    body = SendMessage(
        dataset=task.dataset_id,
        text="Ignore the actual selection",
        request_key="answer",
        interaction={
            "message": receipt["message"],
            "component": "target",
            "values": {"target": "observed"},
        },
    )
    conversation.send(grant, body)
    assert conversation.send(grant, body).pk == task.pk
    incoming = conversation.inbox(task)
    assert incoming[0]["text"] == "Choose the target\nTarget: Observed outcome"
    assert incoming[0]["interaction"]["component"] == "target"
    with pytest.raises(DatasetError, match="already been answered"):
        conversation.send(grant, body.model_copy(update={"request_key": "repeat"}))
    with pytest.raises(DatasetError, match="answer the question again"):
        conversation.control_message(
            grant,
            ControlMessage(
                task=task.pk,
                message="user:answer",
                command="edit",
                revision=0,
                request_key="edit",
                text="A different answer",
            ),
        )
    conversation.control_message(
        grant,
        ControlMessage(
            task=task.pk, message="user:answer", command="remove", revision=0, request_key="remove"
        ),
    )
    conversation.send(grant, body.model_copy(update={"request_key": "replacement"}))
    assert len(conversation.inbox(task)) == 1


@pytest.mark.covers("tenancy")
def test_presentation_rejects_foreign_cells_and_foreign_question_answers():
    grant, task, cell, value = fixture()
    other = mcp_context(["read", "write"])
    dataset = Dataset.objects.create(project=other.project, name="Other")
    foreign = Cell.objects.create(
        dataset=dataset, title="Private", position=0, state="ok", fingerprint="c" * 64
    )
    value["document"]["data"]["counts"]["cells"] = [str(foreign.pk)]
    with pytest.raises(ValueError, match="unavailable"):
        conversation.present(task, 1, value)
    value["document"]["data"]["counts"]["cells"] = [str(cell.pk)]
    receipt = conversation.present(task, 2, value)
    with pytest.raises(DatasetError, match="available Workshop question"):
        conversation.send(
            other,
            SendMessage(
                dataset=dataset.pk,
                text="Answer",
                request_key="foreign",
                interaction={
                    "message": receipt["message"],
                    "component": "target",
                    "values": {"target": "observed"},
                },
            ),
        )


def test_chart_missing_data_is_rejected_instead_of_rendering_a_zero():
    _, _, _, value = fixture()
    document = value["document"]
    document["root"]["props"]["children"][0]["props"]["kind"] = "line"
    with pytest.raises(ValueError, match="missing values are not zeros"):
        presentation.prepare(document, lambda path: b'[{"label":"A","count":null}]')
    document["root"]["props"]["children"] = ["run arbitrary code"]
    with pytest.raises(ValueError, match="Children"):
        presentation.validate(document)


@pytest.mark.parametrize(
    "answer",
    [{"target": "invented"}, {"target": 3}, {"target": "observed", "unexpected": True}, {}],
)
def test_question_rejects_answers_outside_declared_fields(answer):
    _, _, _, value = fixture()
    with pytest.raises(ValueError):
        presentation.answer(value["document"], "target", answer)


def test_retained_presentation_identity_cannot_be_overwritten():
    _, task, _, value = fixture()
    conversation.present(task, 1, value)
    value["data"]["counts"]["rows"][0]["count"] = 7
    with pytest.raises(ValueError, match="conflicts"):
        conversation.present(task, 1, value)
    assert (
        conversation.page(task.project, task.dataset_id)["events"][-1]["message"]["presentation"][
            "data"
        ]["counts"]["rows"][0]["count"]
        == 2
    )
