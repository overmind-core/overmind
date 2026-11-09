from typing import Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from pydantic import Field

from overbae.models import WorkshopTask
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.contracts.common import MCPModel
from overbae.services.mcp.errors import MCPError, dataset_mcp_error
from overbae.services.workshop_agent import conversation, lifecycle
from overbae.services.workshop_agent.contracts import ControlMessage, SendMessage, StartTask


class TaskInput(MCPModel):
    task: UUID


class ControlInput(TaskInput):
    command: Literal["pause", "resume", "cancel"]


class InspectInput(TaskInput):
    after: int = Field(default=0, ge=0)


class TaskOutput(MCPModel):
    task: dict
    actions: list[dict] = Field(default_factory=list)
    events: list[dict] = Field(default_factory=list)
    cursor: int = 0


class MessageOutput(MCPModel):
    message: dict


def invoke(name, payload, context):
    try:
        if name == "control_workshop_message":
            return MessageOutput(message=conversation.control_message(context, payload))
        if name == "start_workshop_task":
            task = lifecycle.start(context, payload)
        elif name == "send_workshop_message":
            task = conversation.send(context, payload)
        else:
            task = WorkshopTask.objects.filter(pk=payload.task, project=context.project).first()
            if task is None:
                raise MCPError("resource_not_found", "Workshop task not found in this project.")
            if name == "control_workshop_task":
                task = lifecycle.control(context.project, task.pk, payload.command)
        actions = list(
            task.actions.order_by("-sequence").values("id", "sequence", "name", "state", "result")[
                :20
            ]
        )
        for action in actions:
            action["id"] = str(action["id"])
        history = (
            conversation.page(context.project, task.dataset_id, payload.after)
            if name == "inspect_workshop_task"
            else {}
        )
        return TaskOutput(task=lifecycle.describe(task), actions=actions, **history)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def register_workshop_agent_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

    for name, model, description, read_only in [
        (
            "start_workshop_task",
            StartTask,
            "Prepare a dataset natively. Pins context datasets read-only; returns a task.",
            False,
        ),
        (
            "send_workshop_message",
            SendMessage,
            "Queue a cell-linked follow-up, answer a presentation interaction, or continue a settled conversation. Request-keyed.",
            False,
        ),
        (
            "control_workshop_message",
            ControlMessage,
            "Edit, remove or reorder queued messages by revision; move uses a zero-based position. Dispatched messages are immutable.",
            False,
        ),
        (
            "inspect_workshop_task",
            InspectInput,
            "Read task, recent actions and chat. Page with after=cursor.",
            True,
        ),
        (
            "control_workshop_task",
            ControlInput,
            "Pause/resume/cancel; keeps files. Remote compute may finish.",
            False,
        ),
    ]:

        async def handler(payload, context, name=name):
            return await sync_to_async(invoke, thread_sensitive=True)(name, payload, context)

        catalog.register(
            ToolDefinition(
                name=name,
                title=name.replace("_", " ").capitalize(),
                description=description,
                input_model=model,
                output_model=MessageOutput if name == "control_workshop_message" else TaskOutput,
                read_only=read_only,
                idempotent=True,
                open_world=False,
                required_scopes=frozenset(
                    {"overmind:read" if read_only else "overmind:data:write"}
                ),
                cost_class="llm"
                if name in {"start_workshop_task", "send_workshop_message"}
                else "free",
                async_mode="task"
                if name in {"start_workshop_task", "send_workshop_message"}
                else "sync",
            ),
            handler,
        )
