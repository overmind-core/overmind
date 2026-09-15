from __future__ import annotations

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.api.eval_serializers import (
    EvalSetMemberSerializer,
    resolve_evaluator_run_scores,
    resolve_evaluator_score_history,
)
from overbae.models import (
    Capability,
    EvalRun,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
)
from overbae.services.eval.authored import persist_specs
from overbae.services.eval.specs import (
    TIER0_GENERATOR,
    TIER1_GENERATOR,
    EvaluatorSpec,
    SpecProvenance,
)

pytestmark = pytest.mark.django_db


def _summary(name: str, mean: float) -> dict:
    """One run's rollup with a single variant scoring *name* at *mean* (0–1)."""
    return {
        "metrics": [name],
        "variants": {
            "v1": {"label": "baseline", "metrics": {name: {"mean": mean, "n": 2}}},
        },
    }


def test_resolve_scores_returns_latest_previous_delta():
    project = Project.objects.create(name="scores", slug="scores")
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Quality",
        kind="llm_judge",
        rubric_md="grade it",
    )

    # Older run first (created_at is auto, so insertion order == time order).
    EvalRun.objects.create(
        project=project,
        name="run-1",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary=_summary("Quality", 0.5),
    )
    newer = EvalRun.objects.create(
        project=project,
        name="run-2",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary=_summary("Quality", 0.8),
    )

    scores = resolve_evaluator_run_scores({str(capability.id)})
    entry = scores[(str(capability.id), evaluator.name)]

    assert entry["latest"] == pytest.approx(80.0)
    assert entry["previous"] == pytest.approx(50.0)
    assert entry["delta"] == pytest.approx(30.0)
    assert entry["latest_run_id"] == str(newer.id)


def test_resolve_scores_single_run_has_no_delta():
    project = Project.objects.create(name="single", slug="single")
    capability = Capability.objects.create(project=project, name="B", slug="b")
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
    EvalRun.objects.create(
        project=project,
        name="only",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary=_summary("Quality", 0.6),
    )

    entry = resolve_evaluator_run_scores({str(capability.id)})[(str(capability.id), "Quality")]
    assert entry["latest"] == pytest.approx(60.0)
    assert entry["previous"] is None
    assert entry["delta"] is None


def test_persist_specs_tags_only_bespoke_specs_to_capability():
    project = Project.objects.create(name="tag", slug="tag")
    capability = Capability.objects.create(project=project, name="C", slug="c")

    generic = EvaluatorSpec(
        name="generic-check",
        kind="deterministic",
        config={"check": "exact_match"},
        provenance=SpecProvenance(
            source="codebase_card.output_fields",
            generator=TIER0_GENERATOR,
            surface_area="output_contract",
        ),
    )
    bespoke = EvaluatorSpec(
        name="bespoke-judge",
        kind="llm_judge",
        rubric_md="grade overall quality",
        provenance=SpecProvenance(
            source="tier1_authoring", generator=TIER1_GENERATOR, surface_area="output_contract"
        ),
    )

    def _prepare(spec, kwargs):
        if spec.provenance.generator == TIER1_GENERATOR:
            kwargs["capability"] = capability
        return False

    created = persist_specs([generic, bespoke], project=project, prepare=_prepare)
    by_name = {ev.name: ev for ev in created}
    assert by_name["generic-check"].capability_id is None
    assert by_name["bespoke-judge"].capability_id == capability.id


def test_member_serializer_resolves_generic_evaluator_via_owning_capability():
    """Summaries key metrics by ``evaluator.name``, so the lookup must use the set's
    owning capability — ``evaluator.capability_id`` is null for library graders."""
    project = Project.objects.create(name="mem", slug="mem")
    capability = Capability.objects.create(project=project, name="A", slug="a")
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)

    bespoke = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Hallucination",
        kind="llm_judge",
        rubric_md="grade",
    )
    # Hyphenated name also guards against name-canonicalisation on match.
    generic = Evaluator.objects.create(
        project=project, capability=None, name="output-parses-as-json", kind="deterministic"
    )

    eval_set = EvalSet.objects.create(project=project, capability=capability, name="default")
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    member_bespoke = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=bespoke, role="generative"
    )
    member_generic = EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=generic, role="generative"
    )

    summary = {
        "metrics": ["Hallucination", "output-parses-as-json"],
        "variants": {
            "v1": {
                "label": "baseline",
                "metrics": {
                    "Hallucination": {"mean": 0.6, "n": 2},
                    "output-parses-as-json": {"mean": 0.95, "n": 2},
                },
            }
        },
    }
    EvalRun.objects.create(
        project=project,
        name="run",
        dataset=dataset,
        status=EvalRun.Status.COMPLETED,
        summary=summary,
    )

    scores = resolve_evaluator_run_scores({str(capability.id)})
    ctx = {"evaluator_scores": scores}

    assert EvalSetMemberSerializer(member_bespoke, context=ctx).data[
        "latest_score"
    ] == pytest.approx(60.0)
    assert EvalSetMemberSerializer(member_generic, context=ctx).data[
        "latest_score"
    ] == pytest.approx(95.0)


def test_score_history_returns_ordered_points_per_evaluator():
    project = Project.objects.create(name="hist", slug="hist")
    capability = Capability.objects.create(project=project, name="H", slug="h")
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)
    evaluator = Evaluator.objects.create(
        project=project,
        capability=capability,
        name="Quality",
        kind="llm_judge",
        rubric_md="grade it",
    )

    # created_at is auto, so insertion order == time order.
    for mean in (0.4, 0.7, 0.9):
        EvalRun.objects.create(
            project=project,
            name=f"run-{mean}",
            dataset=dataset,
            status=EvalRun.Status.COMPLETED,
            summary=_summary("Quality", mean),
        )
    # A non-terminal run must be excluded from the series.
    EvalRun.objects.create(
        project=project,
        name="running",
        dataset=dataset,
        status=EvalRun.Status.RUNNING,
        summary=_summary("Quality", 0.1),
    )

    history = resolve_evaluator_score_history(str(capability.id))

    assert len(history) == 1
    series = history[0]
    assert series["name"] == "Quality"
    assert series["evaluator_id"] == str(evaluator.id)
    scores = [point["score"] for point in series["points"]]
    assert scores == [pytest.approx(40.0), pytest.approx(70.0), pytest.approx(90.0)]
    run_ats = [point["run_at"] for point in series["points"]]
    assert run_ats == sorted(run_ats)


def test_score_history_empty_for_capability_without_runs():
    project = Project.objects.create(name="hist-empty", slug="hist-empty")
    capability = Capability.objects.create(project=project, name="E", slug="e")
    assert resolve_evaluator_score_history(str(capability.id)) == []
