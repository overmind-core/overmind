from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset

from overbae.celery import app as celery_app
from overbae.models import (
    Capability,
    EvalRun,
    EvalSample,
    Evaluator,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    Score,
)
from overbae.models import optimizer as optimizer_module
from overbae.services.datasets import rows as row_store

pytestmark = pytest.mark.django_db

_ROWS = [({"q": "2+2"}, "four"), ({"q": "capital of France"}, "paris")]


@pytest.fixture
def eager_celery(monkeypatch):
    """Run Celery tasks inline and stop the eval callback from resuming the FSM."""
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)
    # Otherwise eager mode cascades the FSM into candidate codegen (cursor_sdk).
    monkeypatch.setattr(optimizer_module.run_experiment_advance, "delay", lambda *a, **k: None)


def _setup(*, with_evaluator: bool = True):
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(
        project,
        [{"input": inp, "expected_output": expected} for inp, expected in _ROWS],
        capability=capability,
    )
    if with_evaluator:
        # Capability-scoped, non-sentinel grader -> runnable_capability_evaluators returns it.
        Evaluator.objects.create(
            project=project,
            capability=capability,
            name="ExactMatch",
            kind="deterministic",
            scope="final_output",
            config={"check": "exact_match"},
            pass_threshold=1.0,
            version=1,
        )
    experiment = OptimizerExperiment.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
        cell=dataset.active_cell,
    )
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=0)
    candidate = OptimizerCandidate.objects.create(
        experiment=experiment, iteration=iteration, candidate_index=0, is_baseline=True
    )
    return project, dataset, experiment, iteration, candidate


def _run_commands(experiment, iteration, candidate, dataset, outputs):
    for index, datapoint in enumerate(row_store.iter_rows(dataset.active_cell)):
        OptimizerCommand.objects.create(
            experiment=experiment,
            candidate=candidate,
            iteration=iteration,
            datapoint_index=index,
            input=datapoint.input,
            result={"output": outputs[index], "trace_id": f"trace-{index}"},
            output=outputs[index],
            status=OptimizerCommand.Status.PASSED,
        )


def test_optimizer_grades_candidates_with_real_evals(eager_celery):
    project, dataset, experiment, iteration, candidate = _setup()
    _run_commands(experiment, iteration, candidate, dataset, ["four", "paris"])

    started = experiment.start_iteration_eval(0)
    assert started is True, "grading should run asynchronously, not via the stub"

    candidate.refresh_from_db()
    run = candidate.eval_run
    assert run is not None, "an EvalRun must be created for each candidate"
    assert run.status == EvalRun.Status.COMPLETED
    assert Score.objects.filter(run=run, outcome=Score.Outcome.SCORED).exists()
    # Project-scoped, so it lists on the evals page query.
    assert EvalRun.objects.filter(project=project, id=run.id).exists()

    experiment.refresh_from_db()
    assert candidate.score == pytest.approx(100.0)
    assert experiment.scores["baseline"] == pytest.approx(100.0)
    assert experiment.scores["best"] == pytest.approx(100.0)
    assert experiment.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS
    assert not experiment._iteration_eval_pending(0)


def test_optimizer_eval_reflects_wrong_output(eager_celery):
    _, dataset, experiment, iteration, candidate = _setup()
    _run_commands(experiment, iteration, candidate, dataset, ["wrong", "nope"])

    experiment.start_iteration_eval(0)

    experiment.refresh_from_db()
    candidate.refresh_from_db()
    assert candidate.score == pytest.approx(0.0)
    assert experiment.scores["baseline"] == pytest.approx(0.0)


def test_optimizer_eval_includes_empty_output_rows_in_denominator(eager_celery):
    _, dataset, experiment, iteration, candidate = _setup()
    datapoints = list(row_store.iter_rows(dataset.active_cell))
    OptimizerCommand.objects.create(
        experiment=experiment,
        candidate=candidate,
        iteration=iteration,
        datapoint_index=0,
        input=datapoints[0].input,
        result={"output": "four", "trace_id": "trace-0"},
        output="four",
        status=OptimizerCommand.Status.PASSED,
    )
    OptimizerCommand.objects.create(
        experiment=experiment,
        candidate=candidate,
        iteration=iteration,
        datapoint_index=1,
        input=datapoints[1].input,
        status=OptimizerCommand.Status.FAILED,
        error="provider timeout",
    )

    experiment.start_iteration_eval(0)

    candidate.refresh_from_db()
    run = candidate.eval_run
    assert run is not None
    assert run.max_items == len(datapoints)
    assert EvalSample.objects.filter(run=run).count() == len(datapoints)
    assert candidate.score == pytest.approx(50.0)
    assert candidate.scores["coverage_rate"] == pytest.approx(0.5)
    assert candidate.scores["graded_rows"] == len(datapoints)
    assert candidate.scores["total_rows"] == len(datapoints)


def test_optimizer_falls_back_to_stub_without_evaluators(eager_celery):
    _, dataset, experiment, iteration, candidate = _setup(with_evaluator=False)
    _run_commands(experiment, iteration, candidate, dataset, ["four", "paris"])

    started = experiment.start_iteration_eval(0)
    assert started is False, "no evaluators -> synchronous stub fallback"

    candidate.refresh_from_db()
    assert candidate.eval_run is None, "no eval run without evaluators"
    candidate.refresh_from_db()
    # Stub scores any non-empty output > 0, so the loop still makes progress.
    assert candidate.score > 0
