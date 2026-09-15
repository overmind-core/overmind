from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from overbae.services.behaviour import ledger
from overbae.services.behaviour.ledger import TurnTransition, TurnTransitions
from overbae.services.behaviour.session_score import compose_session_score


def _event(event_type, ask_id, *, kind="", text="", execution_id=None):
    return SimpleNamespace(
        event_type=event_type,
        ask_id=ask_id,
        ask_text=text,
        ask_kind=kind,
        execution_id=execution_id,
    )


def _row(pk, *, score=None, passed=None):  # noqa: ARG001 — passed kept for call-site clarity
    return SimpleNamespace(pk=pk, success_score=score)


def test_empty_conversation_scores_none():
    assert compose_session_score([], []) == (None, "")


def test_open_produce_ask_zeroes_session_despite_latest_pass():
    rows = [_row(1, score=1.0, passed=True), _row(2, score=1.0, passed=True)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="create the report", execution_id=1),
        _event("ask_reprompted", "a1", execution_id=2),
    ]
    score, rationale = compose_session_score(rows, events)
    assert score == 0.0
    assert "still not delivered" in rationale


def test_delivered_wrong_without_subsequent_delivery_zeroes():
    rows = [_row(1, score=0.9, passed=True), _row(2, score=1.0, passed=True)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="create eval dataset", execution_id=1),
        _event("delivered_wrong", "a1", execution_id=2),
    ]
    score, rationale = compose_session_score(rows, events)
    assert score == 0.0
    assert "wrong kind" in rationale


def test_delivered_produce_lands_and_later_local_misses_stay_turn_local():
    rows = [_row(1, score=0.9, passed=True), _row(2, score=0.2, passed=False)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="create the job", execution_id=1),
        _event("delivered", "a1", execution_id=1),
        _event("ask_opened", "a2", kind="inspect", text="show me the logs", execution_id=2),
    ]
    score, rationale = compose_session_score(rows, events)
    assert score == 0.9
    assert rationale == "task landed; later local misses are turn-local"


def test_refusal_closes_ask_and_latest_verdict_carries():
    rows = [_row(1, score=1.0, passed=True), _row(2, score=0.8, passed=True)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="order me lunch", execution_id=1),
        _event("refused", "a1", execution_id=1),
        _event("ask_opened", "a2", kind="inspect", text="list my leave days", execution_id=2),
    ]
    score, rationale = compose_session_score(rows, events)
    assert score == 0.8
    assert "latest composed execution score" in rationale


def test_open_inspect_ask_uses_latest_verdict():
    rows = [_row(1, score=0.4, passed=True)]
    events = [_event("ask_opened", "a1", kind="inspect", text="compare runs", execution_id=1)]
    assert compose_session_score(rows, events)[0] == 0.4


def test_delivered_wrong_then_delivery_clears():
    rows = [_row(1, score=0.0, passed=False), _row(2, score=1.0, passed=True)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="create eval dataset", execution_id=1),
        _event("delivered_wrong", "a1", execution_id=1),
        _event("ask_reprompted", "a1", execution_id=2),
        _event("delivered", "a1", execution_id=2),
    ]
    score, rationale = compose_session_score(rows, events)
    assert score == 1.0
    assert rationale == "task landed; later local misses are turn-local"


def test_superseded_produce_does_not_zero():
    rows = [_row(1, score=None, passed=None), _row(2, score=0.7, passed=True)]
    events = [
        _event("ask_opened", "a1", kind="produce", text="build a report", execution_id=1),
        _event("ask_superseded", "a1", execution_id=2),
        _event("ask_opened", "a2", kind="inspect", text="who sits on floor 4", execution_id=2),
    ]
    assert compose_session_score(rows, events)[0] == 0.7


def test_rows_without_verdicts_score_none():
    rows = [_row(1), _row(2)]
    events = [_event("ask_opened", "a1", kind="inspect", text="hello", execution_id=1)]
    assert compose_session_score(rows, events) == (None, "")


pytestmark = pytest.mark.django_db


def _classified(*batches):
    return [SimpleNamespace(parsed=TurnTransitions(transitions=list(batch))) for batch in batches]


def test_refresh_session_score_stamps_rows_from_ledger():
    from datetime import UTC, datetime, timedelta

    from overbae.models import ConversationEvent, Project, TaskExecution
    from overbae.services.behaviour.session_score import refresh_session_score

    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    cid = str(uuid.uuid4())
    now = datetime.now(tz=UTC)
    rows = []
    for i, (ask, score) in enumerate([("create the finetune job", 0.0), ("yes, go ahead", 1.0)]):
        rows.append(
            TaskExecution.objects.create(
                project=project,
                trace_id=uuid.uuid4().hex,
                unit_span_id=uuid.uuid4().hex[:16],
                conversation_id=cid,
                started_at=now + timedelta(minutes=i),
                user_intent={"text": ask},
                success_score=score,
                step_results=[
                    {
                        "role": "outcome",
                        "outcome": "scored",
                        "score": score,
                        "passed": score > 0,
                        "delivery": "delivered" if score > 0 else "",
                    }
                ],
            )
        )

    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        side_effect=_classified(
            [
                TurnTransition(
                    event="ask_opened", ask_text="create the finetune job", ask_kind="produce"
                )
            ],
            [TurnTransition(event="delivered", ask_id="a1")],
        ),
    ):
        refresh_session_score(rows[-1])

    for row in rows:
        row.refresh_from_db()
    assert ConversationEvent.objects.filter(conversation_id=cid).exists()
    assert all(
        e.source == ConversationEvent.Source.BACKFILL
        for e in ConversationEvent.objects.filter(conversation_id=cid)
    )
    assert rows[0].session_score == rows[1].session_score == 1.0
    assert rows[0].session_rationale == "task landed; later local misses are turn-local"
    state = rows[0].observed_route["task_state"]
    assert state["status"] == "delivered"
    assert state["running_intent"] == "create the finetune job"
    before = list(
        ConversationEvent.objects.filter(conversation_id=cid).values_list("pk", flat=True)
    )
    with mock.patch.object(
        ledger.judging, "invoke_judge", side_effect=AssertionError("must not re-run")
    ):
        refresh_session_score(rows[-1])
    after = list(ConversationEvent.objects.filter(conversation_id=cid).values_list("pk", flat=True))
    assert before == after


def test_refresh_session_score_open_produce_zeroes_all_rows():
    from datetime import UTC, datetime, timedelta

    from overbae.models import Project, TaskExecution
    from overbae.services.behaviour.session_score import refresh_session_score

    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    cid = str(uuid.uuid4())
    now = datetime.now(tz=UTC)
    rows = [
        TaskExecution.objects.create(
            project=project,
            trace_id=uuid.uuid4().hex,
            unit_span_id=uuid.uuid4().hex[:16],
            conversation_id=cid,
            started_at=now + timedelta(minutes=i),
            user_intent={"text": ask},
            success_score=1.0,
            step_results=[{"role": "outcome", "outcome": "scored", "score": 1.0, "passed": True}],
        )
        for i, ask in enumerate(["create a big sales report", "please create the sales report now"])
    ]
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        side_effect=_classified(
            [
                TurnTransition(
                    event="ask_opened", ask_text="create a big sales report", ask_kind="produce"
                )
            ],
            [TurnTransition(event="ask_reprompted", ask_id="a1")],
        ),
    ):
        refresh_session_score(rows[-1])
    for row in rows:
        row.refresh_from_db()
    assert rows[0].session_score == rows[1].session_score == 0.0
    state = rows[1].observed_route["task_state"]
    assert state["status"] == "outstanding"
    assert state["outstanding_asks"] == ["create a big sales report"]
