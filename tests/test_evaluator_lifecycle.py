from __future__ import annotations

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import (
    Capability,
    EvalRun,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Project,
    RunEvaluator,
)
from overbae.services.datasets import lifecycle
from overbae.services.datasets.rows import row as _dataset_row
from overbae.services.eval import snapshots
from overbae.services.eval.binding_check import (
    synthetic_row_from_card,
    validate_specs_on_synthetic_row,
)
from overbae.services.eval.eval_set import (
    _drop_behaviour_generative_members,
    _drop_stale_generated_trace_members,
    _enforce_behaviour_coverage,
    _refresh_reused_spec,
)
from overbae.services.eval.grounding import EvalGroundingContext
from overbae.services.eval.rubric_compiler import schema_path_errors
from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance

CARD = {
    "task": "Extract fields",
    "output_fields": {
        "category": {"type": "text"},
        "total": {"type": "number", "tolerance": 0.5},
    },
    "output_schema": {"required_keys": ["category", "total"]},
    "trajectory_map": [
        {
            "id": "happy-path",
            "routing": "default",
            "claim": "extracts fields",
            "steps": [{"title": "Parse input", "anchors": ["capability.parse"]}],
            "anchors": ["capability.parse"],
            "terminal": {"kind": "returns_artifact"},
        }
    ],
}


def _spec(name: str, **overrides) -> EvaluatorSpec:
    payload = {
        "name": name,
        "display_name": overrides.pop("display_name", name.title()),
        "description": "d",
        "kind": overrides.pop("kind", "deterministic"),
        "scope": "final_output",
        "score_type": "boolean",
        "config": overrides.pop("config", {"check": "regex", "pattern": "x"}),
        "provenance": SpecProvenance(
            source="dataset_card.failure_modes[0]",
            data_version="v",
            codebase_commit="",
            generator=overrides.pop("generator", "card_compiler@v1"),
            surface_area="failure_mode",
        ),
    }
    payload.update(overrides)
    return EvaluatorSpec.model_validate(payload)


@pytest.fixture
def capability(db):
    project = Project.objects.create(name="authoring", slug="authoring")
    return Capability.objects.create(
        project=project,
        name="a",
        slug="a",
        improvement_metadata={"capability_card": CARD},
    )


@pytest.mark.django_db
def test_display_name_and_content_hash_reach_evaluator_kwargs():
    spec = _spec("no-hallucination", display_name="No failure: hallucination")
    kwargs = spec.to_evaluator_kwargs()
    assert kwargs["display_name"] == "No failure: hallucination"
    assert kwargs["config"]["content_hash"]


@pytest.mark.django_db
def test_refresh_reused_spec_bumps_version_on_grading_change(capability):
    spec = _spec("gate-a")
    row = Evaluator.objects.create(
        project=capability.project, capability=capability, **spec.to_evaluator_kwargs()
    )
    v0 = row.version

    _refresh_reused_spec(row, spec)
    row.refresh_from_db()
    assert row.version == v0

    changed = _spec("gate-a", config={"check": "regex", "pattern": "y"})
    _refresh_reused_spec(row, changed)
    row.refresh_from_db()
    assert row.version == v0 + 1
    assert row.config["content_hash"] != spec.to_evaluator_kwargs()["config"]["content_hash"]


@pytest.mark.django_db
def test_snapshot_carries_display_name_and_version(capability):
    row = Evaluator.objects.create(
        project=capability.project,
        capability=capability,
        name="n",
        display_name="Human name",
        kind="deterministic",
        scope="final_output",
        score_type="boolean",
        config={"check": "regex", "pattern": "x", "content_hash": "abc"},
    )
    snap = snapshots.build_snapshot(row)
    assert snap["display_name"] == "Human name"
    assert snap["version"] == row.version
    assert snap["config"]["content_hash"] == "abc"


def test_schema_path_errors_rejects_unknown_leaves():
    ok = "Compare {{output.total}} to {{reference.total}}."
    bad = "Compare {{output.nonexistent_leaf}} to {{reference.total}}."
    assert schema_path_errors(ok, CARD) == []
    errs = schema_path_errors(bad, CARD)
    assert errs and "nonexistent_leaf" in errs[0]


def test_synthetic_row_covers_card_fields():
    row = synthetic_row_from_card(CARD)
    assert set(row) >= {"category", "total"}


def test_synthetic_validation_flags_crashing_check():
    good = _spec("gate-ok", config={"check": "regex", "pattern": "safe"})
    bad = _spec("gate-bad")
    bad.config["pattern"] = "("  # injected post-validation: the spec validator rejects it
    failures = validate_specs_on_synthetic_row([good, bad], CARD)
    names = {f["name"] for f in failures}
    assert "gate-bad" in names
    assert "gate-ok" not in names


@pytest.mark.django_db
def test_stale_drop_only_removes_same_generator(capability):
    eval_set = EvalSet.objects.create(
        project=capability.project, capability=capability, name="Default"
    )

    def _member(name: str, generator: str, *, behaviour: bool = False):
        config: dict = {"provenance": {"generator": generator}}
        if behaviour:
            config["behaviour"] = {"behaviour_key": "gone", "role": "outcome"}
        ev = Evaluator.objects.create(
            project=capability.project,
            capability=capability,
            name=name,
            kind="llm_judge",
            scope="final_output",
            score_type="numeric",
            config=config,
        )
        return EvalSetMember.objects.create(
            eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.TRACE_SCORING
        )

    _member("behaviour-old-judge", "card_compiler@v1", behaviour=True)
    _member("tier1-trace-judge", "semantic_recommender@v2")
    _member("tool-vocabulary-selection", "card_compiler@v1")
    specs = [_spec("behaviour-new-judge", generator="card_compiler@v1")]

    dropped = _drop_stale_generated_trace_members(eval_set, specs)
    assert dropped == 2
    kept = {m.evaluator.name for m in eval_set.members.select_related("evaluator")}
    assert kept == {"tier1-trace-judge"}


@pytest.mark.django_db
def test_behaviour_generative_drop_spares_hand_authored_members(capability):
    """A resync must never silently delete a user's task-scoped generative
    membership — only machine-authored (card_compiler/tier1) behaviour judges
    are trace-only."""
    eval_set = EvalSet.objects.create(
        project=capability.project, capability=capability, name="Default"
    )

    def _generative_member(name: str, config: dict) -> EvalSetMember:
        ev = Evaluator.objects.create(
            project=capability.project,
            capability=capability,
            name=name,
            kind="llm_judge",
            scope="trajectory",
            score_type="numeric",
            config=config,
        )
        return EvalSetMember.objects.create(
            eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.GENERATIVE
        )

    _generative_member(
        "machine-behaviour-judge",
        {
            "behaviour": {"behaviour_key": "happy", "role": "outcome"},
            "provenance": {"generator": "card_compiler@v1"},
        },
    )
    hand_authored = _generative_member(
        "user-behaviour-judge", {"behaviour": {"behaviour_key": "happy", "role": "outcome"}}
    )
    no_behaviour = _generative_member("plain-generative-judge", {})

    dropped = _drop_behaviour_generative_members(eval_set)

    assert dropped == 1
    kept_ids = set(eval_set.members.values_list("id", flat=True))
    assert kept_ids == {hand_authored.id, no_behaviour.id}


@pytest.mark.django_db
def test_coverage_enforcement_records_gaps(capability):
    eval_set = EvalSet.objects.create(
        project=capability.project, capability=capability, name="Default"
    )
    grounding = EvalGroundingContext(codebase_card=CARD)
    coverage = _enforce_behaviour_coverage(capability, eval_set, grounding)
    assert coverage["complete"] is False
    capability.refresh_from_db()
    persisted = capability.improvement_metadata["eval_coverage"]
    assert persisted["complete"] is False
    assert "happy-path" in persisted["behaviours"]


@pytest.mark.django_db
def test_non_baseline_run_copies_baseline_snapshots(capability, django_assert_num_queries):
    from overbae.models import FinetuningJob, FinetuningJobEval
    from overbae.services.finetuning_eval import _copy_baseline_snapshots

    dataset = frozen_dataset(capability.project, EVAL_ROWS, capability=capability, name="d")
    ev = Evaluator.objects.create(
        project=capability.project,
        capability=capability,
        name="grader",
        display_name="Grader",
        kind="deterministic",
        scope="final_output",
        score_type="boolean",
        config={"check": "regex", "pattern": "old", "content_hash": "h1"},
    )
    baseline_run = EvalRun.objects.create(
        project=capability.project, name="baseline", dataset=dataset
    )
    baseline_snap = snapshots.build_snapshot(ev)
    RunEvaluator.objects.create(run=baseline_run, evaluator=ev, snapshot=baseline_snap, order=0)

    job = FinetuningJob.objects.create(
        project=capability.project,
        capability=capability,
        name="ft",
        base_model="m",
        provider=FinetuningJob.Provider.TOGETHER_AI,
        dataset=dataset,
    )
    FinetuningJobEval.objects.create(
        job=job, kind=FinetuningJobEval.Kind.BASELINE, eval_run=baseline_run, model_id="m"
    )

    ev.config = {"check": "regex", "pattern": "new", "content_hash": "h2"}
    ev.version += 1
    ev.save(update_fields=["config", "version"])
    ckpt_run = EvalRun.objects.create(
        project=capability.project,
        name="ckpt",
        dataset=dataset,
        cell=dataset.active_cell,
    )
    RunEvaluator.objects.create(
        run=ckpt_run, evaluator=ev, snapshot=snapshots.build_snapshot(ev), order=0
    )

    copied = _copy_baseline_snapshots(job, ckpt_run)
    assert copied == 1
    ckpt_re = ckpt_run.run_evaluators.get()
    assert ckpt_re.snapshot == baseline_snap


@pytest.mark.django_db
def test_behaviour_rollup_groups_by_datapoint_key(capability):
    from overbae.models import EvalSample, EvalVariant, Score
    from overbae.tasks.eval import _behaviour_rollup

    dataset = frozen_dataset(
        capability.project,
        [{"input": {}, **{"behaviour_key": "happy-path"}}, {"input": {}, **{}}],
        capability=capability,
        name="d",
    )
    dp1 = _row(dataset, 0)
    dp2 = _row(dataset, 1)
    run = EvalRun.objects.create(
        project=capability.project, name="r", dataset=dataset, cell=dataset.active_cell
    )
    variant = EvalVariant.objects.create(run=run, label="v", order=0)
    s1 = EvalSample.objects.create(run=run, variant=variant, row_index=dp1.index)
    s2 = EvalSample.objects.create(run=run, variant=variant, row_index=dp2.index)
    Score.objects.create(
        project=capability.project,
        run=run,
        sample=s1,
        name="m",
        scope="sample",
        value=0.8,
        outcome=Score.Outcome.SCORED,
    )
    Score.objects.create(
        project=capability.project,
        run=run,
        sample=s2,
        name="m",
        scope="sample",
        value=0.2,
        outcome=Score.Outcome.SCORED,
    )

    rollup = _behaviour_rollup(run)
    assert list(rollup) == ["happy-path"]
    assert rollup["happy-path"]["n_samples"] == 1
    assert rollup["happy-path"]["mean"] == 0.8


@pytest.mark.django_db
def test_pinned_train_version_blocks_delete_but_eval_holdout_does_not(capability):
    from overbae.models import FinetuningJob

    train = frozen_dataset(capability.project, EVAL_ROWS, capability=capability, name="train")
    holdout = frozen_dataset(capability.project, EVAL_ROWS, capability=capability, name="holdout")
    FinetuningJob.objects.create(
        project=capability.project,
        capability=capability,
        name="ft",
        base_model="m",
        provider=FinetuningJob.Provider.TOGETHER_AI,
        dataset=train,
        cell=train.active_cell,
        eval_dataset=holdout,
        status=FinetuningJob.Status.RUNNING,
    )
    assert len(lifecycle.usage(train.active_cell)["finetuning_jobs"]) == 1
    assert "used by runs" in lifecycle.delete_blocked_reason(train)
    assert len(lifecycle.usage(holdout.active_cell)["finetuning_jobs"]) == 0
    assert lifecycle.delete_blocked_reason(holdout) == ""


def _row(dataset, index):
    return _dataset_row(dataset.active_cell, index)
