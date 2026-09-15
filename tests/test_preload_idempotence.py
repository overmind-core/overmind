from __future__ import annotations

import pytest

from overbae.models import Capability, EvalRun, EvalSetMember, Evaluator, Project, RunEvaluator
from overbae.services.eval import semantic_recommender
from overbae.services.eval.eval_set import _merge_specs_into_set, generate_and_preload_default_set
from overbae.services.eval.specs import TIER0_GENERATOR, EvaluatorSpec, SpecProvenance

pytestmark = pytest.mark.django_db


@pytest.fixture
def agent(db):
    project = Project.objects.create(name="idem", slug="idem")
    return Capability.objects.create(
        project=project,
        name="extractor",
        slug="extractor",
        improvement_metadata={
            "capability_card": {
                "task": "Extract fields from a document.",
                "output_fields": {"amount": "number — total due", "summary": "string — a summary"},
                "output_schema": {"required_keys": ["amount"]},
            }
        },
    )


def _judge(name: str) -> EvaluatorSpec:
    return EvaluatorSpec(
        name=name,
        kind="llm_judge",
        scope="final_output",
        description="d",
        rubric_md="r",
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
        provenance=SpecProvenance(
            source="grounding", generator="tier1_llm@v1", surface_area="output_contract"
        ),
    )


def _gen_judge(name: str) -> EvaluatorSpec:
    return _judge(name).model_copy(update={"applicable_roles": ["generative"]})


def _authored_suites(specs):
    return specs, [], None


def test_tier_one_authors_once_and_a_rescan_adds_nothing(agent, monkeypatch):
    """The additive merge dedups on (name, scope), which makes a re-run a no-op
    for the deterministic compilers. Tier 1 is an LLM call: the same grounding
    yields judges that overlap in substance under DIFFERENT names, so name dedup
    cannot see them and every preload stacked another layer."""
    calls: list[int] = []

    def _drifting_author(*_a, **_k):
        # What was actually observed: one pass produced a combined judge, the
        # next split its items into two new ones.
        calls.append(len(calls))
        specs = (
            [_judge("Failure Mode Avoidance")]
            if len(calls) == 1
            else [
                _judge("Paid Receipt Rejection"),
                _judge("Amount Source Preference"),
            ]
        )
        return _authored_suites(specs)

    monkeypatch.setattr(semantic_recommender, "author_tier1_suites", _drifting_author)

    first = generate_and_preload_default_set(agent)
    assert first["tier1_authored"] is True
    assert Evaluator.objects.filter(capability=agent, name="Failure Mode Avoidance").exists()

    second = generate_and_preload_default_set(agent)
    assert second["tier1_authored"] is False
    assert len(calls) == 1, "tier 1 re-authored on a re-scan"
    assert not Evaluator.objects.filter(capability=agent, name="Paid Receipt Rejection").exists()
    assert not Evaluator.objects.filter(capability=agent, name="Amount Source Preference").exists()


def test_the_deterministic_checks_still_reconcile_on_a_rescan(agent, monkeypatch):
    """Skipping tier 1 must not skip tier 0: a check added to the compiler after
    an agent was scanned still has to reach it."""
    monkeypatch.setattr(
        semantic_recommender, "author_tier1_suites", lambda *a, **k: _authored_suites([_judge("J")])
    )
    generate_and_preload_default_set(agent)

    Evaluator.objects.filter(capability=agent, name="output-field-accuracy").delete()
    generate_and_preload_default_set(agent)

    assert Evaluator.objects.filter(capability=agent, name="output-field-accuracy").exists()


def test_a_skipped_pass_reports_no_missing_suite(agent, monkeypatch):
    """The empty-suite alarm exists for a real authoring failure. A skipped pass
    produces no specs by design, and reporting that would train people to ignore
    the signal."""
    monkeypatch.setattr(
        semantic_recommender, "author_tier1_suites", lambda *a, **k: _authored_suites([_judge("J")])
    )
    generate_and_preload_default_set(agent)

    second = generate_and_preload_default_set(agent)

    assert second["empty_tier1_suites"] == []


def _live_judge(project, capability, name, *, archived=False):
    return Evaluator.objects.create(
        project=project,
        capability=capability,
        name=name,
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        checklist=[{"id": "q1", "q": "?", "weight": 1.0}],
        is_archived=archived,
    )


def test_an_archived_evaluator_does_not_block_a_new_member_for_the_same_name(agent):
    from overbae.services.eval.eval_set import ensure_default_eval_set

    eval_set = ensure_default_eval_set(agent)
    archived = _live_judge(agent.project, agent, "Hallucination Avoidance", archived=True)
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=archived, role=EvalSetMember.Role.GENERATIVE
    )

    result = _merge_specs_into_set(agent, eval_set, [_gen_judge("Hallucination Avoidance")])

    live = Evaluator.objects.get(
        capability=agent, name="Hallucination Avoidance", is_archived=False
    )
    assert result["created"] == 1
    assert EvalSetMember.objects.filter(
        eval_set=eval_set, evaluator=live, role=EvalSetMember.Role.GENERATIVE
    ).exists()


def test_a_live_enabled_member_still_dedups_by_name(agent):
    from overbae.services.eval.eval_set import ensure_default_eval_set

    eval_set = ensure_default_eval_set(agent)
    live = _live_judge(agent.project, agent, "Task Success")
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=live, role=EvalSetMember.Role.GENERATIVE, enabled=True
    )
    before = EvalSetMember.objects.filter(eval_set=eval_set).count()

    result = _merge_specs_into_set(agent, eval_set, [_gen_judge("Task Success")])

    assert result["created"] == 0
    assert EvalSetMember.objects.filter(eval_set=eval_set).count() == before
    assert (
        Evaluator.objects.filter(capability=agent, name="Task Success", is_archived=False).count()
        == 1
    )


def _field_spec(*names: str) -> EvaluatorSpec:
    return EvaluatorSpec(
        name="output-field-accuracy",
        description="compiled fields",
        kind="deterministic",
        scope="final_output",
        config={
            "check": "canonical_fields",
            "fields": [{"name": n, "kind": k} for n, k in names],
        },
        provenance=SpecProvenance(
            source="codebase_card.output_fields",
            generator=TIER0_GENERATOR,
            surface_area="output_contract",
        ),
    )


def test_tier0_merge_refreshes_compiled_fields_in_place(agent):
    from overbae.services.eval.eval_set import ensure_default_eval_set

    eval_set = ensure_default_eval_set(agent)
    live = Evaluator.objects.create(
        project=agent.project,
        capability=agent,
        name="output-field-accuracy",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={
            "check": "canonical_fields",
            "fields": [
                {"name": "amount", "kind": "number"},
                {"name": "dueDate", "kind": "date"},
                {"name": "isInvoice", "kind": "boolean"},
            ],
            "provenance": {"generator": TIER0_GENERATOR},
        },
    )
    spec = _field_spec(
        ("amount", "number"),
        ("dueDate", "date"),
        ("isInvoice", "boolean"),
        ("currency", "string"),
        ("invoiceNumber", "string"),
        ("vendor", "string"),
    )

    result = _merge_specs_into_set(agent, eval_set, [spec])
    live.refresh_from_db()

    assert result["created"] == 0
    assert Evaluator.objects.filter(capability=agent, name="output-field-accuracy").count() == 1
    assert live.id == Evaluator.objects.get(capability=agent, name="output-field-accuracy").id
    fields = live.config["fields"]
    assert len(fields) == 6
    assert next(f for f in fields if f["name"] == "vendor")["kind"] == "string"


def test_llm_judge_is_not_overwritten_by_a_same_named_spec(agent):
    from overbae.services.eval.eval_set import ensure_default_eval_set

    eval_set = ensure_default_eval_set(agent)
    checklist = [{"id": "q1", "q": "old item", "weight": 1.0}]
    live = Evaluator.objects.create(
        project=agent.project,
        capability=agent,
        name="output-field-accuracy",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        checklist=checklist,
        rubric_md="Grade it.",
    )
    spec = _field_spec(("vendor", "string"))

    result = _merge_specs_into_set(agent, eval_set, [spec])
    live.refresh_from_db()

    assert result["created"] == 0
    assert live.kind == Evaluator.Kind.LLM_JUDGE
    assert live.checklist == checklist
    assert "fields" not in (live.config or {})


def test_completed_run_snapshot_survives_a_compiled_refresh(agent):
    from overbae.services.eval.eval_set import ensure_default_eval_set

    eval_set = ensure_default_eval_set(agent)
    frozen_fields = [
        {"name": "amount", "kind": "number"},
        {"name": "dueDate", "kind": "date"},
        {"name": "isInvoice", "kind": "boolean"},
    ]
    live = Evaluator.objects.create(
        project=agent.project,
        capability=agent,
        name="output-field-accuracy",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={
            "check": "canonical_fields",
            "fields": frozen_fields,
            "provenance": {"generator": TIER0_GENERATOR},
        },
    )
    run = EvalRun.objects.create(
        project=agent.project,
        name="past",
        eval_set=eval_set,
        status=EvalRun.Status.COMPLETED,
    )
    run_eval = RunEvaluator.objects.create(
        run=run,
        evaluator=live,
        snapshot={"name": "output-field-accuracy", "config": {"fields": frozen_fields}},
    )
    spec = _field_spec(
        ("amount", "number"),
        ("dueDate", "date"),
        ("isInvoice", "boolean"),
        ("vendor", "string"),
    )

    _merge_specs_into_set(agent, eval_set, [spec])
    run_eval.refresh_from_db()
    live.refresh_from_db()

    assert run_eval.snapshot["config"]["fields"] == frozen_fields
    assert len(live.config["fields"]) == 4
