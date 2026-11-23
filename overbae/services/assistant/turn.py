"""A turn: from a user's message to the assistant's reply, in one task. The event log is
saved at every step, so a turn that stops halfway leaves an exact record.

A turn holds the thread by its number and writes only while it is the newest, and a
heartbeat moves the thread while its worker lives. When the worker is lost, another task
takes the thread over under a new number and continues the reply from the log; a call that
may have been running is answered as abandoned, never run again. After ``RESUMES`` such
take-overs for one message, or once nothing resumed it for ``STALE``, the turn is marked
interrupted."""

import json
import logging
import time
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from overbae.core.llms import IncompleteCompletionError, call_llm_tools, stream_llm_tools
from overbae.core.model_registry import TaskType, model_chain
from overbae.models import (
    AssistantEvent,
    AssistantInput,
    AssistantThread,
    BillingService,
    Cell,
    Dataset,
)
from overbae.models.assistant import STALE
from overbae.services.assistant import log, presentation, prompt, threads, tools
from overbae.services.billing_ledger import InsufficientCredits, charge_llm_usage, ensure_credits
from overbae.services.datasets import heartbeat

logger = logging.getLogger(__name__)
Kind, State = AssistantEvent.Kind, AssistantThread.State
Queued = AssistantInput.State.QUEUED

# Longer than any one step: a model reply, a cell at its limit, or one poll of a job.
TURN_SECONDS = 30 * 60
# A turn whose heartbeat is this old lost its worker and may be taken over.
LEASE = timedelta(minutes=2)
RESUMES = 2
DRAFT_INTERVAL_SECONDS = 0.1
ABANDONED = {
    "error": "This call has no recorded result: the turn stopped or was interrupted. It may "
    "have run. Inspect before you repeat a call that changes something."
}
# Newest user messages and replies the model keeps word for word after a summary.
COMPACT_KEEP = 6
COMPACT_PROMPT = """\
Summarize the conversation so far for your own later use; the messages above will be removed.
Keep every identifier you may need again exactly (dataset, cell, pipeline, run and partition
ids, file names in the kernel), the user's goal and decisions, what you measured with its
numbers, what you published, what failed and why, and what remains to do. Do not call tools.
Write plain text."""


class _StoppedError(Exception):
    pass


def wake(thread: AssistantThread) -> None:
    """Start a turn once the change that asks for it is committed. A turn that runs reads
    new messages itself; a second task finds the thread taken and ends."""
    from overbae.tasks.assistant import run_turn  # lazy: tasks import the API serializers

    transaction.on_commit(lambda: run_turn.delay(str(thread.pk)))


def ready(thread):
    """The queued messages a turn may take. The first pass prepares landed rows: after an
    import fails it stays queued for the landing that succeeds."""
    queued = thread.inputs.filter(state=Queued)
    if not Cell.objects.filter(
        dataset_id=thread.dataset_id, position=0, state=Cell.State.OK
    ).exists():
        queued = queued.exclude(request_key__startswith=threads.FIRST_PASS_KEY)
    return queued


def wake_dataset(dataset_id) -> None:
    """The dataset's source landed: a message that waited for it may start its turn."""
    thread = AssistantThread.objects.filter(dataset_id=dataset_id).first()
    if thread is not None and ready(thread).exists():
        wake(thread)


def settle(thread: AssistantThread) -> AssistantThread:
    """Mark a turn that stopped moving as interrupted. A page that reads the thread and a
    user who writes to it call this, for when nothing resumed it."""
    if thread.state != State.RUNNING or thread.updated_at > timezone.now() - STALE:
        return thread
    with transaction.atomic():
        held = AssistantThread.objects.select_for_update().get(pk=thread.pk)
        if held.state == State.RUNNING and held.updated_at <= timezone.now() - STALE:
            _interrupt(held)
    thread.refresh_from_db()
    return thread


def _interrupt(held) -> None:
    """The caller holds the thread's row lock."""
    # A newer number fences the lost turn out of every later write.
    held.turn += 1
    held.state = State.IDLE
    held.save(update_fields=["turn", "state", "updated_at"])
    log.append(
        held,
        Kind.STATUS,
        {"state": "interrupted", "detail": "The turn was interrupted. Send a message to continue."},
    )
    if ready(held).exists():
        wake(held)


def resume_lost() -> int:
    """Wake every turn whose worker stopped beating; ``run`` takes each over."""
    from overbae.tasks.assistant import run_turn  # lazy: tasks import the API serializers

    lost = AssistantThread.objects.filter(
        state=State.RUNNING, updated_at__lt=timezone.now() - LEASE
    ).values_list("pk", flat=True)
    for thread_id in lost:
        run_turn.delay(str(thread_id))
    return len(lost)


def run(thread_id) -> None:
    """The task: one turn, and the next one when messages arrived as it ended."""
    thread, resumed = _begin(thread_id)
    if thread is None:
        return
    beat = heartbeat.start(
        lambda: _held(thread).update(updated_at=timezone.now()), owner=f"assistant {thread_id}"
    )
    try:
        if resumed:
            _resume(thread)
        _turn(thread)
    except log.LostTurnError:
        logger.warning("assistant turn on %s was taken over", thread_id)
        return
    except _StoppedError:
        _stopped(thread)
    except Exception:
        logger.exception("assistant turn failed on %s", thread_id)
        _status(thread, "failed", "The turn failed. Send a message to continue.")
    finally:
        beat.set()
    if _held(thread).update(state=State.IDLE, stop_requested=False):
        log.changed(thread)
        if ready(thread).exists():
            wake(thread)


def _begin(thread_id) -> tuple[AssistantThread | None, bool]:
    """Take the thread for a turn, when a message waits and nothing holds it, or take over
    a turn whose worker was lost. True with a take-over."""
    thread = settle(AssistantThread.objects.select_related("project", "dataset").get(pk=thread_id))
    with transaction.atomic():
        thread = (
            AssistantThread.objects.select_for_update(of=("self",))
            .select_related("project", "dataset")
            .get(pk=thread_id)
        )
        resumed = thread.state == State.RUNNING
        if resumed:
            if thread.updated_at >= timezone.now() - LEASE:
                return None, False
            if _resumes(thread) >= RESUMES:
                _interrupt(thread)
                return None, False
            # The new number fences the lost worker out of every later write.
            thread.turn += 1
            thread.save(update_fields=["turn", "updated_at"])
            _status(thread, "resumed", "The reply continues after its worker stopped.")
        else:
            if not ready(thread).exists():
                return None, False
            # A first message on a new dataset waits for its source; landing wakes the thread.
            if thread.dataset.state == Dataset.State.LANDING:
                return None, False
            thread.state, thread.stop_requested = State.RUNNING, False
            thread.turn += 1
            thread.save(update_fields=["state", "stop_requested", "turn", "updated_at"])
    log.changed(thread)
    return thread, resumed


def _resumes(thread) -> int:
    """Take-overs since the newest message."""
    since = (
        thread.events.filter(kind=Kind.USER)
        .order_by("-sequence")
        .values_list("sequence", flat=True)
        .first()
    )
    return thread.events.filter(
        kind=Kind.STATUS, sequence__gt=since or 0, payload__state="resumed"
    ).count()


def _resume(thread) -> None:
    """Continue the reply the lost worker was writing. Calls without a result are read as
    abandoned (``messages``); a reply that ended, failed or asked the user is left."""
    last = (
        thread.events.exclude(kind=Kind.STATUS, payload__state="resumed")
        .order_by("-sequence")
        .only("kind", "payload", "presentation")
        .first()
    )
    if last is None or last.kind == Kind.STATUS:
        return
    if last.kind == Kind.ASSISTANT and not last.payload["calls"]:
        return
    # Any call of the newest reply that asked the user ends the answer, as in ``_answer``.
    reply = thread.events.filter(kind=Kind.ASSISTANT).order_by("-sequence").first()
    results = thread.events.filter(
        kind=Kind.TOOL_RESULT, sequence__gt=reply.sequence if reply else 0
    ).exclude(presentation=None)
    if any(presentation.asks(result.presentation) for result in results.only("presentation")):
        return
    _answer(thread, time.monotonic() + TURN_SECONDS)


def _held(thread):
    """The thread's row while this turn still holds it: no row once it lost the thread."""
    return AssistantThread.objects.filter(pk=thread.pk, turn=thread.turn, state=State.RUNNING)


def _alive(thread) -> bool:
    """Move the turn's progress mark. False when the user stopped it; a turn that lost the
    thread raises."""
    if not _held(thread).update(updated_at=timezone.now()):
        raise log.LostTurnError
    return not _held(thread).filter(stop_requested=True).exists()


def _status(thread, state: str, detail: str) -> None:
    log.append(thread, Kind.STATUS, {"state": state, "detail": detail}, turn=thread.turn)


def _stopped(thread) -> None:
    with transaction.atomic():
        _status(thread, "stopped", "Stopped.")
        thread.inputs.filter(state=Queued).update(state=AssistantInput.State.REMOVED)


def _turn(thread) -> None:
    deadline = time.monotonic() + TURN_SECONDS
    message = ready(thread).order_by("-urgent", "position").first()
    # Past the deadline the rest stay queued: ``run`` starts the next turn for them.
    while message is not None and time.monotonic() < deadline:
        if _deliver(thread, message):
            _answer(thread, deadline)
        message = ready(thread).order_by("-urgent", "position").first()


def _answer(thread, deadline: float) -> None:
    """Model calls and tool calls until the model replies without calls or asks a question."""
    while True:
        if not _alive(thread):
            raise _StoppedError
        if time.monotonic() > deadline:
            _status(
                thread, "failed", "The turn reached its time limit. Send a message to continue."
            )
            return
        # A message sent "now" joins at the next model call.
        for urgent in ready(thread).filter(urgent=True).order_by("position"):
            _deliver(thread, urgent)
        user = _actor(thread)
        if user is None:
            _status(thread, "failed", "The sender of the last message is not a project member.")
            return
        try:
            ensure_credits(user)
        except InsufficientCredits:
            _status(thread, "failed", "Credits are needed to continue.")
            return
        _compact(thread, user)
        reply = _model_call(thread, user)
        if reply is None or not reply.payload["calls"]:
            return
        asked = False
        for call in reply.payload["calls"]:
            if not _alive(thread):
                raise _StoppedError
            asked |= _run_tool(thread, user, call)
        if asked:
            return


def _actor(thread):
    """The sender of the newest message, while still a member of the project: the turn acts
    with their access and credits."""
    sender = (
        thread.events.filter(kind=Kind.USER)
        .order_by("-sequence")
        .values_list("payload__user", flat=True)
        .first()
    )
    if not thread.project.memberships.filter(user_id=sender).exists():
        return None
    return get_user_model().objects.get(pk=sender)


def _deliver(thread, message) -> bool:
    # The model reads what the dataset holds with a message, once: again only when it
    # changed, or when a summary replaced the message that carried it.
    window = _window(thread)
    known = next(
        (
            event.payload["context"]
            for event in reversed(window)
            if event.kind == Kind.USER and event.payload.get("context")
        ),
        None,
    )
    # Saving a recipe updates the row, not the dataset this turn loaded.
    # A turn's publish changes these; the brief reads them fresh.
    thread.dataset.refresh_from_db(
        fields=["preparation_pipeline", "intent", "train_format", "capability", "source_spec"]
    )
    now = threads.brief(thread)
    with transaction.atomic():
        # The thread first, as every queue change locks it: a stop that came after the
        # last reply is seen before the next message is taken from the queue.
        held = AssistantThread.objects.select_for_update().get(pk=thread.pk)
        if held.turn != thread.turn:
            raise log.LostTurnError
        if held.stop_requested:
            raise _StoppedError
        # The user may have edited or removed it since this turn read the queue.
        if not AssistantInput.objects.filter(pk=message.pk, state=Queued).update(
            state=AssistantInput.State.DELIVERED
        ):
            return False
        message.refresh_from_db()
        log.append(
            thread,
            Kind.USER,
            {
                "input": str(message.pk),
                "user": message.created_by_id,
                "text": message.text,
                "cells": message.cells,
                "interaction": message.interaction,
                **({"context": now} if now != known else {}),
            },
            turn=thread.turn,
        )
    return True


def _window(thread) -> list[AssistantEvent]:
    """The events the model reads: the newest summary and everything it does not cover."""
    covered = (
        thread.events.filter(kind=Kind.SUMMARY)
        .order_by("-sequence")
        .values_list("payload__through", flat=True)
        .first()
    )
    return list(thread.events.filter(sequence__gt=covered or 0).defer("presentation"))


def _charge(thread, user, stats, sequence) -> None:
    charge_llm_usage(
        user,
        stats,
        service=BillingService.DATA_WORKSHOP,
        project_id=thread.project_id,
        idempotency_key=f"assistant:{thread.pk}:{sequence}",
    )


def _record(thread, user, kind, payload) -> AssistantEvent:
    """Save what a model call produced and charge it, both or neither."""
    with transaction.atomic():
        event = log.append(thread, kind, payload, turn=thread.turn)
        _charge(thread, user, payload["usage"], event.sequence)
    return event


def _compact(thread, user) -> None:
    """Summarize the older conversation once the last reply's prompt grew too large. The
    cut falls before a user message or a reply, so no tool call loses its result."""
    window = _window(thread)
    replies = [event for event in window if event.kind == Kind.ASSISTANT]
    if (
        window[-1].kind == Kind.SUMMARY
        or not replies
        or (
            (replies[-1].payload["usage"].get("prompt_tokens") or 0)
            <= settings.ASSISTANT_COMPACT_TOKENS
        )
    ):
        return
    starts = [i for i, event in enumerate(window) if event.kind in (Kind.USER, Kind.ASSISTANT)]
    if len(starts) <= COMPACT_KEEP:
        return
    older = window[: starts[-COMPACT_KEEP]]
    earlier = [event for event in window if event.kind == Kind.SUMMARY and event not in older]
    chain = model_chain(TaskType.ASSISTANT)
    try:
        text, _calls, stats = call_llm_tools(
            [*messages(thread, [*older, *earlier]), {"role": "user", "content": COMPACT_PROMPT}],
            [],
            model=chain[0],
            fallback_models=chain[1:],
        )
    except IncompleteCompletionError as exc:
        # The provider charged for the summary it cut off, and what it wrote still serves.
        text, stats = exc.content, exc.stats
    except (RuntimeError, TimeoutError) as exc:
        logger.warning("assistant summary failed for thread %s: %s", thread.pk, exc)
        return
    _record(
        thread, user, Kind.SUMMARY, {"text": text, "through": older[-1].sequence, "usage": stats}
    )


def _user_text(payload) -> str:
    parts = [payload["text"]]
    if payload["cells"]:
        parts.append(
            "Dataset versions the user refers to (cell ids): " + ", ".join(payload["cells"])
        )
    if payload["interaction"]:
        answers = json.dumps(payload["interaction"]["values"], ensure_ascii=False)
        parts.append(f"Answers: {answers}")
    if payload.get("context"):
        parts.append(
            "The dataset now, as the platform measured it:\n"
            + json.dumps(payload["context"], ensure_ascii=False)
        )
    return "\n\n".join(part for part in parts if part)


def messages(thread, events) -> list[dict]:
    """The events as the model reads them. A call that never got a result is answered as
    abandoned, because the API refuses a tool call without one."""
    out = [{"role": "system", "content": prompt.system(thread)}]
    summary = next((event for event in reversed(events) if event.kind == Kind.SUMMARY), None)
    if summary is not None:
        out.append(
            {
                "role": "user",
                "content": "Summary of the earlier conversation:\n" + summary.payload["text"],
            }
        )
        events = [event for event in events if event.sequence > summary.payload["through"]]
    open_calls: list[str] = []

    def close():
        out.extend(
            {"role": "tool", "tool_call_id": call, "content": json.dumps(ABANDONED)}
            for call in open_calls
        )
        open_calls.clear()

    for event in events:
        payload = event.payload
        if event.kind == Kind.ASSISTANT:
            close()
            # A provider refuses a reply that says and calls nothing.
            if not payload["text"] and not payload["calls"]:
                continue
            message = {"role": "assistant", "content": payload["text"] or None}
            if payload["calls"]:
                message["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["raw"]},
                    }
                    for call in payload["calls"]
                ]
                open_calls.extend(call["id"] for call in payload["calls"])
            if payload["reasoning"]:
                message["reasoning_details"] = payload["reasoning"]
            out.append(message)
        elif event.kind == Kind.TOOL_RESULT and payload["call"] in open_calls:
            open_calls.remove(payload["call"])
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": payload["call"],
                    "content": tools.bounded(payload["content"]),
                }
            )
        elif event.kind == Kind.USER:
            close()
            out.append({"role": "user", "content": _user_text(payload)})
    close()
    return out


def _model_call(thread, user) -> AssistantEvent | None:
    published = time.monotonic()
    shown = None

    def progress(text, calls):
        nonlocal published, shown
        now = time.monotonic()
        steps = [tools.summary(call["function"]["arguments"]) for call in calls]
        if now - published > DRAFT_INTERVAL_SECONDS and (len(text), steps) != shown:
            if not _alive(thread):
                raise _StoppedError
            sent = shown[0] if shown else 0
            log.draft(thread, text[sent:], steps, at=sent)
            published, shown = now, (len(text), steps)

    chain = model_chain(TaskType.ASSISTANT)
    try:
        text, calls, stats = stream_llm_tools(
            messages(thread, _window(thread)),
            tools.specifications(),
            model=chain[0],
            fallback_models=chain[1:],
            on_progress=progress,
        )
    except IncompleteCompletionError as exc:
        # The provider charged for the reply it cut off.
        with transaction.atomic():
            event = log.append(
                thread,
                Kind.STATUS,
                {
                    "state": "failed",
                    "detail": "The model reached its output limit before it finished.",
                },
                turn=thread.turn,
            )
            _charge(thread, user, exc.stats, event.sequence)
        return None
    except (RuntimeError, TimeoutError) as exc:
        logger.warning("assistant model call failed for thread %s: %s", thread.pk, exc)
        _status(thread, "failed", "The model did not answer. Send a message to continue.")
        return None
    return _record(
        thread,
        user,
        Kind.ASSISTANT,
        {
            "text": text,
            # Provider call ids repeat across replies; these are unique in the thread.
            "calls": [
                {
                    "id": uuid.uuid4().hex[:16],
                    "name": call["function"]["name"],
                    "raw": call["function"]["arguments"] or "{}",
                }
                for call in calls
            ],
            # The provider needs these back unchanged with the reply's tool results.
            "reasoning": stats.pop("reasoning_details"),
            "usage": stats,
        },
    )


def _run_tool(thread, user, call) -> bool:
    """Run one call and save its result. True when it asked the user a question."""
    try:
        arguments = json.loads(call["raw"])
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        outcome = tools.Outcome(False, {"error": "The arguments were not a JSON object."})
    else:
        turn = tools.Turn(thread, user, call["id"], lambda: _alive(thread))
        outcome = tools.call(turn, call["name"], arguments)
    log.append(
        thread,
        Kind.TOOL_RESULT,
        {"call": call["id"], "name": call["name"], "ok": outcome.ok, "content": outcome.content},
        turn=thread.turn,
        presentation=outcome.presentation,
    )
    return outcome.asks


def stop(thread: AssistantThread) -> None:
    """Stop the running turn at its next step, and a cell that runs now at once. Queued
    messages are dropped; jobs the turn started continue."""
    from overbae.tasks.assistant import stop_kernel  # lazy: same cycle as ``wake``

    with transaction.atomic():
        held = AssistantThread.objects.select_for_update().get(pk=thread.pk)
        if held.state == State.RUNNING:
            held.stop_requested = True
            held.save(update_fields=["stop_requested"])
            transaction.on_commit(
                lambda: stop_kernel.delay(str(held.project_id), str(held.pk), held.turn)
            )
        elif held.inputs.filter(state=Queued).exists():
            # Messages that wait for the dataset's source to land.
            log.append(held, Kind.STATUS, {"state": "stopped", "detail": "Stopped."})
            held.inputs.filter(state=Queued).update(state=AssistantInput.State.REMOVED)
    log.changed(thread)
