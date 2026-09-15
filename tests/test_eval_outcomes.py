from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import (
    Capability,
    Dataset,
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    Project,
    RunEvaluator,
    Score,
)
from overbae.services.eval import funnel as judging
from overbae.services.eval import snapshots
from overbae.services.eval.evaluators import base, gen_judge, judge
from overbae.services.eval.evaluators.base import EvalUnit, JudgeResult
from overbae.services.eval.evaluators.gen_judge import ChecklistItem, ChecklistResult
from overbae.tasks import eval as eval_tasks
from tests.factories import evaluator_stub


def _judge_outcome(verdicts: dict[str, bool], trace_id: str) -> judging.JudgeOutcome:
    return judging.JudgeOutcome(
        parsed=ChecklistResult(
            items=[ChecklistItem(id=i, verdict=v) for i, v in verdicts.items()],
            reasoning="graded",
        ),
        raw="{}",
        stats={"response_cost": 0.0, "response_ms": 0},
        judge_trace_id=trace_id,
    )


def _checklist(*ids: str) -> list[dict]:
    """Only configured items score, so a stubbed verdict needs a matching item."""
    return [{"id": i, "q": "?", "weight": 1.0} for i in ids]


def _evaluator(**overrides):
    return evaluator_stub(**{"name": "Judge", "kind": "llm_judge", **overrides})


def test_reference_free_judge_scores_in_upload_mode(monkeypatch):
    # Upload mode means output_synthesized_from_reference.
    def fake_invoke_judge(prompt, **kwargs):
        return _judge_outcome({"valid_json": True}, "t1")

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)

    unit = EvalUnit(
        trajectory={
            "final_output": '{"a": 1}',
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": True},
        },
        expected='{"a": 1}',
    )
    ev = _evaluator(
        checklist=_checklist("valid_json"),
        variable_mapping=[{"var": "output", "source": "output"}],
    )
    drafts = gen_judge.evaluate(unit, ev, {})
    assert len(drafts) == 1
    assert drafts[0].value == 1.0
    assert drafts[0].outcome == "scored"


def test_reference_consuming_judge_runs_in_synthesized_mode(monkeypatch):
    # Never force-skip an eval the user chose: runnability gating is the setup wizard's job.
    def fake_invoke_judge(prompt, **kwargs):
        return _judge_outcome({"matches_reference": True}, "t3")

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)

    unit = EvalUnit(
        trajectory={
            "final_output": "the reference answer",
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": True},
        },
        expected="the reference answer",
    )
    ev = _evaluator(
        requires_reference=True,
        checklist=_checklist("matches_reference"),
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
    )
    drafts = gen_judge.evaluate(unit, ev, {})
    assert len(drafts) == 1
    assert drafts[0].value == 1.0
    assert drafts[0].outcome == "scored"


def test_judge_guard_does_not_fire_on_genuine_model_output(monkeypatch):
    # The guard keys on the synthesis flag, not on the over-broad has_expected proxy.
    def fake_invoke_judge(prompt, **kwargs):
        return _judge_outcome({"grounded": True, "complete": False}, "t2")

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)
    unit = EvalUnit(
        trajectory={
            "final_output": "a real generated answer",
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": False},
        },
        expected="the gold answer",
    )
    ev = _evaluator(
        requires_reference=True,
        checklist=_checklist("grounded", "complete"),
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
    )
    drafts = gen_judge.evaluate(unit, ev, {})
    assert drafts[0].value == 0.5
    assert drafts[0].outcome == "scored"


def test_reference_free_trace_judge_scores_in_upload_mode(monkeypatch):
    def fake_invoke_judge(prompt, **kwargs):
        return judging.JudgeOutcome(
            parsed=JudgeResult(items=[], score=0.9, reasoning="valid json"),
            raw="{}",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="t1",
        )

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)
    unit = EvalUnit(
        trajectory={
            "final_output": '{"a": 1}',
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": True},
        },
        expected='{"a": 1}',
    )
    ev = _evaluator(variable_mapping=[{"var": "output", "source": "output"}])
    drafts = judge.evaluate(unit, ev, {})
    assert len(drafts) == 1
    assert drafts[0].value == 0.9
    assert drafts[0].outcome == "scored"


def test_reference_consuming_trace_judge_runs_in_synthesized_mode(monkeypatch):
    def fake_invoke_judge(prompt, **kwargs):
        return judging.JudgeOutcome(
            parsed=JudgeResult(items=[], score=1.0, reasoning="match"),
            raw="{}",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="t3",
        )

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)
    unit = EvalUnit(
        trajectory={
            "final_output": "the reference answer",
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": True},
        },
        expected="the reference answer",
    )
    ev = _evaluator(
        requires_reference=True,
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
    )
    drafts = judge.evaluate(unit, ev, {})
    assert len(drafts) == 1
    assert drafts[0].value == 1.0
    assert drafts[0].outcome == "scored"


def test_trace_judge_guard_does_not_fire_on_genuine_model_output(monkeypatch):
    def fake_invoke_judge(prompt, **kwargs):
        return judging.JudgeOutcome(
            parsed=JudgeResult(items=[], score=0.5, reasoning="ok"),
            raw="{}",
            stats={"response_cost": 0.0, "response_ms": 0},
            judge_trace_id="t2",
        )

    monkeypatch.setattr(judging, "invoke_judge", fake_invoke_judge)
    unit = EvalUnit(
        trajectory={
            "final_output": "a real generated answer",
            "messages": [{"role": "user", "content": "q"}],
            "metadata": {"has_expected": True, "output_synthesized_from_reference": False},
        },
        expected="the gold answer",
    )
    ev = _evaluator(
        requires_reference=True,
        variable_mapping=[
            {"var": "output", "source": "output"},
            {"var": "reference", "source": "reference"},
        ],
    )
    drafts = judge.evaluate(unit, ev, {})
    assert drafts[0].value == 0.5
    assert drafts[0].outcome == "scored"


pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _dataset(project) -> Dataset:
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    return frozen_dataset(capability.project, EVAL_ROWS, capability=capability)


def _attach(run, evaluator) -> RunEvaluator:
    return RunEvaluator.objects.create(
        run=run, evaluator=evaluator, snapshot=snapshots.build_snapshot(evaluator)
    )


def test_aggregate_run_is_idempotent_no_double_write_or_reflip():
    project = _project()
    dataset = _dataset(project)
    evaluator = Evaluator.objects.create(
        project=project,
        name="Acc",
        kind="statistical",
        scope="dataset",
        config={"metric": "accuracy"},
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        status=EvalRun.Status.RUNNING,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)

    for pred, ref in (("a", "a"), ("b", "a")):
        sample = EvalSample.objects.create(run=run, variant=variant)
        Score.objects.create(
            project=project,
            run=run,
            variant=variant,
            sample=sample,
            evaluator=evaluator,
            run_evaluator=run_eval,
            name="Acc__prediction",
            data_type="categorical",
            string_value=pred,
            value=None,
            outcome=Score.Outcome.ABSTAINED,
            scope="sample",
            sub_scores=[{"prediction": pred, "reference": ref}],
        )

    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.id)}).get()
    run.refresh_from_db()
    assert run.status == EvalRun.Status.COMPLETED
    dataset_scores = Score.objects.filter(run=run, scope="dataset", name="Acc")
    assert dataset_scores.count() == 1
    first_value = dataset_scores.first().value
    assert abs(first_value - 0.5) < 1e-9

    # The watchdog finalizes to FAILED, then a late chord callback re-fires aggregate_run.
    EvalRun.objects.filter(pk=run.pk).update(status=EvalRun.Status.FAILED)
    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.id)}).get()
    run.refresh_from_db()
    assert run.status == EvalRun.Status.FAILED
    dataset_scores = Score.objects.filter(run=run, scope="dataset", name="Acc")
    assert dataset_scores.count() == 1
    assert abs(dataset_scores.first().value - first_value) < 1e-9


def test_execute_evaluator_contains_reconstruct_failure(monkeypatch):
    project = _project()
    dataset = _dataset(project)
    evaluator = Evaluator.objects.create(
        project=project,
        name="ExactMatch",
        kind="deterministic",
        scope="final_output",
        config={"check": "exact_match"},
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        status=EvalRun.Status.RUNNING,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing")
    sample = EvalSample.objects.create(
        run=run, variant=variant, trajectory={"final_output": "x"}, expected="x"
    )

    def boom(cls, _sample):
        raise RuntimeError("reconstruct boom")

    monkeypatch.setattr(base.EvalUnit, "from_sample", classmethod(boom))

    result = eval_tasks.execute_evaluator.apply(
        kwargs={"sample_id": str(sample.id), "run_evaluator_id": str(run_eval.id)}
    ).get()

    assert result["status"] == "error"
    errors = Score.objects.filter(run=run, sample=sample, outcome=Score.Outcome.ERROR)
    assert errors.count() == 1
    assert errors.first().value is None
    # The run-level errback must NOT have fired — the failure is contained.
    run.refresh_from_db()
    assert run.status == EvalRun.Status.RUNNING


def test_completed_empty_surfaced_when_nothing_scored():
    project = _project()
    dataset = _dataset(project)
    evaluator = Evaluator.objects.create(
        project=project,
        name="ExactMatch",
        kind="deterministic",
        scope="final_output",
        config={"check": "exact_match"},
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        status=EvalRun.Status.RUNNING,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": ""})
    Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=evaluator,
        run_evaluator=run_eval,
        name="ExactMatch",
        data_type="boolean",
        value=None,
        outcome=Score.Outcome.ABSTAINED,
        scope="final_output",
    )

    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.id)}).get()
    run.refresh_from_db()
    assert run.status == EvalRun.Status.COMPLETED
    assert run.summary.get("completed_empty") is True


def test_completed_empty_false_when_a_row_scored():
    project = _project()
    dataset = _dataset(project)
    evaluator = Evaluator.objects.create(
        project=project,
        name="ExactMatch",
        kind="deterministic",
        scope="final_output",
        config={"check": "exact_match"},
        version=1,
    )
    run = EvalRun.objects.create(
        project=project,
        name="r",
        data_source="dataset",
        dataset=dataset,
        status=EvalRun.Status.RUNNING,
    )
    run_eval = _attach(run, evaluator)
    variant = EvalVariant.objects.create(run=run, label="v", mode="existing", is_baseline=True)
    sample = EvalSample.objects.create(run=run, variant=variant, trajectory={"final_output": "x"})
    Score.objects.create(
        project=project,
        run=run,
        variant=variant,
        sample=sample,
        evaluator=evaluator,
        run_evaluator=run_eval,
        name="ExactMatch",
        data_type="boolean",
        value=1.0,
        passed=True,
        outcome=Score.Outcome.SCORED,
        scope="final_output",
    )

    eval_tasks.aggregate_run.apply(kwargs={"eval_run_id": str(run.id)}).get()
    run.refresh_from_db()
    assert run.summary.get("completed_empty") is False
