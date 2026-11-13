from datetime import timedelta

import pytest
from conftest import frozen_dataset
from django.utils import timezone
from factories import make_project, make_user
from fakes.llm import tool_call
from mcp_fixtures import call_tool

from overbae.core.errors import InputValidationError
from overbae.core.llms import stream_llm_tools
from overbae.models import (
    AssistantThread,
    BillingTelemetry,
    DataExploration,
    Dataset,
    DeployedModel,
    InferenceRequest,
    ProjectMembership,
)
from overbae.services import sandbox_sessions
from overbae.services.assistant import log, loop, sessions, threads
from overbae.services.mcp.context import MCPContext, PlatformCredential
from overbae.tasks import data_exploration
from overbae.tasks.inference_requests import inspect_inference_request

pytestmark = [pytest.mark.django_db, pytest.mark.covers("assistant-loop")]


@pytest.fixture
def user():
    return make_user()


@pytest.fixture
def thread(user):
    project = make_project(member=user)
    return threads.open_thread(project, "dataset", frozen_dataset(project, user=user).pk)


def drive(thread):
    while loop.advance(thread.pk):
        pass
    thread.refresh_from_db()


def kinds(thread):
    return [event.kind for event in thread.events.all()]


def results(thread):
    return [event.payload for event in thread.events.filter(kind="tool_result")]


def explore(thread):
    cell = threads.subject(thread).active_cell
    return tool_call("explore_dataset", {"source_cell": str(cell.pk), "name": "Profile"})


def test_a_message_runs_code_in_the_kernel_and_every_reply_is_charged(fake_llm, user, thread):
    cell = threads.subject(thread).active_cell
    count = f"import pandas\nlen(pandas.read_parquet('../inputs/{cell.pk}.parquet'))"
    fake_llm.stream_rounds(
        [
            ([tool_call("stage_dataset", {"cell": str(cell.pk)})], ""),
            ([tool_call("run_python", {"code": count})], "Counting the rows."),
            ([], "The dataset has 2 rows."),
        ]
    )
    threads.send(thread, user, request_key="first", text="How many rows are there?")
    try:
        drive(thread)
    finally:
        sandbox_sessions.provider().stop(thread.session)

    assert kinds(thread) == [
        "user",
        "assistant",
        "tool_result",
        "assistant",
        "tool_result",
        "assistant",
    ]
    counted = results(thread)[1]
    assert counted["ok"] and counted["content"]["output"] == "2\n"
    assert thread.state == AssistantThread.State.IDLE
    charged = BillingTelemetry.objects.filter(idempotency_key__startswith=f"assistant:{thread.pk}")
    assert charged.count() == 3 and {row.user_id for row in charged} == {user.pk}


def test_a_message_for_a_dataset_still_landing_waits_for_its_source(fake_llm, user, thread):
    fake_llm.stream_rounds([([], "It has landed.")])
    landing = Dataset.objects.filter(pk=thread.subject_id)
    landing.update(state=Dataset.State.LANDING)
    threads.send(thread, user, request_key="first", text="Prepare this.")
    drive(thread)
    assert kinds(thread) == [] and loop.due() == [str(thread.pk)]

    landing.update(state=Dataset.State.IDLE)
    drive(thread)
    assert kinds(thread) == ["user", "assistant"]


def test_a_thread_waits_for_a_job_and_resumes_with_its_state(fake_llm, user, thread, monkeypatch):
    monkeypatch.setattr(data_exploration.run, "delay", lambda *args, **kwargs: None)
    fake_llm.stream_rounds([([explore(thread)], ""), ([], "The profile is complete.")])
    threads.send(thread, user, request_key="first", text="Profile the data.")
    drive(thread)
    assert thread.state == AssistantThread.State.WAITING
    assert loop.due() == [] and kinds(thread) == ["user", "assistant"]

    DataExploration.objects.update(state="completed")
    assert loop.due() == [str(thread.pk)]
    drive(thread)

    assert results(thread)[0]["content"]["now"][0]["status"] == "completed"
    assert kinds(thread)[-1] == "assistant" and thread.state == AssistantThread.State.IDLE


def test_an_urgent_message_ends_a_wait_while_the_job_still_runs(
    fake_llm, user, thread, monkeypatch
):
    monkeypatch.setattr(data_exploration.run, "delay", lambda *args, **kwargs: None)
    fake_llm.stream_rounds([([explore(thread)], ""), ([], "It still runs.")])
    threads.send(thread, user, request_key="first", text="Profile the data.")
    drive(thread)
    later = threads.send(thread, user, request_key="later", text="Is it done?")
    assert loop.due() == []

    threads.change(later.pk, thread, revision=1, action="send_now")
    assert loop.due() == [str(thread.pk)]
    drive(thread)

    assert results(thread)[0]["content"]["now"][0]["status"] != "completed"
    assert kinds(thread) == ["user", "assistant", "tool_result", "user", "assistant"]


def test_stop_ends_a_wait_answers_its_call_and_drops_the_queue(fake_llm, user, thread, monkeypatch):
    monkeypatch.setattr(data_exploration.run, "delay", lambda *args, **kwargs: None)
    fake_llm.stream_rounds([([explore(thread)], ""), ([], "Understood.")])
    threads.send(thread, user, request_key="first", text="Profile the data.")
    drive(thread)
    queued = threads.send(thread, user, request_key="second", text="Then split it.")

    loop.stop(thread)
    thread.refresh_from_db()
    queued.refresh_from_db()
    assert thread.state == AssistantThread.State.IDLE and queued.state == "removed"
    assert thread.events.last().payload == {
        "state": "stopped",
        "detail": "Stopped. Started jobs that continue: 1.",
    }
    assert kinds(thread)[-2:] == ["tool_result", "status"] and loop.due() == []


def test_a_step_that_lost_its_lease_is_interrupted_and_can_write_nothing(fake_llm, user, thread):
    threads.send(thread, user, request_key="first", text="Prepare this dataset.")
    assert loop.advance(thread.pk)
    thread.refresh_from_db()
    stuck = thread.claim
    AssistantThread.objects.filter(pk=thread.pk).update(
        state=AssistantThread.State.RUNNING, updated_at=timezone.now() - timedelta(hours=1)
    )

    assert loop.due() == [str(thread.pk)]
    drive(thread)
    assert thread.state == AssistantThread.State.IDLE
    assert thread.events.last().payload["state"] == "interrupted"
    assert fake_llm.streamed() == []
    with pytest.raises(log.LostClaimError):
        log.append(thread, "assistant", {"text": "late"}, claim=stuck)
    assert kinds(thread) == ["user", "status"]


def test_a_sender_who_left_the_project_runs_nothing_more(fake_llm, user, thread):
    threads.send(thread, user, request_key="first", text="List the datasets.")
    assert loop.advance(thread.pk)
    ProjectMembership.objects.filter(user=user, project=thread.project).delete()
    drive(thread)

    assert fake_llm.streamed() == []
    assert thread.events.last().payload["detail"].endswith("is not a project member.")


def test_a_queued_message_waits_for_the_run_and_send_now_takes_the_next_boundary(
    fake_llm, user, thread
):
    fake_llm.stream_rounds(
        [([tool_call("list_datasets", {})], ""), ([], "Listed."), ([], "Both are done.")]
    )
    threads.send(thread, user, request_key="first", text="List the datasets.")
    assert loop.advance(thread.pk) and loop.advance(thread.pk)
    threads.send(thread, user, request_key="later", text="Later.")
    urgent = threads.send(thread, user, request_key="urgent", text="Now.")
    threads.change(urgent.pk, thread, revision=1, action="send_now")
    drive(thread)

    said = [event.payload["text"] for event in thread.events.filter(kind="user")]
    assert said == ["List the datasets.", "Now.", "Later."]
    second_request = fake_llm.streamed()[1].body["messages"]
    assert [message["role"] for message in second_request[-2:]] == ["tool", "user"]


def test_a_request_key_identifies_one_message_and_a_stale_revision_changes_nothing(user, thread):
    first = threads.send(thread, user, request_key="k", text="One.")
    assert threads.send(thread, user, request_key="k", text="One.").pk == first.pk
    with pytest.raises(InputValidationError):
        threads.send(thread, user, request_key="k", text="Two.")

    edited = threads.change(first.pk, thread, revision=1, action="edit", text="One, edited.")
    assert (edited.text, edited.revision) == ("One, edited.", 2)
    with pytest.raises(InputValidationError):
        threads.change(first.pk, thread, revision=1, action="remove")
    assert thread.inputs.get().state == "queued"


def test_a_long_run_is_summarized_and_no_tool_result_loses_its_call(
    fake_llm, user, thread, settings
):
    settings.ASSISTANT_COMPACT_TOKENS = 0
    listing = [tool_call("list_datasets", {})]
    fake_llm.stream_rounds([*[(listing, "")] * 8, ([], "All listed.")])
    fake_llm.on(lambda request: not request.body.get("stream"), "Listed the datasets 6 times.")
    threads.send(thread, user, request_key="first", text="List the datasets eight times.")
    drive(thread)

    summary = thread.events.filter(kind="summary").first()
    assert summary.payload["text"] == "Listed the datasets 6 times."
    assert (
        kinds(thread)[-1] == "assistant" and thread.events.last().payload["text"] == "All listed."
    )
    after = next(
        request.body["messages"]
        for request in fake_llm.streamed()
        if "Summary of the earlier conversation" in str(request.body["messages"][1]["content"])
    )
    assert "Listed the datasets 6 times." in after[1]["content"]
    called = {call["id"] for message in after for call in message.get("tool_calls", [])}
    answered = [message["tool_call_id"] for message in after if message["role"] == "tool"]
    assert answered and set(answered) <= called
    assert not any("eight times" in str(message.get("content")) for message in after)


def test_a_streamed_reply_reports_progress_where_the_database_is_usable(fake_llm, thread):
    fake_llm.stream_rounds([([], "Done.")])
    seen = []

    text, _calls, _stats = stream_llm_tools(
        [{"role": "user", "content": "Go."}],
        [],
        on_progress=lambda *_: seen.append(AssistantThread.objects.filter(pk=thread.pk).exists()),
    )

    assert text == "Done." and seen and all(seen)


def test_a_reply_with_tool_calls_returns_its_reasoning_to_the_model(fake_llm, user, thread):
    listing = [tool_call("list_datasets", {})]
    fake_llm.stream_rounds([(listing, ""), ([], "Listed.")], reasoning="Look first.")
    threads.send(thread, user, request_key="first", text="List the datasets.")
    drive(thread)

    reply = next(m for m in fake_llm.streamed()[1].body["messages"] if m["role"] == "assistant")
    assert reply["reasoning_details"] == [{"type": "reasoning.text", "text": "Look first."}]


def test_a_project_at_its_session_limit_takes_the_idle_session_and_tells_its_thread(
    fake_llm, user, thread, settings
):
    settings.ASSISTANT_PROJECT_SESSIONS = 1
    other = threads.open_thread(
        thread.project, "dataset", frozen_dataset(thread.project, user=user).pk
    )
    run = [([tool_call("run_python", {"code": "kept = 1"})], ""), ([], "Done.")]
    fake_llm.stream_rounds([*run, *run, *run])
    provider = sandbox_sessions.provider()
    try:
        threads.send(thread, user, request_key="one", text="Run it.")
        drive(thread)
        first = thread.session
        threads.send(other, user, request_key="two", text="Run it.")
        drive(other)
        thread.refresh_from_db()
        assert thread.session == "" and other.session
        with pytest.raises(sandbox_sessions.SessionGoneError):
            provider.execute(first, "kept", execution_id="after", timeout=5)

        threads.send(thread, user, request_key="three", text="Run it again.")
        drive(thread)
        ran, lost = results(thread)
        assert ran["ok"] and lost["content"]["error"].startswith(sessions.EVICTED)
    finally:
        for current in (thread, other):
            current.refresh_from_db()
            if current.session:
                provider.stop(current.session)


def test_one_approval_starts_one_paid_call_and_a_stopped_wait_spends_it(
    fake_llm, user, thread, monkeypatch
):
    monkeypatch.setattr(inspect_inference_request, "apply_async", lambda *args, **kwargs: None)
    monkeypatch.setattr(inspect_inference_request, "delay", lambda *args, **kwargs: None)
    model = DeployedModel.objects.create(project=thread.project, model_id="served", status="ready")
    paid = tool_call(
        "run_inference",
        {
            "deployment": str(model.pk),
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 32,
        },
    )
    fake_llm.stream_rounds([([paid], ""), ([], "It needs approval."), ([paid], "")])
    threads.send(thread, user, request_key="ask", text="Run it.")
    drive(thread)

    (refused,) = results(thread)
    asked = thread.events.get(kind="tool_result").presentation["root"]["children"][1]
    assert not refused["ok"] and refused["asks"] and asked["id"] == "approve"
    assert not InferenceRequest.objects.exists()

    answer = {"call": refused["call"], "id": "approve", "values": {"choice": "approve"}}
    threads.send(thread, user, request_key="yes", text="Approve", interaction=answer)
    drive(thread)
    assert thread.state == AssistantThread.State.WAITING and InferenceRequest.objects.count() == 1

    loop.stop(thread)
    fake_llm.stream_rounds([([paid], ""), ([], "It needs approval again.")])
    threads.send(thread, user, request_key="again", text="Run it again.")
    drive(thread)
    started, repeated = results(thread)[1:]
    assert started["approval"] == refused["call"]
    assert not repeated["ok"] and repeated["asks"] == refused["asks"]
    assert InferenceRequest.objects.count() == 1


def test_a_handoff_waits_for_the_coding_agents_report_over_mcp(fake_llm, user, thread):
    handed = [tool_call("hand_off", {"instructions": "Run the optimiser loop for run 7."})]
    fake_llm.stream_rounds([(handed, ""), ([], "The loop ran.")])
    threads.send(thread, user, request_key="first", text="Optimise the agent.")
    drive(thread)
    assert thread.state == AssistantThread.State.WAITING and loop.due() == []

    agent = MCPContext(
        user=user, token=PlatformCredential(thread.project_id), project=thread.project
    )
    (listed,) = call_tool("list_assistant_handoffs", {}, agent).structuredContent["handoffs"]
    assert listed["instructions"] == "Run the optimiser loop for run 7."
    report = {"handoff": listed["id"], "result": "Score rose from 0.61 to 0.74."}
    call_tool("complete_assistant_handoff", report, agent)

    assert loop.due() == [str(thread.pk)]
    drive(thread)
    assert results(thread)[0]["content"]["now"] == [
        {"kind": "handoff", "state": "done", "result": "Score rose from 0.61 to 0.74."}
    ]
