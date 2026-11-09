import json
from uuid import UUID

from django.db import transaction

from overbae.models import Cell, Project, WorkshopChatEvent, WorkshopTask, WorkshopWorkspace
from overbae.services.datasets import use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import lifecycle
from overbae.services.workshop_agent.contracts import StartTask
from workshop_runtime import presentation


@transaction.atomic
def emit(task, key, message):
    # Serialize allocation and commit: a replay cursor must never skip an uncommitted event.
    Project.objects.select_for_update().get(pk=task.project_id)
    event, _ = WorkshopChatEvent.objects.get_or_create(
        task=task, key=key, defaults={"message": message}
    )
    return event


def page(project, dataset, after=0):
    events = list(
        WorkshopChatEvent.objects.filter(
            task__project=project, task__dataset_id=dataset, pk__gt=after
        ).order_by("pk")[:200]
    )
    return {
        "events": [
            {"id": event.pk, "task": str(event.task_id), "message": event.message}
            for event in events
        ],
        "cursor": events[-1].pk if events else after,
    }


def history(task):
    messages = {}
    for event in (
        WorkshopChatEvent.objects.filter(task__dataset_id=task.dataset_id)
        .filter(task__created_at__lt=task.created_at)
        .order_by("pk")
    ):
        message = event.message
        messages[f"{event.task_id}:{message['id']}"] = message
    return [
        {
            "role": "user" if value["role"] == "user" else "assistant",
            "text": value.get("text", ""),
            "cells": value.get("cells", []),
            **(
                {"presentation": value["presentation"]["document"]}
                if value.get("presentation")
                else {}
            ),
            **({"interaction": value["interaction"]} if value.get("interaction") else {}),
        }
        for value in messages.values()
        if value["role"] in {"user", "assistant", "publication"}
        and value.get("status")
        not in {"streaming", "cancelled", "removed", "pending", "delivering"}
    ]


def user_messages(task):
    latest = {}
    for message in (
        task.chat_events.filter(message__role="user")
        .order_by("pk")
        .values_list("message", flat=True)
    ):
        latest[message["id"]] = message
    return latest


def inbox(task):
    latest = user_messages(task)
    events = sorted(
        task.chat_events.filter(key__startswith="user:")
        .exclude(message__initial=True)
        .order_by("pk"),
        key=lambda event: latest[event.key].get("queue_position", event.pk),
    )
    return [
        {
            "id": str(event.pk),
            "text": latest[event.key]["text"],
            "cells": latest[event.key].get("cells", []),
            **(
                {"interaction": latest[event.key]["interaction"]}
                if latest[event.key].get("interaction")
                else {}
            ),
        }
        for event in events
        if latest[event.key]["status"] not in {"removed", "cancelled"}
    ]


@transaction.atomic
def claim_inbox(task):
    Project.objects.select_for_update().get(pk=task.project_id)
    current = WorkshopTask.objects.get(pk=task.pk)
    if current.desired_state != "running" or current.state in lifecycle.TERMINAL:
        return []
    latest = user_messages(task)
    for incoming in inbox(task):
        original = task.chat_events.get(pk=incoming["id"])
        message = latest[original.key]
        if message["status"] == "pending":
            emit(task, f"delivering:{original.pk}", {**message, "status": "delivering"})
    return inbox(task)


@transaction.atomic
def control_message(grant, payload):
    Project.objects.select_for_update().get(pk=grant.project.pk)
    if not grant.has_permission("write"):
        raise DatasetError("Write permission is required.", code="permission_denied")
    signature = payload.model_dump(mode="json")
    existing = WorkshopChatEvent.objects.filter(
        task__project=grant.project, key=f"queue:{payload.request_key}"
    ).first()
    if existing:
        if existing.message.get("queue_request") != signature:
            raise DatasetError(
                "Request key conflicts with an existing change.", code="request_key_conflict"
            )
        return existing.message
    task = WorkshopTask.objects.filter(pk=payload.task, project=grant.project).first()
    if task is None:
        raise DatasetError("Workshop task not found in this project.", code="dataset_not_found")
    message = user_messages(task).get(payload.message)
    if message is None or message.get("initial"):
        raise DatasetError("Queued message not found.", code="dataset_not_found")
    if message.get("revision", 0) != payload.revision:
        raise DatasetError(
            "This queued message changed. Reload it before editing.", code="request_key_conflict"
        )
    if (
        message["status"] != "pending"
        or task.state in lifecycle.TERMINAL
        or task.desired_state == "cancelled"
    ):
        raise DatasetError("This message can no longer be changed.", code="dataset_busy")
    if payload.command == "move":
        pending = [item for item in user_messages(task).values() if item["status"] == "pending"]
        pending.sort(key=lambda item: item.get("queue_position", float("inf")))
        if payload.position is None or payload.position >= len(pending):
            raise DatasetError("Queue position is unavailable.", code="dataset_invalid")
        pending = [item for item in pending if item["id"] != message["id"]]
        pending.insert(payload.position, message)
        for position, item in enumerate(pending):
            updated = {**item, "queue_position": position, "revision": item.get("revision", 0) + 1}
            if item["id"] == message["id"]:
                updated["queue_request"] = signature
                result = updated
                key = f"queue:{payload.request_key}"
            else:
                key = f"queue:{payload.request_key}:{position}"
            emit(task, key, updated)
        return result
    text = (payload.text or "").strip()
    if payload.command == "edit" and not text:
        raise DatasetError("Enter a message before saving.", code="dataset_invalid")
    if payload.command == "edit" and message.get("interaction"):
        raise DatasetError(
            "Remove this response and answer the question again.", code="dataset_invalid"
        )
    updated = {
        **message,
        "text": text if payload.command == "edit" else message["text"],
        "status": "pending" if payload.command == "edit" else "removed",
        "revision": payload.revision + 1,
        "queue_request": signature,
    }
    emit(task, f"queue:{payload.request_key}", updated)
    return updated


@transaction.atomic
def send(grant, payload):
    Project.objects.select_for_update().get(pk=grant.project.pk)
    if not grant.has_permission("write"):
        raise DatasetError("Write permission is required.", code="permission_denied")
    existing = (
        WorkshopChatEvent.objects.select_related("task")
        .filter(task__project=grant.project, key=f"user:{payload.request_key}")
        .first()
    )
    signature = {
        "dataset": str(payload.dataset),
        "text": payload.text,
        "cell": str(payload.cell) if payload.cell else None,
        "interaction": payload.interaction.model_dump(mode="json") if payload.interaction else None,
    }
    if existing:
        if existing.message.get("request") != signature:
            raise DatasetError(
                "Request key conflicts with an existing message.", code="request_key_conflict"
            )
        return existing.task
    text = payload.text
    interaction = signature["interaction"]
    if interaction:
        try:
            task_id, message_id = interaction["message"].split(":", 1)
            UUID(task_id)
            original = WorkshopChatEvent.objects.filter(
                task_id=task_id,
                task__project=grant.project,
                task__dataset_id=payload.dataset,
                message__id=message_id,
                message__status="completed",
            ).last()
            if original is None or not original.message.get("presentation"):
                raise ValueError("Presentation not found")
            text = presentation.answer(
                original.message["presentation"]["document"],
                interaction["component"],
                interaction["values"],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise DatasetError(
                "This response does not match an available Workshop question.",
                code="dataset_invalid",
            ) from exc
        responses = WorkshopChatEvent.objects.filter(
            task__project=grant.project,
            task__dataset_id=payload.dataset,
            message__role="user",
            message__interaction__message=interaction["message"],
            message__interaction__component=interaction["component"],
        ).order_by("pk")
        latest_responses = {event.message["id"]: event.message for event in responses}
        if any(
            message["status"] not in {"removed", "cancelled"}
            for message in latest_responses.values()
        ):
            raise DatasetError(
                "This question has already been answered.", code="request_key_conflict"
            )
    cells = []
    if payload.cell:
        cell = Cell.objects.filter(pk=payload.cell, dataset__project=grant.project).first()
        if cell is None:
            raise DatasetError("Cell not found in this project.", code="dataset_not_found")
        if cell.state != "ok" or not cell.fingerprint:
            raise DatasetError("Select a published cell before referencing it.")
        use.freeze(cell)
        cells = [
            {
                "id": str(cell.pk),
                "dataset": str(cell.dataset_id),
                "name": cell.title,
                "rows": cell.rows,
                "fingerprint": cell.fingerprint,
                "path": f"data/chat-cells/{cell.pk}.parquet",
            }
        ]
    latest = (
        WorkshopTask.objects.filter(project=grant.project, dataset_id=payload.dataset)
        .order_by("-created_at")
        .first()
    )
    if latest and latest.state not in lifecycle.TERMINAL | {"blocked"}:
        if latest.desired_state == "cancelled":
            raise DatasetError(
                "Wait for cancellation before sending another message.", code="dataset_busy"
            )
        task = latest
    else:
        if (
            latest
            and WorkshopWorkspace.objects.filter(
                project=grant.project, task_owner=latest.pk
            ).exists()
        ):
            raise DatasetError(
                "Reconcile the retained task before continuing.", code="dataset_busy"
            )
        task = lifecycle.start(
            grant,
            StartTask(
                dataset=payload.dataset,
                objective=text,
                request_key=f"chat:{lifecycle.digest(payload.request_key)}",
                context_datasets=[source["dataset"] for source in latest.context_sources]
                if latest
                else [],
            ),
            record_objective=False,
        )
    emit(
        task,
        f"user:{payload.request_key}",
        {
            "id": f"user:{payload.request_key}",
            "role": "user",
            "text": text,
            "status": "pending" if task == latest else "completed",
            "initial": task != latest,
            "cells": cells,
            "request": signature,
            **({"interaction": interaction} if interaction else {}),
        },
    )
    return task


def project_snapshot(task, state):
    existing = set(task.chat_events.values_list("key", flat=True))
    for message in state.get("public_messages", []):
        if state.get("state") == "cancelled" and message["status"] == "running":
            message = {**message, "status": "interrupted"}
        key = f"resident:{message['id']}:{message['status']}"
        if key not in existing:
            emit(task, key, message)
    if state.get("state") in {"completed", "cancelled", "failed", "blocked"}:
        activities = {}
        for value in (
            task.chat_events.filter(message__role="activity")
            .order_by("pk")
            .values_list("message", flat=True)
        ):
            activities[value["id"]] = value
        for value in activities.values():
            if value["status"] == "preparing":
                emit(task, f"settled:{value['id']}", {**value, "status": "interrupted"})
    accepted = set(state.get("accepted_messages", []))
    latest = user_messages(task)
    for event in task.chat_events.filter(key__startswith="user:"):
        message = latest[event.key]
        if str(event.pk) in accepted and f"accepted:{event.pk}" not in existing:
            emit(task, f"accepted:{event.pk}", {**message, "status": "completed"})
        elif (
            state.get("state") == "cancelled"
            and str(event.pk) not in accepted
            and not message.get("initial")
            and message["status"] != "removed"
            and f"cancelled:{event.pk}" not in existing
        ):
            emit(task, f"cancelled:{event.pk}", {**message, "status": "cancelled"})
    if state.get("reason"):
        emit(
            task,
            f"result:{state['turn']}:{state['state']}",
            {
                "id": f"result:{state['turn']}",
                "role": "assistant",
                "text": state["reason"],
                "status": "completed",
            },
        )


def cell_refs(task, ids):
    return [
        {
            "id": str(cell.pk),
            "dataset": str(cell.dataset_id),
            "name": cell.title,
            "rows": cell.rows,
            "columns": cell.columns,
            "checks": cell.intent_report,
            "fingerprint": cell.fingerprint,
        }
        for cell in Cell.objects.filter(pk__in=ids, dataset__project_id=task.project_id).order_by(
            "position"
        )
    ]


@transaction.atomic
def present(task, turn, value):
    lifecycle.authorise(task)
    if len(json.dumps(value, allow_nan=False).encode()) > 3 * 1024 * 1024:
        raise ValueError("Presentation is too large")
    document = presentation.validate(value["document"])
    presentation.validate_data(value)
    ids = {
        cell
        for group in ("data", "images")
        for reference in document[group].values()
        for cell in reference["cells"]
    }
    ids.update(
        node["props"]["cell"]
        for node in presentation.nodes(document["root"])
        if node["component"] == "Cell"
    )
    cells = list(
        Cell.objects.select_for_update().filter(pk__in=ids, dataset__project_id=task.project_id)
    )
    if any(cell.state != "ok" or not cell.fingerprint for cell in cells):
        raise ValueError("Presentation sources must be published cells")
    use.freeze(*cells)
    refs = cell_refs(task, ids)
    if {ref["id"] for ref in refs} != ids or any(not ref["fingerprint"] for ref in refs):
        raise ValueError("Presentation references an unavailable published cell in this project")
    for group in ("data", "images"):
        if set(value.get(group, {})) != set(document[group]):
            raise ValueError("Presentation files do not match the declared references")
    for image in value["images"].values():
        if not image["url"].startswith(
            ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")
        ):
            raise ValueError("Unsupported presentation image")
    payload = {**value, "document": document, "program": presentation.program(document["root"])}
    identity = f"presentation:{turn}"
    event = emit(
        task,
        identity,
        {
            "id": identity,
            "role": "assistant",
            "text": "",
            "status": "completed",
            "presentation": payload,
            "cells": refs,
        },
    )
    if event.message["presentation"] != payload:
        raise ValueError("Presentation identity conflicts with retained content")
    return {"message": f"{task.pk}:{identity}", "cells": refs}
