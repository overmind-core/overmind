"""The sandbox session of a thread. A session is a cache: the platform or its provider may
drop it at any time. The next use starts one with the staged datasets back, and the model's
next cell is told that its kernel is new."""

import contextlib

from django.conf import settings
from django.db import transaction

from overbae.core.errors import InputValidationError
from overbae.models import AssistantThread, Cell, Project
from overbae.services.datasets import rows
from overbae.services.sandbox_sessions import (
    SessionGoneError,
    SessionProvider,
    SessionUnavailableError,
)

# Staged frames count against a session's memory and files.
STAGED_BYTES = 1024**3
EVICTED = "This project needed the session for another conversation."


def forget(thread: AssistantThread, reason: str) -> None:
    """Record that the thread's session is gone, unless a newer one already replaced it."""
    AssistantThread.objects.filter(pk=thread.pk, session=thread.session).update(
        session="", kernel_lost=reason[:255]
    )
    thread.session, thread.kernel_lost = "", reason[:255]


def use(thread: AssistantThread, provider: SessionProvider) -> str:
    """The thread's session, started with its staged datasets when it has none."""
    if thread.session:
        return thread.session
    session = provider.start()
    try:
        for cell in Cell.objects.filter(dataset__project=thread.project, pk__in=thread.staged):
            _put(provider, session, cell)
        evicted = _admit(thread, session)
    except BaseException:
        _drop(provider, session)
        raise
    if evicted:
        _drop(provider, evicted)
    thread.session = session
    return session


def stage(thread: AssistantThread, provider: SessionProvider, cell: Cell) -> None:
    held = Cell.objects.filter(dataset__project=thread.project, pk__in=thread.staged)
    size = sum(rows.frame_path(each).stat().st_size for each in {cell, *held})
    if size > STAGED_BYTES:
        raise InputValidationError(
            f"The staged versions would take {size:,} bytes; a kernel holds {STAGED_BYTES:,}. "
            "Read this version with query_dataset, or transform it in a pipeline package."
        )
    for _attempt in range(2):
        session = use(thread, provider)
        try:
            _put(provider, session, cell)
            break
        except SessionGoneError as exc:
            forget(thread, str(exc))
    else:
        raise SessionUnavailableError("the session was lost twice")
    if str(cell.pk) not in thread.staged:
        thread.staged = [*thread.staged, str(cell.pk)]
        AssistantThread.objects.filter(pk=thread.pk).update(staged=thread.staged)


def _put(provider, session, cell) -> None:
    with open(rows.frame_path(cell), "rb") as frame:
        provider.put_file(session, f"{cell.pk}.parquet", frame)


def _drop(provider, session) -> None:
    # A session the provider cannot stop now ends at the provider's own idle limit.
    with contextlib.suppress(SessionUnavailableError):
        provider.stop(session)


def _admit(thread, session) -> str:
    """Give the thread its started session within the project's limit. Returns the session
    this one replaced, for the caller to stop: the conversation idle longest gives way."""
    limit = settings.ASSISTANT_PROJECT_SESSIONS
    with transaction.atomic():
        # One admission per project at a time, for the length of this short transaction.
        Project.objects.select_for_update(no_key=True).get(pk=thread.project_id)
        holders = (
            AssistantThread.objects.filter(project=thread.project_id)
            .exclude(session="")
            .exclude(pk=thread.pk)
        )
        evicted = ""
        if holders.count() >= limit:
            resting = holders.exclude(state=AssistantThread.State.RUNNING)
            victim = resting.order_by("updated_at").first()
            if victim is None or not resting.filter(pk=victim.pk, session=victim.session).update(
                session="", kernel_lost=EVICTED
            ):
                raise SessionUnavailableError(f"this project already runs {limit} sessions")
            evicted = victim.session
        # A second step on this thread must not replace a session the first one holds.
        if not AssistantThread.objects.filter(pk=thread.pk, session="").update(session=session):
            raise SessionUnavailableError("another step started this conversation's session")
    return evicted
