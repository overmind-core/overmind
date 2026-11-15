"""What a user does to a conversation: open it, send a message, change the queue."""

from django.db import transaction
from django.db.models import F, Max

from overbae.core.errors import InputValidationError
from overbae.models import AssistantInput, AssistantThread, Cell, Dataset
from overbae.services.assistant import log

Queued = AssistantInput.State.QUEUED
# A wide dataset names its first columns; the model queries the rest.
BRIEF_COLUMNS = 40
BRIEF_EXAMPLE_CHARS = 160


def open_thread(dataset: Dataset) -> AssistantThread:
    """The dataset's conversation, made on first use."""
    return AssistantThread.objects.get_or_create(
        dataset=dataset, defaults={"project_id": dataset.project_id}
    )[0]


def data_name(cell) -> str:
    """The file a version has in a workspace's ``data``. A published version never changes,
    so its id names its rows."""
    return f"{cell.pk}.parquet"


def brief(thread) -> dict:
    """What the dataset holds now, in JSON types: the model reads it with the user's
    message, so its first step is work and not a lookup."""
    dataset = thread.dataset
    names = dataset.versions()
    source, active = dataset.source, dataset.active_cell

    def version(cell):
        return {
            "cell": str(cell.pk),
            "title": cell.title,
            "version": names.get(cell.pk),
            "rows": cell.rows,
            "path": f"data/{data_name(cell)}",
            "column_count": len(cell.columns),
            "columns": cell.columns[:BRIEF_COLUMNS],
        }

    out = {
        "dataset": {"id": str(dataset.pk), "name": dataset.name, "intent": dataset.intent},
        "versions": len(names),
    }
    if source is not None and source.ran:
        measured = (source.stats.get("preparation_profile") or {}).get("columns") or {}
        out["source"] = version(source) | {
            "profile": {
                name: {
                    "empty_rows": column.get("empty_rows"),
                    "examples": [
                        example[:BRIEF_EXAMPLE_CHARS] for example in column.get("examples", [])[:2]
                    ],
                }
                for name, column in list(measured.items())[:BRIEF_COLUMNS]
            }
        }
    if active is not None and active != source:
        out["latest"] = version(active)
    recipe = dataset.preparation_pipeline
    if recipe is not None:
        out["pipeline"] = {"id": str(recipe.family), "name": recipe.name}
    return out


def send(thread, user, *, request_key, text, cells=(), interaction=None) -> AssistantInput:
    """Queue a message. The same key with the same message returns the first one."""
    cells = [str(cell) for cell in cells]
    with transaction.atomic():
        AssistantThread.objects.select_for_update().get(pk=thread.pk)
        prior = thread.inputs.filter(request_key=request_key).first()
        if prior is not None:
            if (prior.text, prior.cells, prior.interaction) != (text, cells, interaction):
                raise InputValidationError("This request key identifies a different message.")
            return prior
        known = Cell.objects.filter(dataset__project=thread.project_id, pk__in=cells).count()
        if known != len(set(cells)):
            raise InputValidationError("A referenced dataset version is not in this project.")
        last = thread.inputs.aggregate(last=Max("position"))["last"] or 0
        log.changed(thread)
        return AssistantInput.objects.create(
            thread=thread,
            created_by=user,
            request_key=request_key,
            text=text,
            cells=cells,
            interaction=interaction,
            position=last + 1,
        )


def change(message_id, thread, user, *, revision, action, text=None, before=None) -> AssistantInput:
    """Edit, remove, move or send now a queued message. ``revision`` must be the one the
    user saw, so a change to a message a turn already read is refused. A message's words
    are its sender's: only they edit or remove it; any member orders the queue."""
    with transaction.atomic():
        # The thread first, as ``send`` does: two moves then cannot lock each other's rows.
        AssistantThread.objects.select_for_update().get(pk=thread.pk)
        message = (
            AssistantInput.objects.select_for_update().filter(pk=message_id, thread=thread).first()
        )
        if message is None or message.state != Queued or message.revision != revision:
            raise InputValidationError("This message changed or was already sent.")
        if action in ("edit", "remove") and message.created_by_id != user.pk:
            raise InputValidationError("Only its sender can edit or remove a queued message.")
        if action == "edit":
            message.text = text
        elif action == "remove":
            message.state = AssistantInput.State.REMOVED
        elif action == "send_now":
            message.urgent = True
        elif action == "move":
            queue = [m for m in thread.inputs.filter(state=Queued) if m.pk != message.pk]
            index = next((i for i, m in enumerate(queue) if str(m.pk) == str(before)), len(queue))
            queue.insert(index, message)
            for position, moved in enumerate(queue, start=1):
                AssistantInput.objects.filter(pk=moved.pk).update(position=position)
            message.position = index + 1
        message.revision = F("revision") + 1
        message.save()
        log.changed(thread)
    message.refresh_from_db()
    return message
