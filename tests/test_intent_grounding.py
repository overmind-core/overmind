from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest import mock

import pytest

from overbae.api import overmind_attrs as oc_attrs
from overbae.models import (
    Capability,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    Span,
    TaskExecution,
)
from overbae.services.behaviour import ledger
from overbae.services.behaviour.ledger import TurnTransition, TurnTransitions
from overbae.services.eval.grounding import capability_mapping_context, cluster_occupancy
from overbae.services.eval.rubric_compiler import build_judge_prompt
from overbae.services.eval.trace_scoring import score_trace

CARD = {
    "tool_spec": [
        {
            "name": "create_finetune_job",
            "cluster": "finetune lifecycle",
            "purpose": "start a finetune run",
            "side_effect": "write",
        },
        {
            "name": "delete_project",
            "cluster": "platform admin",
            "purpose": "destroy a project",
            "side_effect": "destructive",
        },
        {"name": "list_models", "purpose": "read model registry"},
    ],
    "trajectory_map": [
        {
            "id": "chat-turn",
            "claim": "decision_surface",
            "routing": "the model picks tools per user ask",
            "terminal": {"kind": "emits_record"},
        }
    ],
}


def test_cluster_occupancy_joins_via_canonical_names():
    occupancy = cluster_occupancy(
        ["Create-Finetune-Job", "delete_project", "list_models", "rm_rf_everything"],
        CARD["tool_spec"],
    )
    assert occupancy == {
        "finetune lifecycle": ["create_finetune_job"],
        "platform admin": ["delete_project"],
        "(unclustered)": ["list_models"],
        "(undeclared)": ["rm_rf_everything"],
    }


def test_cluster_occupancy_dedupes_and_skips_blanks():
    occupancy = cluster_occupancy(
        ["create_finetune_job", "create_finetune_job", ""], CARD["tool_spec"]
    )
    assert occupancy == {"finetune lifecycle": ["create_finetune_job"]}
    assert cluster_occupancy([], CARD["tool_spec"]) == {}


def _capability_stub(card=CARD):
    return SimpleNamespace(improvement_metadata={"capability_card": card})


def test_capability_mapping_context_renders_clusters_and_decision_surface():
    text = capability_mapping_context(_capability_stub(), "chat-turn")
    assert "Bound path: chat-turn" in text
    assert "Decision surface: chat-turn" in text
    assert "Cluster 'finetune lifecycle':" in text
    assert "create_finetune_job — start a finetune run; side_effect=write" in text
    assert "Cluster '(unclustered)':" in text


def test_capability_mapping_context_empty_without_card():
    assert capability_mapping_context(SimpleNamespace(improvement_metadata={})) == ""


def _stub_evaluator():
    return SimpleNamespace(
        rubric_md="Judge the outcome.",
        checklist=[{"id": "ok", "q": "Did it work?", "weight": 1.0}],
        score_type="numeric",
        score_min=0.0,
        score_max=1.0,
        choices=[],
    )


def test_build_judge_prompt_renders_grounding_section():
    grounding = {
        "user_intent": {"text": "I want to fine tune a model", "source": "declared"},
        "mapping": capability_mapping_context(_capability_stub(), "chat-turn"),
        "cluster_occupancy": {"platform admin": ["delete_project"]},
    }
    prompt = build_judge_prompt(_stub_evaluator(), {"output": "done"}, grounding=grounding)
    assert "The user's ask (declared, verbatim): I want to fine tune a model" in prompt
    assert "Score this turn against the ask it serves" in prompt
    assert "must LOWER the score" in prompt
    assert "Empty or truncated tool payloads are not a miss on inspect or list" in prompt
    assert "Capability map" in prompt
    assert "- platform admin: delete_project" in prompt


def test_build_judge_prompt_renders_binding_evidence():
    grounding = {
        "user_intent": {"text": "triage my inbox", "source": "declared"},
        "binding": {
            "behaviour_key": "email-invoice-triage",
            "binding_source": "anchor_join",
            "matched_anchors": ["capability.triage_email", "capability.extract_invoice"],
        },
    }
    prompt = build_judge_prompt(_stub_evaluator(), {"output": "done"}, grounding=grounding)
    assert (
        "Bound behaviour (which task this run bound to — evidence, not a scoring contract): "
        in prompt
    )
    assert "email-invoice-triage" in prompt
    assert "capability.triage_email, capability.extract_invoice" in prompt
    assert "Binding is evidence, not ground truth of correctness" in prompt


def test_build_judge_prompt_unchanged_without_grounding():
    prompt = build_judge_prompt(_stub_evaluator(), {"output": "done"})
    assert "Grounding context" not in prompt


def test_build_judge_prompt_renders_trace_context_not_ledger():
    """Judges see neutral run structure; the ledger's folded conclusions never render."""
    grounding = {
        "user_intent": {"text": "make an alpha-set with the north intent", "source": "declared"},
        "trace_context": {
            "declared_intent": {
                "text": "make an alpha-set with the north intent",
                "source": "declared",
            },
            "run_total_s": 631.4,
            "root_final_output": "alpha-set created",
            "terminal_span_id": "s2",
            "position": {"index": 1, "of": 2, "operation": "Capability.step", "is_terminal": False},
            "timeline": [
                {
                    "index": 1,
                    "span_id": "s1",
                    "operation": "Capability.step",
                    "started_s": 1.5,
                    "duration_s": 29.4,
                    "status": "ok",
                    "terminal": False,
                    "tools": ['navigate({"url": "https://x"})'],
                },
                {
                    "index": 2,
                    "span_id": "s2",
                    "operation": "Capability.step",
                    "started_s": 31.0,
                    "duration_s": 12.0,
                    "status": "ok",
                    "terminal": True,
                    "tools": ["done"],
                },
            ],
        },
        # A stray ledger key must render nothing.
        "ledger": {"status": "outstanding", "open_asks": [{"reprompts": 2}]},
    }
    prompt = build_judge_prompt(_stub_evaluator(), {"output": "created"}, grounding=grounding)
    assert "This evaluation targets invocation 1 of 2" in prompt
    assert "The run CONTINUES after this invocation" in prompt
    assert "alpha-set created" in prompt
    assert "[terminal]" in prompt
    assert "[THIS UNIT]" in prompt
    assert "Conversation ask ledger" not in prompt
    assert "reprompts=" not in prompt
    assert "entering status" not in prompt

    terminal_grounding = dict(grounding)
    terminal_grounding["trace_context"] = {
        **grounding["trace_context"],
        "position": {"index": 2, "of": 2, "operation": "Capability.step", "is_terminal": True},
    }
    terminal_prompt = build_judge_prompt(
        _stub_evaluator(), {"output": "created"}, grounding=terminal_grounding
    )
    assert "TERMINAL invocation" in terminal_prompt


def test_build_judge_prompt_renders_produced_identity():
    grounding = {
        "user_intent": {"text": "Yes — create with the north source", "source": "declared"},
        "produced_identity": [{"name": "alpha-set", "intent": "south-kind"}],
    }
    prompt = build_judge_prompt(_stub_evaluator(), {"output": "created"}, grounding=grounding)
    assert "Produced artifact identity fields" in prompt
    assert "south-kind" in prompt
    assert "compare each to the asked kind" in prompt


def test_compact_conversation_clips_and_keeps_tools():
    from overbae.services.eval.envelope import compact_conversation

    turns = compact_conversation(
        [
            SimpleNamespace(pk="1", role="user", content="I want to fine-tune", parts=[]),
            SimpleNamespace(
                pk="2",
                role="assistant",
                content="ok",
                parts=[
                    {"type": "activity", "phase": "tool_done", "tool": "create_finetune_job"},
                    {"type": "activity", "phase": "tool_start", "tool": "ignored"},
                ],
            ),
            SimpleNamespace(pk="3", role="user", content="now the curves", parts=[]),
        ],
        exclude_ids=["3"],
    )
    assert turns == [
        {"role": "user", "text": "I want to fine-tune"},
        {"role": "assistant", "text": "ok", "tools": ["create_finetune_job"]},
    ]


def test_parse_conversation_context_from_envelope_facts():
    from overbae.services.eval.envelope import (
        conversation_context_facts,
        parse_conversation_context,
    )

    facts = conversation_context_facts(
        conversation_id="sess-1",
        prior_turns=[{"role": "user", "text": "fine-tune"}],
    )
    turns, running, cid = parse_conversation_context(facts)
    assert cid == "sess-1"
    assert running == ""
    assert turns == [{"role": "user", "text": "fine-tune"}]


pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project) -> Capability:
    capability = Capability.objects.create(
        project=project,
        name="A",
        slug=f"a-{uuid.uuid4().hex[:6]}",
        improvement_metadata={"capability_card": CARD},
    )
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="Default")
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="contains-ok",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.TRAJECTORY,
        version=1,
        pass_threshold=1.0,
        config={"check": "contains", "reference": "done"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.TRACE_SCORING, order=0
    )
    return capability


def _intent_event(text: str) -> dict:
    return {
        "time_unix_nano": 1,
        "name": oc_attrs.EVAL_EVENT_INTENT,
        "attributes": {
            oc_attrs.EVAL_SCHEMA_VERSION: 1,
            oc_attrs.EVAL_PAYLOAD: json.dumps({"text": text, "source": "declared"}),
        },
    }


def _trace(project, capability, *, events=(), with_tool=True, conversation_id="") -> str:
    trace_id = uuid.uuid4().hex
    attributes = {
        "overmind.input.data": "please fine tune a model on my tickets",
        "overmind.output.data": "done, job started",
    }
    if conversation_id:
        attributes["conversation.id"] = conversation_id
    root = Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=None,
        project=project,
        capability=capability,
        events=list(events),
        attributes=attributes,
    )
    if with_tool:
        Span.objects.create(
            span_id=uuid.uuid4().hex[:16],
            trace_id=trace_id,
            parent_span_id=root.span_id,
            project=project,
            capability=capability,
            span_type="tool_call",
            attributes={"tool.name": "create_finetune_job"},
        )
    return trace_id


def test_declared_intent_wins_and_occupancy_persists():
    project = _project()
    capability = _capability(project)
    trace_id = _trace(project, capability, events=[_intent_event("fine tune a model")])

    result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    execution = TaskExecution.objects.get(project=project, trace_id=trace_id)
    assert execution.user_intent == {"text": "fine tune a model", "source": "declared"}
    assert execution.observed_route["cluster_occupancy"] == {
        "finetune lifecycle": ["create_finetune_job"]
    }


def test_intent_falls_back_to_first_user_message():
    project = _project()
    capability = _capability(project)
    trace_id = _trace(project, capability)

    score_trace(trace_id, str(project.id))

    execution = TaskExecution.objects.get(project=project, trace_id=trace_id)
    assert execution.user_intent == {
        "text": "please fine tune a model on my tickets",
        "source": "first_message",
    }


def test_scaffold_detection_is_structural():
    from overbae.services.eval.trace_scoring import _is_scaffold_text

    assert _is_scaffold_text("Conversation Context:\nUser: hi\nAssistant: hello")
    assert _is_scaffold_text("User: do the thing\nAssistant: done\nUser: now this")
    # A genuine ask — even harness-wrapped uploads — is not a transcript dump.
    assert not _is_scaffold_text("please fine tune a model on my tickets")
    assert not _is_scaffold_text(
        "<current_uploads>\n- data.csv (59 KB)\n</current_uploads>\nanalyze this file"
    )
    # A single role label is a style choice, not an embedded transcript.
    assert not _is_scaffold_text("User: analyze my data")


def test_conversation_turn_records_ledger_and_running_intent():
    """The ledger classifier is the only writer; task_state reads its transitions back."""
    from overbae.models import ConversationEvent

    project = _project()
    capability = _capability(project)
    cid = str(uuid.uuid4())
    trace_id = _trace(
        project,
        capability,
        events=[_intent_event("fine tune a model on my tickets")],
        conversation_id=cid,
    )
    with mock.patch.object(
        ledger.judging,
        "invoke_judge",
        return_value=SimpleNamespace(
            parsed=TurnTransitions(
                transitions=[
                    TurnTransition(
                        event="ask_opened",
                        ask_text="fine tune a model on my tickets",
                        ask_kind="produce",
                    )
                ]
            )
        ),
    ):
        result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"

    execution = TaskExecution.objects.get(project=project, trace_id=trace_id)
    assert execution.conversation_id == cid
    events = list(ConversationEvent.objects.filter(conversation_id=cid).order_by("order"))
    assert [e.event_type for e in events] == ["ask_opened"]
    assert events[0].ask_kind == "produce"
    assert events[0].source == ConversationEvent.Source.CLASSIFIER
    assert execution.session_score is not None
    assert execution.observed_route["task_state"]["running_intent"] == (
        "fine tune a model on my tickets"
    )


def test_conversation_turn_classifier_down_does_not_sink_scoring():
    """A failing classifier leaves the turn unrecorded — no lexical fallback."""
    from overbae.models import ConversationEvent

    project = _project()
    capability = _capability(project)
    cid = str(uuid.uuid4())
    trace_id = _trace(
        project,
        capability,
        events=[_intent_event("fine tune a model on my tickets")],
        conversation_id=cid,
    )
    with mock.patch.object(ledger.judging, "invoke_judge", side_effect=RuntimeError("down")):
        result = score_trace(trace_id, str(project.id))
    assert result["status"] == "scored"
    assert ConversationEvent.objects.filter(conversation_id=cid).count() == 0
    execution = TaskExecution.objects.get(project=project, trace_id=trace_id)
    assert execution.user_intent["text"] == "fine tune a model on my tickets"
