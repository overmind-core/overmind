import json
import time
from datetime import timedelta

import pytest
from conftest import frozen_dataset
from django.utils import timezone
from factories import auth_client, make_capability, make_project, make_user
from fakes.llm import tool_call

from overbae.core.llms import stream_llm_tools
from overbae.models import (
    AssistantInput,
    AssistantThread,
    BillingTelemetry,
    Dataset,
    DatasetPipeline,
    ProjectMembership,
)
from overbae.services import workspaces
from overbae.services.assistant import log, threads, tools, turn
from overbae.services.datasets import land, lifecycle, list_activity
from overbae.tasks import assistant as assistant_tasks

pytestmark = [pytest.mark.django_db, pytest.mark.covers("assistant-turn")]


@pytest.fixture(autouse=True)
def woken(monkeypatch):
    """Turns run where the test says: a woken thread is only recorded."""
    started = []
    monkeypatch.setattr(assistant_tasks.run_turn, "delay", started.append)
    return started


@pytest.fixture
def user():
    return make_user()


@pytest.fixture
def thread(user):
    project = make_project(member=user)
    return threads.open_thread(frozen_dataset(project, user=user))


def say(thread, user, text, key=None, **extra):
    threads.send(thread, user, request_key=key or text, text=text, **extra)
    turn.run(thread.pk)
    thread.refresh_from_db()


def kinds(thread):
    return [event.kind for event in thread.events.all()]


def results(thread):
    return [event.payload for event in thread.events.filter(kind="tool_result")]


KEEP = """\
import shutil, sys
shutil.copy(sys.argv[1], sys.argv[2])
"""


def python(code, versions=(), **extra):
    return tool_call(
        "run_python", {"summary": "Counting", "code": code, "versions": list(versions), **extra}
    )


def test_a_turn_runs_code_on_the_datasets_version_and_charges_each_model_call(
    fake_llm, user, thread
):
    source = thread.dataset.source
    count = f"import pandas\nlen(pandas.read_parquet('data/{source.pk}.parquet'))"
    fake_llm.stream_rounds(
        [([python(count, [str(source.pk)])], "Counting the rows."), ([], "It has 2 rows.")]
    )
    try:
        say(thread, user, "How many rows are there?")
    finally:
        workspaces.provider().remove(str(thread.project_id))

    assert kinds(thread) == ["user", "assistant", "tool_result", "assistant"]
    (counted,) = results(thread)
    assert counted["ok"] and counted["content"]["output"] == "2\n", counted
    assert thread.state == AssistantThread.State.IDLE
    charged = BillingTelemetry.objects.filter(idempotency_key__startswith=f"assistant:{thread.pk}")
    assert charged.count() == 2 and {row.user_id for row in charged} == {user.pk}
    # The model read the dataset's facts with the message, once.
    first = fake_llm.streamed()[0].body["messages"][-1]["content"]
    assert f"data/{source.pk}.parquet" in first


def test_the_summary_of_a_call_is_for_the_reader_and_no_tool_receives_it(fake_llm, user, thread):
    query = {"summary": "Counting", "dataset": str(thread.dataset_id), "sql": "select 1 as one"}
    fake_llm.stream_rounds([([tool_call("query_dataset", query)], ""), ([], "One.")])
    say(thread, user, "Count.")
    (counted,) = results(thread)
    # A catalog tool refuses an argument it does not declare.
    assert counted["ok"], counted


def test_a_question_ends_the_turn_and_the_answer_continues_it(fake_llm, user, thread):
    question = {"type": "Choices", "id": "task", "prompt": "Which task?"}
    options = [{"label": "Chat", "value": "chat"}, {"label": "Decision", "value": "decision"}]
    asked = tool_call("present", {"summary": "Asking", "root": {**question, "options": options}})
    fake_llm.stream_rounds([([asked], "The file holds questions."), ([], "Chat it is.")])
    say(thread, user, "Train a decision model.")
    assert kinds(thread) == ["user", "assistant", "tool_result"]
    assert len(fake_llm.streamed()) == 1

    answer = {"call": results(thread)[0]["call"], "id": "task", "values": {"choice": "chat"}}
    say(thread, user, "Chat", interaction=answer)
    assert kinds(thread)[3:] == ["user", "assistant"]


def test_a_message_waits_for_its_source_and_landing_starts_the_turn(
    user, woken, django_capture_on_commit_callbacks
):
    project = make_project(member=user)
    dataset = Dataset.objects.create(project=project, name="new", state=Dataset.State.LANDING)
    thread = threads.open_thread(dataset)
    threads.send(thread, user, request_key="k", text="Prepare it.")
    turn.run(thread.pk)
    thread.refresh_from_db()
    assert thread.state == AssistantThread.State.IDLE and not thread.events.exists()

    Dataset.objects.filter(pk=dataset.pk).update(state=Dataset.State.IDLE)
    with django_capture_on_commit_callbacks(execute=True):
        turn.wake_dataset(dataset.pk)
    assert woken == [str(thread.pk)]


def test_stop_ends_the_turn_at_its_next_step_and_drops_the_queue(fake_llm, user, thread, woken):
    def reply(_request):
        # The user stops while the model writes a call, and the reply streams on past the
        # next draft, where the turn sees the stop.
        AssistantThread.objects.filter(pk=thread.pk).update(stop_requested=True)
        threads.send(thread, user, request_key="later", text="And then?")
        time.sleep(turn.DRAFT_INTERVAL_SECONDS * 2)
        query = {"summary": "Counting", "dataset": str(thread.dataset_id), "sql": "select 1"}
        return {"content": None, "tool_calls": [tool_call("query_dataset", query)], "usage": {}}

    fake_llm.on(lambda request: bool(request.body.get("stream")), reply)
    say(thread, user, "Count.")

    # The unfinished reply is dropped and its call never runs.
    assert kinds(thread) == ["user", "status"]
    assert thread.events.last().payload["state"] == "stopped"
    assert thread.state == AssistantThread.State.IDLE and not thread.stop_requested
    assert not thread.inputs.filter(state="queued").exists()


def test_a_turn_that_stopped_moving_is_interrupted_and_writes_nothing_more(
    user, thread, woken, django_capture_on_commit_callbacks
):
    threads.send(thread, user, request_key="k", text="Prepare it.")
    stale = timezone.now() - turn.STALE - timedelta(minutes=1)
    AssistantThread.objects.filter(pk=thread.pk).update(state="running", turn=4)
    assert str(thread.dataset_id) in list_activity.current_work([thread.dataset])
    AssistantThread.objects.filter(pk=thread.pk).update(updated_at=stale)
    thread.refresh_from_db()
    # The dataset list does not show a lost turn as current work.
    assert str(thread.dataset_id) not in list_activity.current_work([thread.dataset])

    with django_capture_on_commit_callbacks(execute=True):
        settled = turn.settle(thread)
    assert settled.state == AssistantThread.State.IDLE and settled.turn == 5
    assert settled.events.last().payload["state"] == "interrupted"
    # The queued message starts a new turn; the lost one is fenced out.
    assert woken == [str(thread.pk)]
    with pytest.raises(log.LostTurnError):
        log.append(thread, "assistant", {"text": "late", "calls": [], "usage": {}}, turn=4)


def lost_midway(thread, user, *, calls):
    """The record a worker leaves when it is lost while its reply is under way."""
    threads.send(thread, user, request_key="lost", text="Count.")
    message = thread.inputs.get()
    AssistantThread.objects.filter(pk=thread.pk).update(state="running", turn=4)
    AssistantInput.objects.filter(pk=message.pk).update(state=AssistantInput.State.DELIVERED)
    log.append(
        thread,
        "user",
        {
            "input": str(message.pk),
            "user": user.pk,
            "text": "Count.",
            "cells": [],
            "interaction": None,
        },
        turn=4,
    )
    log.append(
        thread, "assistant", {"text": "", "calls": calls, "reasoning": None, "usage": {}}, turn=4
    )
    AssistantThread.objects.filter(pk=thread.pk).update(
        updated_at=timezone.now() - turn.LEASE - timedelta(seconds=1)
    )


def test_a_turn_whose_worker_was_lost_is_taken_over_and_continues_its_reply(
    fake_llm, user, thread, woken
):
    query = {"summary": "Counting", "dataset": "x", "sql": "select 1"}
    lost_midway(
        thread, user, calls=[{"id": "c1", "name": "query_dataset", "raw": json.dumps(query)}]
    )

    assert turn.resume_lost() == 1 and woken == [str(thread.pk)]
    fake_llm.stream_rounds([([], "It has 2 rows.")])
    turn.run(thread.pk)
    thread.refresh_from_db()

    assert kinds(thread) == ["user", "assistant", "status", "assistant"]
    assert thread.events.get(kind="status").payload["state"] == "resumed"
    # The call that may have run is answered as abandoned, never run again.
    assert not results(thread)
    (request,) = fake_llm.streamed()
    answered = [m for m in request.body["messages"] if m.get("tool_call_id") == "c1"]
    assert json.loads(answered[0]["content"]) == turn.ABANDONED
    assert thread.state == AssistantThread.State.IDLE and thread.turn == 5
    with pytest.raises(log.LostTurnError):
        log.append(thread, "assistant", {"text": "late", "calls": [], "usage": {}}, turn=4)


@pytest.mark.parametrize("ended", ["reply", "failure"])
def test_a_reply_that_ended_is_not_written_again_when_its_turn_is_taken_over(
    fake_llm, user, thread, ended
):
    lost_midway(thread, user, calls=[])
    if ended == "failure":
        log.append(thread, "status", {"state": "failed", "detail": "The model did not answer."})
    turn.run(thread.pk)
    thread.refresh_from_db()
    assert thread.events.last().payload["state"] == "resumed"
    assert fake_llm.streamed() == []
    assert thread.state == AssistantThread.State.IDLE


def test_a_turn_lost_again_and_again_is_interrupted(fake_llm, user, thread):
    lost_midway(thread, user, calls=[])
    for _ in range(turn.RESUMES):
        log.append(thread, "status", {"state": "resumed", "detail": "Resumed."})
    turn.run(thread.pk)
    thread.refresh_from_db()
    assert thread.events.last().payload["state"] == "interrupted"
    assert thread.state == AssistantThread.State.IDLE
    assert fake_llm.streamed() == []


def test_a_sender_who_left_the_project_runs_nothing(fake_llm, user, thread):
    threads.send(thread, user, request_key="k", text="Prepare it.")
    ProjectMembership.objects.filter(user=user).delete()
    turn.run(thread.pk)
    assert thread.events.last().payload["state"] == "failed"
    assert fake_llm.streamed() == []


def test_one_call_retains_a_pipeline_and_runs_it_and_a_correction_is_its_next_revision(
    fake_llm, user, thread, eager
):
    manifest = {"version": 1, "steps": [{"name": "Keep", "entrypoint": "keep.py"}]}
    files = {"manifest.json": json.dumps(manifest), "keep.py": KEEP}
    run = {"summary": "Publishing", "source_cell": str(thread.dataset.source.pk), "mode": "publish"}
    first = tool_call("run_pipeline", {**run, "name": "Keep", "files": files})
    fake_llm.stream_rounds([([first], ""), ([], "Published.")])
    say(thread, user, "Prepare it.")

    (saved,) = DatasetPipeline.objects.all()
    (published,) = results(thread)
    assert published["ok"], published
    assert saved.datasetpipelinerun_set.get().state == "completed"

    corrected = {**run, "pipeline": str(saved.family), "files": {**files, "keep.py": "pass\n"}}
    fake_llm.forget()
    fake_llm.stream_rounds([([tool_call("run_pipeline", corrected)], ""), ([], "Again.")])
    say(thread, user, "Correct it.")
    second = DatasetPipeline.objects.exclude(pk=saved.pk).get()
    assert (second.family, second.revision, second.name) == (saved.family, 2, "Keep")
    # The corrected script writes nothing: the failed step says why.
    failed = results(thread)[-1]
    assert not failed["ok"] and failed["content"]["error"]


def test_a_publish_runs_a_sample_first_and_a_failing_sample_publishes_nothing(
    fake_llm, user, eager
):
    project = make_project(member=user)
    rows = [{"input": f"q{i}", "expected_output": "a"} for i in range(tools.SAMPLE_ROWS + 1)]
    thread = threads.open_thread(frozen_dataset(project, rows, user=user))
    source = thread.dataset.source
    manifest = {"version": 1, "steps": [{"name": "Break", "entrypoint": "break.py"}]}
    files = {"manifest.json": json.dumps(manifest), "break.py": "raise SystemExit('broken')\n"}
    call = {"summary": "Publishing", "source_cell": str(source.pk), "mode": "publish"}
    fake_llm.stream_rounds(
        [([tool_call("run_pipeline", {**call, "name": "Break", "files": files})], ""), ([], "")]
    )
    say(thread, user, "Prepare it.")

    (failed,) = results(thread)
    assert not failed["ok"] and failed["content"]["sample"] and failed["content"]["error"]
    (run,) = DatasetPipeline.objects.get().datasetpipelinerun_set.all()
    assert (run.mode, run.state) == ("preview", "failed")
    thread.dataset.refresh_from_db()
    assert thread.dataset.active_cell == source


def test_the_first_message_carries_each_versions_fit_profile_and_the_capability_contract(user):
    project = make_project(member=user)
    capability = make_capability(
        project,
        "Support",
        improvement_metadata={"capability_card": {"system_prompt": "Answer tickets."}},
    )
    reply = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]
    rows = [{"messages": reply, "queue": "billing"}] * 2 + [
        {"messages": reply[:1] + [{"role": "assistant", "content": "Bye"}], "queue": "tech"}
    ]
    dataset = frozen_dataset(project, rows, user=user, capability=capability)
    brief = threads.brief(threads.open_thread(dataset))

    assert brief["capability"]["system_prompt"] == "Answer tickets."
    source = brief["source"]
    assert source["fit"]["sft"]["ok"] and not source["fit"]["decision"]["ok"]
    assert source["duplicate_rows"] == 1
    queue = next(column for column in source["columns"] if column["name"] == "queue")
    assert queue["distinct"] == 2 and queue["top"][0] == {"value": "billing", "count": 2}


def test_a_dataset_made_with_an_intent_queues_one_first_pass_and_a_split_one_for_both(user):
    project = make_project(member=user)
    client = auth_client(user)
    body = {
        "project": str(project.pk),
        "name": "Tickets",
        "brief": "Keep the tone.",
        "prepare": True,
        "source": {"rows": [{"text": f"t{i}"} for i in range(4)]},
    }
    made = client.post("/api/datasets/", {**body, "intent": "train"}, format="json").data
    (message,) = AssistantInput.objects.filter(thread__dataset_id=made["id"])
    assert message.text == f"{threads.FIRST_PASS['train']}\n\nKeep the tone."
    assert message.state == AssistantInput.State.QUEUED

    plain = client.post("/api/datasets/", {**body, "prepare": False}, format="json").data
    assert not AssistantThread.objects.filter(dataset_id=plain["id"]).exists()

    split = {**body, "name": "Pair", "eval_percent": 50, "position": "tail"}
    pair = client.post("/api/datasets/split/", split, format="json").data
    (first,) = AssistantInput.objects.filter(thread__dataset_id=pair["train"]["id"])
    assert first.text.startswith(threads.SPLIT_PASS)
    assert not AssistantThread.objects.filter(dataset_id=pair["eval"]["id"]).exists()


def test_a_first_pass_waits_through_a_failed_import_for_a_landed_source(
    user, woken, django_capture_on_commit_callbacks
):
    project = make_project(member=user)
    dataset = Dataset.objects.create(
        project=project, name="t", intent="train", state=Dataset.State.ERROR
    )
    message = threads.queue_first_pass(dataset, user)
    thread = message.thread
    turn.run(thread.pk)
    message.refresh_from_db()
    assert message.state == AssistantInput.State.QUEUED and not thread.events.exists()

    land.land_rows(dataset, [{"text": "a"}])
    with django_capture_on_commit_callbacks(execute=True):
        turn.wake_dataset(dataset.pk)
    assert woken == [str(thread.pk)]


def test_a_kernel_that_restarted_is_told_to_the_model(fake_llm, user, thread):
    provider = workspaces.provider()
    project, conversation = str(thread.project_id), thread.pk.hex
    fake_llm.stream_rounds(
        [([python("x = 1")], ""), ([], "Set."), ([python("x")], ""), ([], "Gone.")]
    )
    try:
        say(thread, user, "Set x.")
        provider.stop(project, conversation)
        say(thread, user, "Read x.")
    finally:
        provider.remove(project)
    restarted = results(thread)[1]["content"]
    assert "restarted" in restarted["note"] and "NameError" in restarted["error"]


def test_a_long_conversation_is_summarized_and_no_call_loses_its_result(
    fake_llm, user, thread, settings
):
    settings.ASSISTANT_COMPACT_TOKENS = 0
    query = {"summary": "Counting", "dataset": str(thread.dataset_id), "sql": "select 1"}
    fake_llm.on_json(lambda r: not r.body.get("stream"), lambda _r: {})
    fake_llm.on(lambda r: not r.body.get("stream"), {"content": "Earlier: counted.", "usage": {}})
    rounds = []
    for _ in range(4):
        rounds += [([tool_call("query_dataset", query)], ""), ([], "Done.")]
    fake_llm.stream_rounds(rounds)
    for index in range(4):
        say(thread, user, f"Count {index}.")
    assert "summary" in kinds(thread)
    for request in fake_llm.streamed():
        calls = [c["id"] for m in request.body["messages"] for c in m.get("tool_calls") or []]
        answered = [m["tool_call_id"] for m in request.body["messages"] if m["role"] == "tool"]
        assert calls == answered


def test_a_reply_with_tool_calls_returns_its_reasoning_to_the_model(fake_llm, user, thread):
    query = {"summary": "Counting", "dataset": str(thread.dataset_id), "sql": "select 1"}
    fake_llm.stream_rounds(
        [([tool_call("query_dataset", query)], ""), ([], "Done.")], reasoning="why"
    )
    say(thread, user, "Count.")
    replayed = [m for m in fake_llm.streamed()[1].body["messages"] if m["role"] == "assistant"]
    assert replayed[0]["reasoning_details"]


def test_distinct_reasoning_records_without_an_index_stay_apart(fake_llm):
    records = [
        {"type": "reasoning.encrypted", "id": "first", "data": "a"},
        {"type": "reasoning.encrypted", "id": "second", "data": "b"},
    ]
    fake_llm.on(
        lambda r: bool(r.body.get("stream")),
        lambda _r: {"content": "Done.", "usage": {"cost": 0.01}, "reasoning_details": records},
    )
    _text, _calls, stats = stream_llm_tools([{"role": "user", "content": "Go."}], [])
    assert stats["reasoning_details"] == records


def test_a_streamed_reply_reports_progress_where_the_database_is_usable(fake_llm, thread):
    fake_llm.stream_rounds([([], "Done.")])
    seen = []
    text, _calls, _stats = stream_llm_tools(
        [{"role": "user", "content": "Go."}],
        [],
        on_progress=lambda *_: seen.append(AssistantThread.objects.filter(pk=thread.pk).exists()),
    )
    assert text == "Done." and seen and all(seen)


def test_the_log_stores_what_a_kernel_prints_that_jsonb_refuses(thread):
    event = log.append(thread, "tool_result", {"content": {"output": "a\x00b", "n": float("nan")}})
    event.refresh_from_db()
    assert event.payload == {"content": {"output": "a�b", "n": None}}


def test_deleting_a_dataset_deletes_its_copies_in_the_workspace(
    user, thread, monkeypatch, django_capture_on_commit_callbacks
):
    forgotten = []
    monkeypatch.setattr(
        assistant_tasks.forget_workspace_data, "delay", lambda *a: forgotten.append(a)
    )
    source = thread.dataset.source
    with django_capture_on_commit_callbacks(execute=True):
        lifecycle.delete_dataset(thread.dataset)
    assert forgotten == [(str(thread.project_id), [f"{source.pk}.parquet"], [thread.pk.hex])]
