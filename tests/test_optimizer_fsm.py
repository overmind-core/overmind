"""The client runs every datapoint and posts outputs; the server grades them and
parks for the client. Driven through the ledger, the eval chord and the tasks."""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from conftest import frozen_dataset
from django.urls import reverse
from factories import auth_client, make_project, make_user

from overbae.celery import app as celery_app
from overbae.models import (
    Capability,
    EvalRun,
    Evaluator,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
)
from overbae.models.optimizer import (
    optimize_capability,
    optimizer_on_candidate_eval_complete,
    run_experiment_advance,
)
from overbae.services.optimizer_create import create_optimizer_experiment
from overbae.services.optimizer_ledger import (
    complete_experiment,
    create_iteration,
    evaluate_iteration,
    post_results,
)
from overbae.tasks.optimizer_reconciler import reconcile_optimizer_experiments

pytestmark = pytest.mark.django_db

ROWS = [({"q": "2+2"}, "four"), ({"q": "capital of France"}, "paris")]
S = OptimizerExperiment.Status


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def _experiment(*, graded: bool = True, user=None) -> OptimizerExperiment:
    project = make_project(member=user) if user else make_project()
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(
        project,
        [{"input": inp, "expected_output": expected} for inp, expected in ROWS],
        capability=capability,
    )
    if graded:
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
    return create_optimizer_experiment(user=user, capability=capability, dataset=dataset)


def _run(exp, order: int, outputs: list[str | None]) -> OptimizerCandidate:
    iteration = create_iteration(
        exp, order=order, candidates=[{"candidate_index": 0, "code_path": f"+v{order}"}]
    )
    candidate = iteration.candidates.get()
    post_results(
        exp,
        [
            {
                "candidate_id": str(candidate.id),
                "datapoint_index": index,
                "success": output is not None,
                "output": output or "",
                "error": "" if output is not None else "provider timeout",
            }
            for index, output in enumerate(outputs)
        ],
    )
    return candidate


@pytest.mark.parametrize(
    ("outputs", "score", "coverage"),
    [
        (["four", "paris"], 100.0, 1.0),
        (["wrong", "nope"], 0.0, 1.0),
        (["four", None], 50.0, 0.5),
        ([None, None], 0.0, 0.0),
    ],
    ids=["right", "wrong", "one failed row", "every row failed"],
)
def test_posted_outputs_are_graded_by_a_real_eval_run(eager, outputs, score, coverage):
    exp = _experiment()
    candidate = _run(exp, 0, outputs)

    evaluate_iteration(exp, 0)

    exp.refresh_from_db()
    candidate.refresh_from_db()
    assert exp.status == S.EVALUATED_BASELINE_OUTPUTS
    assert candidate.eval_run.status == EvalRun.Status.COMPLETED
    assert candidate.eval_run.max_items == len(ROWS)
    assert candidate.score == pytest.approx(score)
    assert candidate.scores["coverage_rate"] == pytest.approx(coverage)
    assert exp.scores["baseline"] == pytest.approx(score)


def test_without_evaluators_the_stub_still_scores_the_iteration():
    exp = _experiment(graded=False)
    candidate = _run(exp, 0, ["four", "paris"])

    evaluate_iteration(exp, 0)

    exp.refresh_from_db()
    candidate.refresh_from_db()
    assert exp.status == S.EVALUATED_BASELINE_OUTPUTS
    assert candidate.eval_run is None
    assert candidate.score > 0


@pytest.mark.parametrize(
    ("best", "stalled", "score", "stalled_after", "best_after"),
    [
        (60.0, 2, 70.0, 0, 70.0),
        (90.0, 0, 90.5, 1, 90.5),
        (97.2, 1, 97.5, 2, 97.5),
        (90.0, 1, 40.0, 2, 90.0),
    ],
    ids=["improves", "within the plateau threshold", "small win still ranks", "regresses"],
)
def test_a_graded_iteration_updates_the_best_score_and_the_plateau_count(
    best, stalled, score, stalled_after, best_after
):
    exp = _experiment()
    exp.scores = {"baseline": 50.0, "best": best}
    exp.stalled_iterations = stalled
    exp.save()
    iteration = OptimizerIteration.objects.create(experiment=exp, order=1)
    candidate = OptimizerCandidate.objects.create(
        experiment=exp, iteration=iteration, code_path="+v1", score=score
    )

    exp.on_iteration_eval_complete(1)
    complete_experiment(exp)

    exp.refresh_from_db()
    assert exp.stalled_iterations == stalled_after
    assert exp.scores["best"] == pytest.approx(best_after)
    assert exp.state["winner_score"] == pytest.approx(best_after)
    if score > best:
        assert exp.state["winner_candidate_id"] == str(candidate.id)


@pytest.mark.parametrize(
    ("scores", "winner"),
    [({"baseline": 50.0, "best": 82.0}, 82.0), ({"baseline": 50.0}, 50.0), ({}, 0.0)],
    ids=["best", "baseline only", "nothing scored"],
)
def test_completion_records_the_winning_score(scores, winner):
    exp = _experiment()
    exp.scores = scores
    exp.save()
    complete_experiment(exp)
    exp.refresh_from_db()
    assert exp.status == S.COMPLETED
    assert exp.state["winner_score"] == pytest.approx(winner)


def test_completion_fails_an_iteration_the_client_never_finished():
    exp = _experiment()
    iteration = create_iteration(exp, order=1, candidates=[{"candidate_index": 0}])

    complete_experiment(exp)

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.FAILED
    assert iteration.candidates.get().status == OptimizerCandidate.Status.FAILED


def test_cancel_stops_the_running_iteration():
    user = make_user()
    exp = _experiment(user=user)
    candidate = _run(exp, 0, ["four", None])

    response = auth_client(user).post(
        reverse("optimizer-experiment-cancel", kwargs={"id": exp.id}), format="json"
    )

    assert response.status_code == 200
    assert response.data["status"] == S.CANCELLED
    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.FAILED
    assert candidate.iteration.status == OptimizerIteration.Status.FAILED
    assert set(candidate.commands.values_list("status", flat=True)) == {
        OptimizerCommand.Status.RAN,
        OptimizerCommand.Status.FAILED,
    }


@pytest.mark.parametrize("terminal", [S.COMPLETED, S.FAILED, S.CANCELLED])
def test_cancel_leaves_a_finished_experiment_alone(terminal):
    user = make_user()
    exp = _experiment(user=user)
    candidate = _run(exp, 0, ["four", "paris"])
    OptimizerExperiment.objects.filter(pk=exp.pk).update(status=terminal)

    auth_client(user).post(reverse("optimizer-experiment-cancel", kwargs={"id": exp.id}))

    exp.refresh_from_db()
    candidate.refresh_from_db()
    assert exp.status == terminal
    assert candidate.status == OptimizerCandidate.Status.COMMANDS_DONE


def test_a_grading_run_that_finishes_after_cancel_does_not_revive_the_experiment():
    user = make_user()
    exp = _experiment(user=user)
    candidate = _run(exp, 0, ["four", "paris"])
    evaluate_iteration(exp, 0)
    auth_client(user).post(reverse("optimizer-experiment-cancel", kwargs={"id": exp.id}))

    optimizer_on_candidate_eval_complete(
        experiment_id=str(exp.id), candidate_id=str(candidate.id), iteration_order=0
    )

    exp.refresh_from_db()
    assert exp.status == S.CANCELLED


def test_an_advance_during_candidate_grading_waits_for_the_grades():
    exp = _experiment()
    _run(exp, 0, ["four", "paris"])
    evaluate_iteration(exp, 0)
    _run(exp, 1, ["four", "paris"])
    evaluate_iteration(exp, 1)

    run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert exp.status == S.EVALUATING_CANDIDATE_OUTPUTS
    assert exp.failure_reason == ""


def test_a_late_advance_does_not_grade_an_iteration_still_receiving_outputs(eager):
    exp = _experiment()
    _run(exp, 0, ["four", "paris"])
    evaluate_iteration(exp, 0)
    iteration = create_iteration(exp, order=1, candidates=[{"candidate_index": 0}])

    run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert exp.status == S.ITERATING
    assert iteration.candidates.get().eval_run is None


def test_an_advance_that_raises_fails_the_experiment():
    exp = _experiment()
    OptimizerExperiment.objects.filter(pk=exp.pk).update(status=S.EVALUATING_BASELINE_OUTPUTS)

    run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert exp.status == S.FAILED
    assert exp.failure_reason.startswith("advance failed: DoesNotExist")


def test_an_advance_for_a_deleted_experiment_is_a_noop():
    run_experiment_advance(str(uuid.uuid4()))


def test_an_advance_that_cannot_take_the_lock_leaves_the_experiment(monkeypatch):
    @contextmanager
    def held(*_args, **_kwargs):
        yield False

    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", held)
    exp = _experiment()
    OptimizerExperiment.objects.filter(pk=exp.pk).update(status=S.EVALUATING_BASELINE_OUTPUTS)

    run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert exp.status == S.EVALUATING_BASELINE_OUTPUTS


def test_the_reconciler_redrives_only_experiments_waiting_on_grades(monkeypatch):
    dispatched = []
    monkeypatch.setattr(run_experiment_advance, "delay", dispatched.append)
    by_status = {}
    for status in S.values:
        exp = _experiment(graded=False)
        OptimizerExperiment.objects.filter(pk=exp.pk).update(status=status)
        by_status[status] = str(exp.id)

    reconcile_optimizer_experiments()

    assert sorted(dispatched) == sorted(
        [by_status[S.EVALUATING_BASELINE_OUTPUTS], by_status[S.EVALUATING_CANDIDATE_OUTPUTS]]
    )


def test_optimize_capability_schedules_without_dispatch(monkeypatch):
    dispatched = []
    monkeypatch.setattr(run_experiment_advance, "delay", dispatched.append)
    project = make_project()
    capability = Capability.objects.create(
        project=project, name="a", slug="a", entrypoint_fn="trigger"
    )
    dataset = frozen_dataset(
        project, [{"input": {"q": 1}, "expected_output": "one"}], capability=capability
    )

    experiment = optimize_capability(capability, dataset=dataset)

    assert experiment.status == S.SCHEDULED
    assert experiment.entrypoint == "trigger"
    assert dispatched == []
