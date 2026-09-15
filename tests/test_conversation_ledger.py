from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from overbae.services.behaviour import ledger
from overbae.services.behaviour.ledger import (
    LedgerState,
    TurnTransition,
    TurnTransitions,
    _validated_transitions,
    fold,
    task_state_dict,
)


def _event(event_type, ask_id, *, text="", kind="", execution_id=None):
    return SimpleNamespace(
        event_type=event_type,
        ask_id=ask_id,
        ask_text=text,
        ask_kind=kind,
        rationale="",
        execution_id=execution_id,
    )


def _row(*, observed_route=None, user_intent=None):
    return SimpleNamespace(observed_route=observed_route or {}, user_intent=user_intent or {})


def test_ledger_opt_out_excludes_scaffold_intent_only():
    """Only a structurally-detected transcript dump (``scaffold``) is barred
    from the ledger. A plain first user message stays classifiable — for most
    agents it IS the ask."""
    assert ledger._ledger_opt_out(_row(user_intent={"text": "x", "source": "scaffold"}))
    assert not ledger._ledger_opt_out(_row(user_intent={"text": "x", "source": "first_message"}))
    assert not ledger._ledger_opt_out(_row(user_intent={"text": "x", "source": "declared"}))
    assert ledger._ledger_opt_out(_row(observed_route={"ledger_turn": False}))
    assert not ledger._ledger_opt_out(_row())


def test_fold_open_deliver_closes_ask():
    events = [
        _event("ask_opened", "a1", text="create a dataset", kind="produce"),
        _event("delivered", "a1"),
    ]
    state = fold(events)
    assert state.asks["a1"].status == "delivered"
    assert not state.outstanding_produce()
    assert task_state_dict(state)["status"] == "delivered"
    assert task_state_dict(fold(events)) == task_state_dict(state)


def test_fold_delivered_wrong_stays_outstanding_until_delivery():
    events = [
        _event("ask_opened", "a1", text="create an eval dataset", kind="produce"),
        _event("delivered_wrong", "a1"),
    ]
    state = fold(events)
    assert state.asks["a1"].delivered_wrong
    assert [a.ask_id for a in state.outstanding_produce()] == ["a1"]
    d = task_state_dict(state)
    assert d["status"] == "outstanding" and d["delivered_wrong"]
    assert "wrong kind" in d["reason"]

    state = fold([*events, _event("delivered", "a1")])
    assert not state.outstanding_produce()
    assert not state.asks["a1"].delivered_wrong


def test_fold_refusal_closes_and_reprompt_reopens():
    events = [
        _event("ask_opened", "a1", text="order me lunch", kind="produce"),
        _event("refused", "a1"),
    ]
    state = fold(events)
    assert state.asks["a1"].status == "refused"
    assert not state.outstanding_produce()

    state = fold([*events, _event("ask_reprompted", "a1")])
    assert state.asks["a1"].status == "open"
    assert state.asks["a1"].reprompts == 1


def test_fold_supersession_closes_but_keeps_delivered_wrong_sticky():
    state = fold(
        [
            _event("ask_opened", "a1", text="build a report", kind="produce"),
            _event("ask_superseded", "a1"),
            _event("ask_opened", "a2", text="who sits on floor 4", kind="inspect"),
        ]
    )
    assert state.asks["a1"].status == "superseded"
    assert not state.outstanding_produce()
    assert state.running_intent() == "who sits on floor 4"

    # delivered_wrong without subsequent delivery zeroes even when superseded.
    state = fold(
        [
            _event("ask_opened", "a1", text="build a report", kind="produce"),
            _event("delivered_wrong", "a1"),
            _event("ask_superseded", "a1"),
            _event("ask_opened", "a2", text="list datasets", kind="inspect"),
        ]
    )
    assert [a.ask_id for a in state.outstanding_produce()] == ["a1"]


def test_fold_open_inspect_ask_does_not_zero_session():
    state = fold([_event("ask_opened", "a1", text="list the datasets", kind="inspect")])
    assert not state.outstanding_produce()
    assert task_state_dict(state)["status"] == "delivered"


def test_fold_park_counts():
    state = fold(
        [
            _event("ask_opened", "a1", text="create the job", kind="produce"),
            _event("parked", "a1"),
            _event("parked", "a1"),
        ]
    )
    assert state.asks["a1"].parks == 2
    assert state.asks["a1"].is_open


def _state_with_open(text="create a training dataset", kind="produce") -> LedgerState:
    return fold([_event("ask_opened", "a1", text=text, kind=kind)])


def test_validation_assigns_ask_ids_and_supersession_chains():
    parsed = TurnTransitions(
        transitions=[
            TurnTransition(event="ask_superseded", ask_id="a1"),
            TurnTransition(event="ask_opened", ask_text="who sits on floor 4", ask_kind="inspect"),
        ]
    )
    out = _validated_transitions(parsed, _state_with_open())
    assert [t["event_type"] for t in out] == ["ask_superseded", "ask_opened"]
    assert out[1]["ask_id"] == "a2"


def test_validation_chains_same_turn_open_and_resolve():
    """Open-and-refuse in one turn: a placeholder id or an empty reference must
    chain to the ask assigned this turn, not raise."""
    out = _validated_transitions(
        TurnTransitions(
            transitions=[
                TurnTransition(
                    event="ask_opened", ask_id="new", ask_text="order lunch", ask_kind="instruct"
                ),
                TurnTransition(event="refused", ask_id="new"),
            ]
        ),
        LedgerState(),
    )
    assert [(t["event_type"], t["ask_id"]) for t in out] == [
        ("ask_opened", "a1"),
        ("refused", "a1"),
    ]

    out = _validated_transitions(
        TurnTransitions(
            transitions=[
                TurnTransition(event="ask_opened", ask_text="order lunch", ask_kind="instruct"),
                TurnTransition(event="refused"),
            ]
        ),
        LedgerState(),
    )
    assert [(t["event_type"], t["ask_id"]) for t in out] == [
        ("ask_opened", "a1"),
        ("refused", "a1"),
    ]

    # Placeholder invented only on the follow-up (open used an empty id).
    out = _validated_transitions(
        TurnTransitions(
            transitions=[
                TurnTransition(event="ask_opened", ask_text="make a report", ask_kind="produce"),
                TurnTransition(event="delivered_wrong", ask_id="new"),
            ]
        ),
        LedgerState(),
    )
    assert [(t["event_type"], t["ask_id"]) for t in out] == [
        ("ask_opened", "a1"),
        ("delivered_wrong", "a1"),
    ]


def test_validation_rejects_unknown_event_kind_and_ask():
    state = _state_with_open()
    with pytest.raises(ValueError):
        _validated_transitions(
            TurnTransitions(transitions=[TurnTransition(event="delivered_maybe", ask_id="a1")]),
            state,
        )
    with pytest.raises(ValueError):
        _validated_transitions(
            TurnTransitions(
                transitions=[TurnTransition(event="ask_opened", ask_text="x", ask_kind="wish")]
            ),
            state,
        )
    with pytest.raises(ValueError):
        _validated_transitions(
            TurnTransitions(transitions=[TurnTransition(event="delivered", ask_id="a9")]), state
        )


def _mock_outcome(transitions):
    return SimpleNamespace(parsed=TurnTransitions(transitions=transitions))


def test_classifier_confirmation_is_not_a_new_ask():
    """A confirmation turn's transitions reference the OPEN ask; delivered_wrong lands on it."""
    state = _state_with_open(text="create an Eval dataset for the Train ask")
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=_mock_outcome(
            [TurnTransition(event="delivered_wrong", ask_id="a1", rationale="wrong kind")]
        ),
    ) as invoked:
        out = ledger.classify_turn(
            state, [], user_message="Yes — create with source-alpha", result_summary="kind=train"
        )
    assert invoked.call_count == 1
    assert out == [
        {
            "event_type": "delivered_wrong",
            "ask_id": "a1",
            "ask_text": "",
            "ask_kind": "",
            "rationale": "wrong kind",
        }
    ]
    prompt = invoked.call_args.args[0]
    assert "Yes — create with source-alpha" in prompt
    assert "bare confirmation" in prompt.lower() or "confirmation" in prompt


def test_classifier_prompt_carries_rubric_and_ledger():
    state = _state_with_open()
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=_mock_outcome([TurnTransition(event="refused", ask_id="a1")]),
    ) as invoked:
        ledger.classify_turn(state, [], user_message="order me lunch", result_summary="")
    prompt = invoked.call_args.args[0]
    assert "supersedes the open ask" in prompt
    assert "closes the ask" in prompt
    assert "a1 [produce] status=open" in prompt


def test_classifier_parse_failure_raises():
    with (
        mock.patch.object(
            ledger.judging, "invoke_judge", return_value=SimpleNamespace(parsed=None)
        ),
        pytest.raises(ValueError),
    ):
        ledger.classify_turn(LedgerState(), [], user_message="hi", result_summary="")


pytestmark = pytest.mark.django_db


@pytest.fixture
def project():
    from overbae.models import Project

    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _execution(project, cid, ask, *, minutes=0, step_results=None):
    from datetime import UTC, datetime, timedelta

    from overbae.models import TaskExecution

    return TaskExecution.objects.create(
        project=project,
        trace_id=uuid.uuid4().hex,
        unit_span_id=uuid.uuid4().hex[:16],
        conversation_id=cid,
        started_at=datetime.now(tz=UTC) + timedelta(minutes=minutes),
        user_intent={"text": ask},
        step_results=step_results or [],
    )


def test_record_turn_classifier_failure_leaves_turn_unrecorded(project):
    """No lexical fallback: a classifier failure logs, records nothing, and
    the next pass retries the same turn."""
    from overbae.models import ConversationEvent

    cid = str(uuid.uuid4())
    row = _execution(project, cid, "create a finetune job")
    with mock.patch.object(ledger.judging, "invoke_judge", side_effect=RuntimeError("down")):
        entering, events = ledger.record_turn(
            row, user_message="create a finetune job", result_summary=""
        )
    assert entering.asks == {}
    assert events == []
    assert ConversationEvent.objects.filter(conversation_id=cid).count() == 0

    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=_mock_outcome(
            [
                TurnTransition(
                    event="ask_opened", ask_text="create a finetune job", ask_kind="produce"
                )
            ]
        ),
    ):
        _, retried = ledger.record_turn(
            row, user_message="create a finetune job", result_summary=""
        )
    assert [e.event_type for e in retried] == ["ask_opened"]
    assert retried[0].source == ConversationEvent.Source.CLASSIFIER


def test_recorded_transitions_are_never_rerun(project):
    cid = str(uuid.uuid4())
    row = _execution(project, cid, "create a finetune job")
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=_mock_outcome(
            [
                TurnTransition(
                    event="ask_opened", ask_text="create a finetune job", ask_kind="produce"
                )
            ]
        ),
    ):
        _, first = ledger.record_turn(row, user_message="create a finetune job", result_summary="")
    assert [e.event_type for e in first] == ["ask_opened"]

    with mock.patch.object(
        ledger.judging, "invoke_judge", side_effect=AssertionError("must not re-run")
    ):
        _, second = ledger.record_turn(row, user_message="create a finetune job", result_summary="")
    assert [e.pk for e in second] == [e.pk for e in first]


def test_record_turn_backfills_prior_turns_through_classifier(project):
    """Unrecorded prior turns route through the SAME classifier, tagged
    source=backfill, with the persisted outcome verdict as evidence."""
    from overbae.models import ConversationEvent

    cid = str(uuid.uuid4())
    prior = _execution(
        project,
        cid,
        "create a finetune job",
        minutes=-5,
        step_results=[
            {"role": "outcome", "outcome": "scored", "delivery": "delivered", "score": 1.0}
        ],
    )
    current = _execution(project, cid, "yes, go ahead")

    outcomes = [
        _mock_outcome(
            [
                TurnTransition(
                    event="ask_opened", ask_text="create a finetune job", ask_kind="produce"
                ),
                TurnTransition(event="delivered"),
            ]
        ),
        _mock_outcome([TurnTransition(event="ask_reprompted", ask_id="a1")]),
    ]
    with mock.patch.object(ledger.judging, "invoke_judge", side_effect=outcomes) as invoked:
        entering, turn_events = ledger.record_turn(
            current, user_message="yes, go ahead", result_summary="job started"
        )
    assert invoked.call_count == 2
    assert "delivery=delivered" in invoked.call_args_list[0].args[0]
    assert "a1" in entering.asks
    assert entering.asks["a1"].status == "delivered"
    prior_events = list(prior.ledger_events.all())
    assert [e.event_type for e in prior_events] == ["ask_opened", "delivered"]
    assert {e.source for e in prior_events} == {ConversationEvent.Source.BACKFILL}
    assert [e.source for e in turn_events] == [ConversationEvent.Source.CLASSIFIER]


def test_record_turn_skips_empty_prior_rows_without_llm_calls(project):
    """A wiped/unscored prior row (no ask text, no outcome) is nothing to
    classify — no LLM call, no rows, retried when it has evidence."""
    cid = str(uuid.uuid4())
    empty_prior = _execution(project, cid, "", minutes=-5)
    current = _execution(project, cid, "list datasets")
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=_mock_outcome(
            [TurnTransition(event="ask_opened", ask_text="list datasets", ask_kind="inspect")]
        ),
    ) as invoked:
        _, turn_events = ledger.record_turn(
            current, user_message="list datasets", result_summary="table"
        )
    assert invoked.call_count == 1  # current turn only
    assert empty_prior.ledger_events.count() == 0
    assert [e.event_type for e in turn_events] == ["ask_opened"]


def test_ensure_backfill_classifies_once_and_skips_failures(project):
    from overbae.models import ConversationEvent

    cid = str(uuid.uuid4())
    rows = [
        _execution(project, cid, "create a report", minutes=-10),
        _execution(project, cid, "yes go ahead", minutes=-5),
    ]
    outcomes = [
        _mock_outcome(
            [TurnTransition(event="ask_opened", ask_text="create a report", ask_kind="produce")]
        ),
        RuntimeError("down"),
    ]
    with mock.patch.object(ledger.judging, "invoke_judge", side_effect=outcomes):
        events = ledger.ensure_backfill(rows)
    assert [e.event_type for e in events] == ["ask_opened"]
    assert events[0].source == ConversationEvent.Source.BACKFILL
    assert rows[1].ledger_events.count() == 0  # failed turn skipped, not guessed

    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        side_effect=[_mock_outcome([TurnTransition(event="delivered", ask_id="a1")])],
    ) as invoked:
        events = ledger.ensure_backfill(rows)
    assert invoked.call_count == 1  # only the previously failed row
    assert [e.event_type for e in events] == ["ask_opened", "delivered"]


def test_outcome_ledger_verdict_park_counting(project):
    from overbae.models import ConversationEvent

    cid = str(uuid.uuid4())
    first = _execution(project, cid, "create the job", minutes=-5)
    later = _execution(project, cid, "create the job now")

    def _persist(execution, event_type, order):
        ConversationEvent.objects.create(
            project=project,
            conversation_id=cid,
            execution=execution,
            event_type=event_type,
            ask_id="a1",
            order=order,
        )

    _persist(first, "ask_opened", 0)
    _persist(first, "parked", 1)
    assert ledger.outcome_ledger_verdict(first) == {"delivery": "first_park", "gate_note": ""}

    _persist(later, "ask_reprompted", 2)
    _persist(later, "parked", 3)
    verdict = ledger.outcome_ledger_verdict(later)
    assert verdict["delivery"] == "outstanding"
    assert "Later confirm-park" in verdict["gate_note"]


def test_outcome_ledger_verdict_delivered_wrong_cannot_pass(project):
    from overbae.models import ConversationEvent

    cid = str(uuid.uuid4())
    row = _execution(project, cid, "yes — create it")
    ConversationEvent.objects.create(
        project=project,
        conversation_id=cid,
        execution=row,
        event_type="ask_opened",
        ask_id="a1",
        ask_kind="produce",
        order=0,
    )
    ConversationEvent.objects.create(
        project=project,
        conversation_id=cid,
        execution=row,
        event_type="delivered_wrong",
        ask_id="a1",
        order=1,
    )
    verdict = ledger.outcome_ledger_verdict(row)
    assert verdict["delivery"] == "delivered_wrong"
    assert "cannot pass" in verdict["gate_note"]
