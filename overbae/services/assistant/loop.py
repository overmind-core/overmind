"""The assistant's loop, one step at a time. A thread is not a process: each step reads the
event log, does one thing and saves it. A deploy or a crash delays the next step and loses
nothing that was saved.

A step holds a claim on its thread and writes only through it. A step that outlives its
lease loses the claim to the next one, and from then on it can write nothing."""

import contextlib
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
    AssistantHandoff,
    AssistantInput,
    AssistantThread,
    BillingService,
    Dataset,
)
from overbae.services.assistant import jobs, log, prompt, tools
from overbae.services.billing_ledger import InsufficientCredits, charge_llm_usage, ensure_credits
from overbae.services.datasets import workbench
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)
Kind, State = AssistantEvent.Kind, AssistantThread.State
Queued = AssistantInput.State.QUEUED

# A running step that is silent for this long has lost its worker. Longer than the longest
# tool call: a session start, its staged datasets and a cell at the time limit.
LEASE = timedelta(minutes=15)
HEARTBEAT_SECONDS = 10
DRAFT_INTERVAL_SECONDS = 0.1
# A step enqueues the next one itself; the reconciler takes over when that was lost.
READY_GRACE = timedelta(seconds=30)
ABANDONED = {"error": "This call did not run: the run was stopped or interrupted."}
# Newest user messages and replies the model keeps word for word after a summary.
COMPACT_KEEP = 6
COMPACT_PROMPT = """\
Summarize the conversation so far for your own later use; the messages above will be removed.
Keep every identifier you may need again exactly (dataset, cell, pipeline, package, run,
partition and preparation ids, fingerprints, file names in the kernel), the user's goal and
decisions, what you measured with its numbers, what you published, what failed and why, and
what remains to do. Do not call tools. Write plain text."""


class _StoppedError(Exception):
    pass


def advance(thread_id) -> bool:
    """Run one step of the thread. True when another step is due."""
    settle(thread_id)
    thread = _claim(thread_id)
    if thread is None:
        return False
    try:
        more = _attempt(thread)
        held = _held(thread).filter(state=State.RUNNING)
        if more:
            held.update(state=State.READY, updated_at=timezone.now())
        else:
            # A stop that arrives as the run ends has nothing left to stop.
            held.update(state=State.IDLE, stop_requested=False, updated_at=timezone.now())
    except log.LostClaimError:
        logger.warning("assistant step lost its claim on thread %s", thread_id)
        return False
    # A stop that arrived while this step put the thread on hold ends that wait now.
    if AssistantThread.objects.filter(
        pk=thread_id, state=State.WAITING, stop_requested=True
    ).exists():
        stop(thread)
    log.changed(thread_id)
    return more


def _attempt(thread) -> bool:
    try:
        return _step(thread)
    except _StoppedError:
        _stopped(thread, "Stopped.", claim=thread.claim)
    except log.LostClaimError:
        raise
    except Exception:
        logger.exception("assistant step failed for thread %s", thread.pk)
        _status(thread, "failed", "The step failed. Send a message to continue.")
    return False


def _claim(thread_id) -> AssistantThread | None:
    """Take the thread for one step. None when a step holds it or it waits for jobs."""
    with transaction.atomic():
        thread = (
            AssistantThread.objects.select_for_update(of=("self",))
            .select_related("project")
            .get(pk=thread_id)
        )
        if thread.state == State.WAITING:
            return None
        if thread.state == State.RUNNING:
            if thread.updated_at > timezone.now() - LEASE:
                return None
            # A paid model call or a tool may have run: the user decides whether to go on.
            detail = "The run was interrupted. Send a message to continue."
            log.append(thread, Kind.STATUS, {"state": "interrupted", "detail": detail})
        thread.state = State.RUNNING
        thread.claim += 1
        thread.save(update_fields=["state", "claim", "updated_at"])
    return thread


def _held(thread):
    """The thread's row while this step still holds it: no row once the claim is lost."""
    return AssistantThread.objects.filter(pk=thread.pk, claim=thread.claim)


def _status(thread, state: str, detail: str) -> None:
    log.append(thread, Kind.STATUS, {"state": state, "detail": detail}, claim=thread.claim)


def _window(thread) -> list[AssistantEvent]:
    """The events a step reads: the newest summary and everything it does not cover."""
    covered = (
        thread.events.filter(kind=Kind.SUMMARY)
        .order_by("-sequence")
        .values_list("payload__through", flat=True)
        .first()
    )
    return list(thread.events.filter(sequence__gt=covered or 0).defer("presentation"))


def _pending(events) -> list[dict]:
    """Tool calls of the latest reply that have no result and were not abandoned."""
    for index in range(len(events) - 1, -1, -1):
        if events[index].kind == Kind.ASSISTANT:
            later = events[index + 1 :]
            if any(event.kind in (Kind.USER, Kind.STATUS) for event in later):
                return []
            answered = {event.payload["call"] for event in later}
            return [call for call in events[index].payload["calls"] if call["id"] not in answered]
    return []


def _actor(thread):
    """The sender of the newest message, while still a member of the project: the step
    acts with their access and credits."""
    sender = (
        thread.events.filter(kind=Kind.USER)
        .order_by("-sequence")
        .values_list("payload__user", flat=True)
        .first()
    )
    if not thread.project.memberships.filter(user_id=sender).exists():
        return None
    return get_user_model().objects.get(pk=sender)


def _landing(thread) -> bool:
    """The dataset the conversation is about still receives its source."""
    return (
        thread.subject_kind == "dataset"
        and Dataset.objects.filter(pk=thread.subject_id, state=Dataset.State.LANDING).exists()
    )


def _step(thread) -> bool:
    if _held(thread).filter(stop_requested=True).exists():
        raise _StoppedError
    window = _window(thread)
    # A summary changes what the model reads, never what happens next.
    events = [event for event in window if event.kind != Kind.SUMMARY]
    pending = _pending(events)
    resting = not pending and (not events or events[-1].kind in (Kind.STATUS, Kind.ASSISTANT))
    queued = thread.inputs.filter(state=Queued)
    # An urgent message waits only for the calls of the reply before it.
    message = None if pending else queued.filter(urgent=True).first()
    if message is None and resting:
        message = queued.first()
    if message is not None:
        # The first message of a new dataset waits here until its source has landed.
        return not _landing(thread) and _deliver(thread, message)
    if resting:
        return False
    user = _actor(thread)
    if user is None:
        _status(thread, "failed", "The sender of the last message is not a project member.")
        return False
    if pending:
        return _run_tool(thread, user, pending[0])
    try:
        ensure_credits(user)
    except InsufficientCredits:
        _status(thread, "failed", "Credits are needed to continue.")
        return False
    if window[-1].kind != Kind.SUMMARY and _compact(thread, user, window):
        return True
    _model_turn(thread, user, window)
    return True


def _deliver(thread, message) -> bool:
    with transaction.atomic():
        # The user may have edited or removed it since this step read the queue.
        if not AssistantInput.objects.filter(pk=message.pk, state=Queued).update(
            state=AssistantInput.State.DELIVERED
        ):
            return True
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
            },
            claim=thread.claim,
        )
    return True


def _compact(thread, user, window) -> bool:
    """Summarize the older conversation once the last reply's prompt grew too large. The
    cut falls before a user message or a reply, so no tool call loses its result."""
    replies = [event for event in window if event.kind == Kind.ASSISTANT]
    if not replies or (
        (replies[-1].payload["usage"].get("prompt_tokens") or 0)
        <= settings.ASSISTANT_COMPACT_TOKENS
    ):
        return False
    starts = [
        index for index, event in enumerate(window) if event.kind in (Kind.USER, Kind.ASSISTANT)
    ]
    if len(starts) <= COMPACT_KEEP:
        return False
    older = window[: starts[-COMPACT_KEEP]]
    # The summary the window starts from stays part of what is summarized.
    earlier = [event for event in window if event.kind == Kind.SUMMARY and event not in older]
    chain = model_chain(TaskType.ASSISTANT)
    try:
        text, _calls, stats = call_llm_tools(
            [*messages(thread, [*older, *earlier]), {"role": "user", "content": COMPACT_PROMPT}],
            [],
            model=chain[0],
            fallback_models=chain[1:],
        )
    except (RuntimeError, TimeoutError) as exc:
        logger.warning("assistant summary failed for thread %s: %s", thread.pk, exc)
        return False
    _record(
        thread, user, Kind.SUMMARY, {"text": text, "through": older[-1].sequence, "usage": stats}
    )
    return True


def _record(thread, user, kind, payload) -> None:
    """Save what a model call produced and charge it, both or neither. A step makes one
    model call, so its claim names the charge."""
    with transaction.atomic():
        log.append(thread, kind, payload, claim=thread.claim)
        _charge(thread, user, payload["usage"])


def _charge(thread, user, stats) -> None:
    charge_llm_usage(
        user,
        stats,
        service=BillingService.DATA_WORKSHOP,
        project_id=thread.project_id,
        idempotency_key=f"assistant:{thread.pk}:{thread.claim}",
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


def _model_turn(thread, user, window) -> None:
    beat = published = time.monotonic()
    shown = None

    def progress(text, names):
        nonlocal beat, published, shown
        now = time.monotonic()
        if now - beat > HEARTBEAT_SECONDS:
            beat = now
            if not _held(thread).update(updated_at=timezone.now()):
                raise log.LostClaimError
            if _held(thread).filter(stop_requested=True).exists():
                raise _StoppedError
        if now - published > DRAFT_INTERVAL_SECONDS and (len(text), names) != shown:
            sent = shown[0] if shown else 0
            log.draft(thread.pk, text[sent:], names, first=shown is None)
            published, shown = now, (len(text), names)

    chain = model_chain(TaskType.ASSISTANT)
    try:
        text, calls, stats = stream_llm_tools(
            messages(thread, window),
            tools.specifications(thread),
            model=chain[0],
            fallback_models=chain[1:],
            on_progress=progress,
        )
    except IncompleteCompletionError as exc:
        # The provider charged for the reply it cut off.
        _charge(thread, user, exc.stats)
        _status(thread, "failed", "The model reached its output limit before it finished.")
        return
    except (RuntimeError, TimeoutError) as exc:
        logger.warning("assistant model call failed for thread %s: %s", thread.pk, exc)
        _status(thread, "failed", "The model did not answer. Send a message to continue.")
        return
    _record(
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
    if not _held(thread).update(updated_at=timezone.now()):
        raise log.LostClaimError
    try:
        arguments = json.loads(call["raw"])
    except ValueError:
        arguments = None
    if not isinstance(arguments, dict):
        outcome = tools.Outcome(False, {"error": "The arguments were not a JSON object."})
    else:
        outcome = tools.call(thread, user, call["id"], call["name"], arguments)
    if outcome.jobs:
        waiting = {
            "call": call["id"],
            "name": call["name"],
            "user": user.pk,
            "started": outcome.content,
            "approval": outcome.approval,
            "jobs": outcome.jobs,
        }
        if not _held(thread).update(
            state=State.WAITING, waiting_on=waiting, updated_at=timezone.now()
        ):
            raise log.LostClaimError
        return False
    payload = {
        "call": call["id"],
        "name": call["name"],
        "ok": outcome.ok,
        "content": outcome.content,
        "asks": outcome.asks,
        "approval": outcome.approval,
    }
    log.append(
        thread, Kind.TOOL_RESULT, payload, claim=thread.claim, presentation=outcome.presentation
    )
    return True


def _end_wait(thread) -> None:
    """Answer the call a waiting thread holds with what its jobs are now. The caller holds
    the thread's row lock."""
    waiting = thread.waiting_on
    payload = {
        "call": waiting["call"],
        "name": waiting["name"],
        "ok": True,
        "content": tools.finished(thread, waiting),
        "asks": None,
        "approval": waiting["approval"],
    }
    log.append(thread, Kind.TOOL_RESULT, payload)


def settle(thread_id) -> None:
    """End a thread's wait when its jobs rest, or when an urgent message wants the thread
    while they still move."""
    with transaction.atomic():
        thread = (
            AssistantThread.objects.select_for_update(of=("self",))
            .select_related("project")
            .filter(pk=thread_id, state=State.WAITING)
            .first()
        )
        if thread is None:
            return
        urgent = thread.inputs.filter(state=Queued, urgent=True).exists()
        if jobs.moving(thread.waiting_on["jobs"]) and not urgent:
            return
        _end_wait(thread)
        thread.state, thread.waiting_on = State.READY, None
        thread.save(update_fields=["state", "waiting_on", "updated_at"])


def _stopped(thread, detail: str, *, claim=None) -> None:
    with transaction.atomic():
        log.append(thread, Kind.STATUS, {"state": "stopped", "detail": detail}, claim=claim)
        thread.inputs.filter(state=Queued).update(state=AssistantInput.State.REMOVED)
        AssistantThread.objects.filter(pk=thread.pk).update(
            stop_requested=False, state=State.IDLE, waiting_on=None, updated_at=timezone.now()
        )


def stop(thread: AssistantThread) -> None:
    """Stop the run at its next step boundary and drop its queued messages. A wait ends
    now: a pipeline run or a handoff it holds for is cancelled. Other started jobs are the
    user's paid work and continue; the log says how many."""
    with transaction.atomic():
        thread = (
            AssistantThread.objects.select_for_update(of=("self",))
            .select_related("project")
            .get(pk=thread.pk)
        )
        if thread.state in (State.RUNNING, State.READY):
            thread.stop_requested = True
            thread.save(update_fields=["stop_requested"])
        elif thread.state == State.WAITING:
            held = thread.waiting_on["jobs"]
            for job in held:
                if job["kind"] == "dataset_pipeline":
                    # The run may have ended since the thread last looked.
                    with contextlib.suppress(DatasetError):
                        workbench.cancel_run(thread.project, job["id"])
                elif job["kind"] == "handoff":
                    AssistantHandoff.objects.filter(
                        pk=job["id"], state=AssistantHandoff.State.OPEN
                    ).update(state=AssistantHandoff.State.CANCELLED)
            _end_wait(thread)
            continuing = len(jobs.moving(held))
            _stopped(
                thread,
                f"Stopped. Started jobs that continue: {continuing}." if continuing else "Stopped.",
            )
    log.changed(thread.pk)


def due() -> list[str]:
    """Threads that may have a step to run. It only reads: ``advance`` decides under the
    thread's lock, so a thread listed twice or too early costs one refused claim."""
    now = timezone.now()
    threads = AssistantThread.objects
    # One query per job kind, however many threads wait.
    held = dict(threads.filter(state=State.WAITING).values_list("pk", "waiting_on__jobs"))
    moving = jobs.moving([job for listed in held.values() for job in listed])
    rested = [
        pk
        for pk, listed in held.items()
        if not any((job["kind"], job["id"]) in moving for job in listed)
    ]
    wanted = AssistantInput.objects.filter(
        state=Queued, urgent=True, thread__state=State.WAITING
    ).values_list("thread_id", flat=True)
    lost = threads.filter(state=State.RUNNING, updated_at__lt=now - LEASE)
    stalled = threads.filter(state=State.READY, updated_at__lt=now - READY_GRACE)
    queued = threads.filter(state=State.IDLE, inputs__state=Queued)
    listed = [*rested, *wanted]
    for found in (lost, stalled, queued):
        listed += found.values_list("pk", flat=True)
    return [str(pk) for pk in dict.fromkeys(listed)]
