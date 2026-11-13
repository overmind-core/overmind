"""What a user does to a thread: open it, send a message, change the queue."""

from django.db import transaction
from django.db.models import F, Max

from overbae.core.errors import InputValidationError
from overbae.models import AssistantInput, AssistantThread, Cell, Dataset
from overbae.services.assistant import log

Queued = AssistantInput.State.QUEUED
# Records whose Console page opens the conversation about them.
SUBJECTS = {"dataset": Dataset}


def subject(thread):
    """The record the thread was opened on, or None when it has none or the record is gone."""
    if thread.subject_id is None:
        return None
    return SUBJECTS[thread.subject_kind].objects.filter(pk=thread.subject_id).first()


def open_thread(project, kind="", record=None) -> AssistantThread:
    """The thread about a record, made on first use, or a new thread with no subject."""
    if record is None:
        return AssistantThread.objects.create(project=project)
    if not SUBJECTS[kind].objects.filter(pk=record, project=project).exists():
        raise InputValidationError("The subject is not in this project.")
    return AssistantThread.objects.get_or_create(
        subject_kind=kind, subject_id=record, defaults={"project": project}
    )[0]


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
        log.changed(thread.pk)
        return AssistantInput.objects.create(
            thread=thread,
            created_by=user,
            request_key=request_key,
            text=text,
            cells=cells,
            interaction=interaction,
            position=last + 1,
        )


def change(message_id, thread, *, revision, action, text=None, before=None) -> AssistantInput:
    """Edit, remove, move or prioritise a queued message. ``revision`` must be the one the
    user saw, so a change to a message the loop already took is refused."""
    with transaction.atomic():
        message = (
            AssistantInput.objects.select_for_update().filter(pk=message_id, thread=thread).first()
        )
        if message is None or message.state != Queued or message.revision != revision:
            raise InputValidationError("This message changed or was already sent.")
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
        log.changed(thread.pk)
    message.refresh_from_db()
    return message
