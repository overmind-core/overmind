import uuid

import pytest

from overbae.models import Capability, EvalSet, EvalSetMember, Evaluator, Project, Verdict
from overbae.services.eval.dispatch import (
    EnvelopeLabels,
    abstention_kwargs,
    environment_corpus,
    fetch_existing,
    grain_matches,
    persist_verdicts,
    plan_members,
    warrant_unmet,
)
from overbae.services.eval.evaluators import base as eval_base
from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance, Warrant


def _spec(name: str, **kwargs) -> EvaluatorSpec:
    kwargs.setdefault("kind", "llm_judge")
    kwargs.setdefault("rubric_md", "Grade the answer.")
    kwargs.setdefault(
        "provenance",
        SpecProvenance(source="test", generator="test@v1", surface_area="trajectory"),
    )
    return EvaluatorSpec(name=name, **kwargs)


def _member(name: str, **evaluator_kwargs) -> EvalSetMember:
    evaluator_kwargs.setdefault("kind", "llm_judge")
    return EvalSetMember(evaluator=Evaluator(name=name, **evaluator_kwargs))


def test_warrant_unmet_is_a_set_comparison():
    warrant = Warrant(provenance=["agent", "environment"], detail="full", requires=["tool_io"])

    full = EnvelopeLabels(detail="full", provenance={"agent", "environment"}, evidence={"tool_io"})
    assert warrant_unmet(warrant, full) == []

    bare = EnvelopeLabels(detail="compacted", provenance={"agent"}, evidence=set())
    unmet = warrant_unmet(warrant, bare)
    assert "provenance:environment" in unmet
    assert "detail:full" in unmet
    assert "evidence:tool_io" in unmet


def test_grain_matches_terminal_only_at_delivery():
    assert grain_matches("terminal", is_terminal=True)
    assert not grain_matches("terminal", is_terminal=False)
    assert not grain_matches("session", is_terminal=False)
    assert grain_matches("unit", is_terminal=False)


def test_trajectory_grain_binds_once_at_terminal_on_turn_slices():
    """A mid-run turn slice would read cross-turn history as this turn's failure;
    a behaviour-bound sibling is a complete trajectory of its own."""
    assert not grain_matches("trajectory", is_terminal=False, turn_slice=True)
    assert grain_matches("trajectory", is_terminal=True, turn_slice=True)
    assert grain_matches("trajectory", is_terminal=False, turn_slice=True, behaviour_bound=True)
    assert grain_matches("trajectory", is_terminal=False)


def test_trajectory_grain_binds_at_capability_terminal_on_multi_capability_traces():
    assert grain_matches("trajectory", is_terminal=False, turn_slice=True, capability_terminal=True)
    assert not grain_matches(
        "trajectory", is_terminal=False, turn_slice=True, capability_terminal=False
    )
    assert not grain_matches("terminal", is_terminal=False, capability_terminal=True)
    assert not grain_matches("session", is_terminal=False, capability_terminal=True)


def test_plan_members_bound_task_runs_despite_unmet_warrant():
    labels = EnvelopeLabels(detail="full", provenance=set(), evidence=set())
    specs = {
        "outcome": _spec(
            "outcome",
            claim={"type": "quality", "grain": "trajectory"},
            warrant={
                "provenance": ["agent"],
                "detail": "full",
                "requires": ["final_output", "tool_io"],
            },
        ),
        "generic": _spec(
            "generic",
            warrant={"provenance": ["agent"], "detail": "full", "requires": ["final_output"]},
        ),
    }
    members = [
        _member(
            "outcome", config={"behaviour": {"behaviour_key": "run-startup", "role": "outcome"}}
        ),
        _member("generic"),
    ]
    plan = plan_members(members, specs, labels, is_terminal=True)
    assert {m.evaluator.name for m in plan.runnable} == {"outcome"}
    assert "generic" in plan.abstained


def test_plan_members_outcome_judge_abstains_without_ask_or_evidence():
    bare = EnvelopeLabels(detail="full", provenance={"agent"}, evidence=set())
    specs = {
        "outcome": _spec("outcome", claim={"type": "quality", "grain": "trajectory"}),
        "step": _spec("step", claim={"type": "progress", "grain": "trajectory"}),
    }
    members = [
        _member("outcome", config={"behaviour": {"behaviour_key": "b", "role": "outcome"}}),
        _member("step", config={"behaviour": {"behaviour_key": "b", "role": "step"}}),
    ]
    plan = plan_members(members, specs, bare, is_terminal=True, has_ask=False)
    assert plan.abstained == {"outcome": ["evidence:unit_output"]}
    assert {m.evaluator.name for m in plan.runnable} == {"step"}

    with_ask = plan_members(members, specs, bare, is_terminal=True, has_ask=True)
    assert {m.evaluator.name for m in with_ask.runnable} == {"outcome", "step"}

    with_output = EnvelopeLabels(detail="full", provenance={"agent"}, evidence={"final_output"})
    plan = plan_members(members, specs, with_output, is_terminal=True, has_ask=False)
    assert {m.evaluator.name for m in plan.runnable} == {"outcome", "step"}


def test_plan_members_turn_slice_keeps_behaviour_bound_trajectory_members():
    labels = EnvelopeLabels(detail="full", provenance={"agent", "user"}, evidence=set())
    specs = {
        "step_judge": _spec("step_judge", claim={"type": "progress", "grain": "trajectory"}),
        "run_claim": _spec("run_claim", claim={"type": "safety", "grain": "trajectory"}),
    }
    members = [
        _member("step_judge", config={"behaviour": {"behaviour_key": "b", "role": "step"}}),
        _member("run_claim"),
    ]
    mid_run = plan_members(members, specs, labels, is_terminal=False, turn_slice=True)
    assert {m.evaluator.name for m in mid_run.runnable} == {"step_judge"}
    assert mid_run.skipped == ["run_claim"]


def test_plan_members_interrupted_unit_skips_delivery_grades_keeps_steps():
    """An interrupted run never delivered, so terminal/session claims have no
    subject and skip retryably; unit- and trajectory-grain claims (behaviour
    step members included) still grade the steps that did happen."""
    labels = EnvelopeLabels(
        detail="full",
        provenance={"agent", "user", "environment"},
        evidence={"tool_io", "final_output"},
    )
    specs = {
        "success": _spec("success", claim={"type": "verification", "grain": "terminal"}),
        "session": _spec("session", claim={"type": "quality", "grain": "session"}),
        "step": _spec("step", claim={"type": "progress", "grain": "trajectory"}),
        "safety": _spec("safety", claim={"type": "safety", "grain": "trajectory"}),
        "per_unit": _spec("per_unit", claim={"type": "progress", "grain": "unit"}),
    }
    members = [
        _member("success", config={"behaviour": {"behaviour_key": "b", "role": "outcome"}}),
        _member("session"),
        _member("step", config={"behaviour": {"behaviour_key": "b", "role": "step"}}),
        _member("safety"),
        _member("per_unit"),
    ]
    plan = plan_members(members, specs, labels, is_terminal=True, interrupted=True)
    assert set(plan.interrupted) == {"success", "session"}
    assert {m.evaluator.name for m in plan.runnable} == {"step", "safety", "per_unit"}
    assert plan.skipped == []

    complete = plan_members(members, specs, labels, is_terminal=True)
    assert complete.interrupted == []
    assert {m.evaluator.name for m in complete.runnable} == set(specs)


def test_plan_members_routes_runnable_abstained_skipped():
    labels = EnvelopeLabels(detail="full", provenance={"agent", "user"}, evidence={"final_output"})
    specs = {
        "quality": _spec("quality"),
        "verify": _spec(
            "verify",
            claim={"type": "verification", "grain": "terminal"},
            warrant={"provenance": ["environment"], "detail": "full", "requires": ["tool_io"]},
        ),
        "terminal_only": _spec("terminal_only"),
    }
    members = [_member(n) for n in specs]

    mid_run = plan_members(members, specs, labels, is_terminal=False)
    assert set(mid_run.skipped) == {"quality", "verify", "terminal_only"}

    at_delivery = plan_members(members, specs, labels, is_terminal=True)
    runnable = {m.evaluator.name for m in at_delivery.runnable}
    assert runnable == {"quality", "terminal_only"}
    assert set(at_delivery.abstained["verify"]) == {"provenance:environment", "evidence:tool_io"}


def _tool_node(inputs, outputs, tool="lookup"):
    return {
        "id": "s1",
        "name": "Tools.act",
        "tool": tool,
        "type": "tool_call",
        "inputs": inputs,
        "outputs": outputs,
        "children": [],
    }


def test_environment_corpus_drops_pure_echo_outputs():
    """Done-style terminal tools echo the input back: self-report, not environment evidence."""
    claim = "Flight BA287 departs LHR 14:30, round trip price £768 shown."
    tree = [_tool_node({"action": {"done": {"text": claim}}}, {"extracted_content": claim})]
    assert environment_corpus([], tree) == []


def test_environment_corpus_scrubs_echoed_fragments_keeps_independent():
    claim = "Flight BA287 departs LHR 14:30, round trip price £768 shown."
    observed = "Google Flights results page lists BA285 dep 10:25 and BA287 dep 14:30."
    tree = [_tool_node({"query": claim}, {"echo": claim, "page_text": observed})]
    corpus = environment_corpus([], tree)
    assert len(corpus) == 1
    assert observed in corpus[0]
    assert claim not in corpus[0]


def test_environment_corpus_catches_truncated_wrapped_echoes():
    claim = (
        "British Airways direct flight found: Flight BA287, departs LHR 14:30 (2:30 PM), "
        "arrives SFO 5:40 PM on Aug 29. Round trip price £768 shown."
    )
    truncated = f"Task completed: True - {claim[:100]} - 113 more characters"
    tree = [_tool_node({"done": {"text": claim}}, {"long_term_memory": truncated})]
    assert environment_corpus([], tree) == []


def test_environment_corpus_keeps_genuine_tool_outputs():
    tree = [_tool_node({"url": "https://flights.example"}, {"page_text": "BA287 dep 14:30 £768"})]
    corpus = environment_corpus([], tree)
    assert len(corpus) == 1
    assert "BA287" in corpus[0]


@pytest.mark.django_db
def test_verdict_series_key_idempotency():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(project=project, name="A", slug="a")
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="default")
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="q",
        kind="llm_judge",
        rubric_md="Grade the answer.",
    )
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.TRACE_SCORING, order=0
    )

    kwargs = abstention_kwargs(
        member,
        ["evidence:tool_io"],
        project_id=str(project.id),
        target_id="span-1",
        identifier="openai:gpt-x:abc",
        reason="warrant unmet",
    )
    persist_verdicts([kwargs])
    persist_verdicts([dict(kwargs)])
    assert Verdict.objects.filter(target_id="span-1").count() == 1

    row = Verdict.objects.get(target_id="span-1")
    assert row.outcome == Verdict.Outcome.ABSTAINED
    assert row.unmet == ["evidence:tool_io"]

    hits = fetch_existing(str(project.id), "span-1", {"q": "openai:gpt-x:abc"})
    assert set(hits) == {"q"}
    # A changed judge contract misses, so the new series re-dispatches.
    assert fetch_existing(str(project.id), "span-1", {"q": "openai:gpt-y:def"}) == {}


def test_panel_tie_abstains_on_passed_instead_of_failing(monkeypatch):
    from overbae.services.eval import dispatch as dispatch_mod
    from overbae.services.eval import funnel as funnel_mod

    drafts = iter(
        [
            [eval_base.ScoreDraft(name="v", value=1.0, passed=True, outcome="scored")],
            [eval_base.ScoreDraft(name="v", value=0.2, passed=False, outcome="scored")],
            [eval_base.ScoreDraft(name="v", value=None, outcome="error")],
        ]
    )
    monkeypatch.setattr(dispatch_mod.eval_base, "evaluate", lambda *a, **k: next(drafts))
    monkeypatch.setattr(
        dispatch_mod.funnel,
        "cross_family_judges",
        lambda n: [
            funnel_mod.ResolvedJudge(model_name=f"m{i}", model_spec=None, family=f"f{i}")
            for i in range(n)
        ],
    )
    member = _member("v")
    (merged,) = dispatch_mod._panel_evaluate(member, eval_base.EvalUnit(), {})
    assert merged.passed is None
    assert merged.outcome == "scored"


def test_panel_spread_persisted_in_panel_sub_score(monkeypatch):
    from overbae.services.eval import dispatch as dispatch_mod
    from overbae.services.eval import funnel as funnel_mod

    drafts = iter(
        [
            [eval_base.ScoreDraft(name="v", value=1.0, passed=True, outcome="scored")],
            [eval_base.ScoreDraft(name="v", value=0.2, passed=False, outcome="scored")],
            [eval_base.ScoreDraft(name="v", value=0.9, passed=True, outcome="scored")],
        ]
    )
    monkeypatch.setattr(dispatch_mod.eval_base, "evaluate", lambda *a, **k: next(drafts))
    monkeypatch.setattr(
        dispatch_mod.funnel,
        "cross_family_judges",
        lambda n: [
            funnel_mod.ResolvedJudge(model_name=f"m{i}", model_spec=None, family=f"f{i}")
            for i in range(n)
        ],
    )
    (merged,) = dispatch_mod._panel_evaluate(_member("v"), eval_base.EvalUnit(), {})
    panel = merged.sub_scores[-1]["_panel"]
    assert panel["spread"] == 0.8
    assert [m["value"] for m in panel["members"]] == [1.0, 0.2, 0.9]
    assert merged.value == 0.9


def test_numeric_judge_contract_hashes_the_anchor_scale(monkeypatch):
    from overbae.services.eval import dispatch as dispatch_mod
    from overbae.services.eval import rubric_compiler

    stored = {"content_hash": "a" * 64}
    numeric = Evaluator(
        name="q",
        kind="llm_judge",
        score_type="numeric",
        score_min=0.0,
        score_max=1.0,
        config=dict(stored),
    )
    boolean = Evaluator(
        name="b",
        kind="llm_judge",
        score_type="boolean",
        score_min=0.0,
        score_max=1.0,
        config=dict(stored),
    )
    boolean_digest = dispatch_mod._rubric_digest(boolean)
    assert boolean_digest != "a" * 16
    numeric_digest = dispatch_mod._rubric_digest(numeric)
    assert numeric_digest != "a" * 16

    monkeypatch.setattr(rubric_compiler, "NUMERIC_ANCHOR_SCALE", "different anchors")
    assert dispatch_mod._rubric_digest(numeric) != numeric_digest
    assert dispatch_mod._rubric_digest(boolean) == boolean_digest
    boolean.config["decision"] = {"backend": "generative"}
    assert dispatch_mod._rubric_digest(boolean) == boolean_digest
    boolean.config["decision"] = {"backend": "jev"}
    assert dispatch_mod._rubric_digest(boolean) != boolean_digest


def _unit_with_tools(*nodes: dict) -> eval_base.EvalUnit:
    return eval_base.EvalUnit(structured={"tool_graph": {"nodes": list(nodes)}})


def test_scope_draft_drops_click_cited_from_another_turn():
    unit = _unit_with_tools({"tool": "navigate", "arguments": {"url": "https://flights"}})
    draft = eval_base.ScoreDraft(
        name="Hallucinated Element Index Avoidance",
        value=0.0,
        passed=False,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning="click index 1882 is not in the preceding interactive_indices list",
    )
    scoped = eval_base.scope_draft_to_unit_tree(draft, unit)
    assert scoped.outcome == eval_base.OUTCOME_NOT_APPLICABLE
    assert scoped.value is None


def test_scope_draft_keeps_fail_for_index_this_unit_used():
    unit = _unit_with_tools({"tool": "click", "arguments": {"index": 1882}})
    draft = eval_base.ScoreDraft(
        name="Hallucinated Element Index Avoidance",
        value=0.0,
        passed=False,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning="click index 1882 is not in the preceding interactive_indices list",
    )
    scoped = eval_base.scope_draft_to_unit_tree(draft, unit)
    assert scoped.outcome == eval_base.OUTCOME_SCORED
    assert scoped.passed is False


def test_scope_draft_drops_clicking_prose_without_index():
    unit = _unit_with_tools({"tool": "navigate", "arguments": {"url": "https://flights"}})
    draft = eval_base.ScoreDraft(
        name="Repeated Failing Action Detection",
        value=0.2,
        passed=False,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning="Capability repeatedly retried clicking the Heathrow airport element",
    )
    scoped = eval_base.scope_draft_to_unit_tree(draft, unit)
    assert scoped.outcome == eval_base.OUTCOME_NOT_APPLICABLE


def test_scope_entry_rewrites_leaked_feedback_row():
    unit = _unit_with_tools({"tool": "navigate", "arguments": {"url": "https://flights"}})
    entry = {
        "score": 0.0,
        "passed": False,
        "outcome": "scored",
        "rationale": "Capability repeatedly retried clicking the Heathrow element index 4559",
        "surface_area": "failure_mode",
    }
    patched = eval_base.scope_entry_to_unit_tree(entry, unit)
    assert patched["outcome"] == "not_applicable"
    assert patched["score"] is None


def test_scope_draft_ignores_unrelated_failures():
    unit = _unit_with_tools({"tool": "navigate", "arguments": {"url": "https://flights"}})
    draft = eval_base.ScoreDraft(
        name="Live Task Success",
        value=0.2,
        passed=True,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning="The navigate did not complete the booking task.",
    )
    assert eval_base.scope_draft_to_unit_tree(draft, unit) is draft


def test_scope_draft_keeps_fail_that_mentions_input_context():
    unit = eval_base.EvalUnit()
    draft = eval_base.ScoreDraft(
        name="Quality Signals",
        value=0.0,
        passed=False,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning=(
            "The input context was non-empty and the output produced a full report "
            "rather than an abstention."
        ),
    )
    scoped = eval_base.scope_draft_to_unit_tree(draft, unit)
    assert scoped is draft
    assert scoped.outcome == eval_base.OUTCOME_SCORED
    assert scoped.value == 0.0


def _failing_click_draft():
    return eval_base.ScoreDraft(
        name="Repeated Failing Action Detection",
        value=0.2,
        passed=False,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning="Capability repeatedly retried clicking the Heathrow airport element",
    )


def _stub_deterministic(monkeypatch, draft):
    monkeypatch.setattr(
        "overbae.services.eval.evaluators.deterministic.evaluate",
        lambda unit, evaluator, ctx: [draft],
    )


def test_evaluate_skips_span_tree_warrant_on_generative_surface(monkeypatch):
    unit = eval_base.EvalUnit()
    draft = _failing_click_draft()
    ev = type(
        "Ev",
        (),
        {
            "name": draft.name,
            "kind": "deterministic",
            "score_type": "numeric",
            "scope": "sample",
            "config": {},
            "variable_mapping": [],
        },
    )()
    _stub_deterministic(monkeypatch, draft)
    out = eval_base.evaluate(unit, ev, {"eval_surface": eval_base.SURFACE_GENERATIVE})
    assert out[0] is draft
    assert out[0].outcome == eval_base.OUTCOME_SCORED


def test_evaluate_applies_span_tree_warrant_on_trace_surface(monkeypatch):
    unit = _unit_with_tools({"tool": "navigate", "arguments": {"url": "https://flights"}})
    draft = _failing_click_draft()
    ev = type(
        "Ev",
        (),
        {
            "name": draft.name,
            "kind": "deterministic",
            "score_type": "numeric",
            "scope": "sample",
            "config": {},
            "variable_mapping": [],
        },
    )()
    _stub_deterministic(monkeypatch, draft)
    out = eval_base.evaluate(unit, ev, {"eval_surface": eval_base.SURFACE_TRACE_SCORING})
    assert out[0].outcome == eval_base.OUTCOME_NOT_APPLICABLE
    assert out[0].value is None


def test_abstention_reason_states_what_was_looked_for():
    from overbae.services.eval.dispatch import abstention_reason

    reason = abstention_reason(["evidence:final_output", "detail:full"])
    assert "looked for" in reason.lower()
    assert "tool-call" in reason
    assert "truncated" in reason
    generic = abstention_reason(["provenance:environment"])
    assert "environment-provenance spans" in generic


@pytest.mark.django_db
def test_skip_verdict_is_retryable_and_overwritten_by_real_verdict():
    from overbae.services.eval.dispatch import SKIP_BEHAVIOUR, is_skip_verdict, skip_kwargs

    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(project=project, name="A", slug="a-skip")
    eval_set = EvalSet.objects.create(project=project, capability=capability, name="default")
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="bound-judge",
        kind="llm_judge",
        rubric_md="Grade the answer.",
    )
    member = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.TRACE_SCORING, order=0
    )

    persist_verdicts(
        [
            skip_kwargs(
                member,
                SKIP_BEHAVIOUR,
                "Skipped: member is bound to behaviour 'x'; this execution is bound to 'y'.",
                project_id=str(project.id),
                target_id="span-skip",
                identifier="openai:gpt-x:abc",
            )
        ]
    )
    row = Verdict.objects.get(target_id="span-skip")
    assert row.outcome == Verdict.Outcome.NOT_APPLICABLE
    assert is_skip_verdict(row)
    assert row.unmet == [SKIP_BEHAVIOUR]
    assert "Skipped" in row.explanation

    persist_verdicts(
        [
            abstention_kwargs(
                member,
                ["evidence:tool_io"],
                project_id=str(project.id),
                target_id="span-skip",
                identifier="openai:gpt-x:abc",
                reason="warrant unmet",
            )
        ]
    )
    assert Verdict.objects.filter(target_id="span-skip").count() == 1
    row.refresh_from_db()
    assert not is_skip_verdict(row)
    assert row.outcome == Verdict.Outcome.ABSTAINED


def test_plain_not_applicable_verdict_is_not_a_skip():
    from overbae.services.eval.dispatch import is_skip_verdict

    row = Verdict(outcome=Verdict.Outcome.NOT_APPLICABLE, unmet=[])
    assert not is_skip_verdict(row)


class _JudgeOutcome:
    def __init__(self, parsed):
        self.parsed = parsed
        self.stats = {}
        self.judge_trace_id = "jt"


def _grounding(monkeypatch, checks):
    from types import SimpleNamespace

    from overbae.services.eval import dispatch as dispatch_mod

    claims = [f"claim {i}" for i in range(len(checks))]
    outcomes = iter(
        [
            _JudgeOutcome(SimpleNamespace(claims=claims)),
            _JudgeOutcome(
                SimpleNamespace(
                    checks=[SimpleNamespace(index=i, verdict=v) for i, v in enumerate(checks)]
                )
            ),
        ]
    )
    monkeypatch.setattr(dispatch_mod.funnel, "invoke_judge", lambda *a, **k: next(outcomes))
    return dispatch_mod.grounding_verdict(
        trajectory={"final_output": "The report was filed and three rows were updated."},
        unit_spans=[],
        project_id="p",
        target_id="s",
        trace_corpus=["update result: 3 rows changed"],
    )


def test_grounding_score_counts_only_checkable_claims(monkeypatch):
    row = _grounding(monkeypatch, ["supported", "supported", "contradicted", "insufficient"])
    assert row["outcome"] == Verdict.Outcome.SCORED
    # supported / (supported + contradicted), never supported / total.
    assert row["score"] == pytest.approx(2 / 3)
    assert row["metadata"]["coverage"] == 0.75
    assert "coverage" in row["explanation"]


def test_grounding_all_insufficient_abstains_never_zero(monkeypatch):
    row = _grounding(monkeypatch, ["insufficient"] * 17)
    assert row["outcome"] == Verdict.Outcome.ABSTAINED
    assert row["score"] is None
    assert row["unmet"] == ["claims:uncheckable"]
    assert row["metadata"]["coverage"] == 0.0
    assert "insufficient" in row["explanation"]
