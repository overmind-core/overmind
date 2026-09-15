"""Optimizer FSM — client posts outputs; server scores. No Cursor codegen."""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from conftest import EVAL_ROWS, frozen_dataset

from overbae.models import (
    Capability,
    Dataset,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
)
from overbae.models import optimizer as optimizer_module
from overbae.tasks.optimizer_reconciler import reconcile_optimizer_experiments

pytestmark = pytest.mark.django_db


@contextmanager
def _noop_lock(*_args, **_kwargs):
    """Stand-in for acquire_task_lock that grants the lock without touching Redis."""
    yield True


@contextmanager
def _held_lock(*_args, **_kwargs):
    """Stand-in for acquire_task_lock simulating another worker already holding it."""
    yield False


def _make_experiment(**kwargs) -> OptimizerExperiment:
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    defaults = dict(
        project=project,
        capability=capability,
        status=OptimizerExperiment.Status.BASELINE,
        command_template="echo hi",
        scores={},
    )
    defaults.update(kwargs)
    return OptimizerExperiment.objects.create(**defaults)


def _make_dataset(experiment: OptimizerExperiment, num_datapoints: int = 2) -> Dataset:
    dataset = frozen_dataset(
        experiment.project,
        [{"input": {"q": order}, "expected_output": "a"} for order in range(num_datapoints)],
        capability=experiment.capability,
    )
    experiment.dataset = dataset
    experiment.cell = dataset.active_cell
    experiment.save(update_fields=["dataset", "cell"])
    return dataset


def _make_iteration(experiment, order=1) -> OptimizerIteration:
    return OptimizerIteration.objects.create(
        experiment=experiment,
        order=order,
        name=f"iter{order}",
        status=OptimizerIteration.Status.RUNNING_COMMANDS,
    )


def _make_candidate(experiment, iteration) -> OptimizerCandidate:
    return OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        status=OptimizerCandidate.Status.RUNNING_COMMANDS,
    )


def _make_command(experiment, iteration, candidate, **kwargs) -> OptimizerCommand:
    return OptimizerCommand.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=kwargs.get("datapoint_index", 0),
        status=OptimizerCommand.Status.RUNNING,
    )


def test_candidate_recompute_running_when_any_pending(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=1,
        status=OptimizerCommand.Status.PENDING,
    )

    candidate.recompute_status()

    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.RUNNING_COMMANDS


def test_candidate_recompute_commands_done_when_all_ran(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    candidate = _make_candidate(exp, iteration)
    for i in range(3):
        OptimizerCommand.objects.create(
            experiment=exp,
            iteration=iteration,
            candidate=candidate,
            datapoint_index=i,
            status=OptimizerCommand.Status.RAN,
        )

    candidate.recompute_status()

    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.COMMANDS_DONE


def test_candidate_recompute_evaluated_when_all_evaluated(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    candidate = _make_candidate(exp, iteration)
    for i in range(2):
        OptimizerCommand.objects.create(
            experiment=exp,
            iteration=iteration,
            candidate=candidate,
            datapoint_index=i,
            status=OptimizerCommand.Status.EVALUATED,
        )

    candidate.recompute_status()

    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.EVALUATED


def test_iteration_recompute_running_when_candidate_running():
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        status=OptimizerCandidate.Status.RUNNING_COMMANDS,
    )

    iteration.recompute_status()

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.RUNNING_COMMANDS


def test_iteration_recompute_evaluating_when_all_commands_done():
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        status=OptimizerCandidate.Status.COMMANDS_DONE,
    )
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=1,
        status=OptimizerCandidate.Status.COMMANDS_DONE,
    )

    iteration.recompute_status()

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.EVALUATING


def test_iteration_recompute_evaluated_when_all_candidates_evaluated():
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    for i in range(3):
        OptimizerCandidate.objects.create(
            experiment=exp,
            iteration=iteration,
            candidate_index=i,
            status=OptimizerCandidate.Status.EVALUATED,
            score=float(70 + i),
        )

    iteration.recompute_status()

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.EVALUATED


def test_generate_next_baseline_blocked_while_commands_pending(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.PENDING,
    )

    before_status = exp.status
    exp.generate_next()

    assert exp.status == before_status


def test_generate_next_baseline_evaluates_and_advances_to_iterating(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)

    # Patch at the iteration level: OptimizerCandidate.evaluate() scores through a
    # ThreadPoolExecutor and SQLite cannot take the concurrent writes.
    def _fake_iter_evaluate(self):
        for c in self.candidates.all():
            c.score = 75.0
            c.status = OptimizerCandidate.Status.EVALUATED
            c.save(update_fields=["score", "status"])
        self.scores = {"best": 75.0}
        self.status = OptimizerIteration.Status.EVALUATED
        self.save(update_fields=["status", "scores"])

    monkeypatch.setattr(OptimizerIteration, "evaluate", _fake_iter_evaluate)

    exp = _make_experiment()
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    cmd = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )
    cmd  # noqa: B018

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS
    assert "baseline" in exp.scores
    assert "best" in exp.scores


def test_generate_next_iterating_blocked_while_commands_running(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    exp.status = OptimizerExperiment.Status.ITERATING
    exp.save()
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RUNNING,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.ITERATING
    assert exp.current_iteration == 0


def test_generate_next_iterating_evaluates_and_increments(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)

    # Iteration-level patch again: the executor inside evaluate() locks SQLite.
    def _fake_iter_evaluate(self):
        for c in self.candidates.all():
            c.score = 80.0
            c.status = OptimizerCandidate.Status.EVALUATED
            c.save(update_fields=["score", "status"])
        self.scores = {"best": 80.0}
        self.status = OptimizerIteration.Status.EVALUATED
        self.save(update_fields=["status", "scores"])

    monkeypatch.setattr(OptimizerIteration, "evaluate", _fake_iter_evaluate)

    exp = _make_experiment()
    exp.status = OptimizerExperiment.Status.ITERATING
    exp.scores = {"baseline": 60.0, "best": 60.0}
    exp.save()

    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    cmd = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )
    cmd  # noqa: B018

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.current_iteration == 1
    assert exp.scores.get("best") == pytest.approx(80.0)
    assert exp.stalled_iterations == 0


@pytest.mark.parametrize(
    "terminal_status",
    [
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
        OptimizerExperiment.Status.PAUSED,
    ],
)
def test_generate_next_is_noop_for_terminal_statuses(monkeypatch, terminal_status):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment(status=terminal_status, current_iteration=3)

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == terminal_status
    assert exp.current_iteration == 3


def test_generate_next_parks_on_scheduled_without_command_template():
    exp = _make_experiment(status=OptimizerExperiment.Status.SCHEDULED, command_template="")

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.SCHEDULED
    assert exp.command_template == ""


def test_generate_next_baseline_parks_when_iteration_missing():
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE, command_template="run.sh")
    _make_dataset(exp, num_datapoints=3)
    assert exp.iterations.filter(order=0).count() == 0

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.BASELINE
    assert exp.iterations.filter(order=0).count() == 0


def test_generate_next_baseline_parks_in_evaluating_when_evaluators_runnable(monkeypatch):
    monkeypatch.setattr(OptimizerExperiment, "_has_runnable_evaluators", lambda self: True)
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE)
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS


def test_generate_next_evaluating_baseline_blocked_while_eval_pending():
    exp = _make_experiment(status=OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS)
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    exp.state = {"eval_pending": {str(candidate.id): "some-run-id"}}
    exp.save(update_fields=["state"])

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS


def test_generate_next_evaluating_baseline_stays_parked_when_eval_starts(monkeypatch):
    calls = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "start_iteration_eval",
        lambda self, order: (calls.append(order), True)[1],
    )
    exp = _make_experiment(status=OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS)

    exp.generate_next()

    exp.refresh_from_db()
    assert calls == [0]
    assert exp.status == OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS


def test_generate_next_evaluating_baseline_falls_back_to_stub_scoring(monkeypatch):
    monkeypatch.setattr(OptimizerExperiment, "start_iteration_eval", lambda self, order: False)
    recorded = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "_record_baseline_scores",
        lambda self, iteration: recorded.append(iteration.id),
    )
    exp = _make_experiment(status=OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS)
    baseline = _make_iteration(exp, order=0)

    exp.generate_next()

    exp.refresh_from_db()
    assert recorded == [baseline.id]
    assert exp.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS


def test_generate_next_evaluated_baseline_parks_for_client():
    exp = _make_experiment(status=OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS)
    _make_iteration(exp, order=0)

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS


def test_generate_next_iterating_parks_when_next_iteration_missing():
    exp = _make_experiment(
        status=OptimizerExperiment.Status.ITERATING,
        current_iteration=0,
        num_candidates_per_iteration=2,
        command_template="run __CANDIDATE_ID__",
    )
    _make_dataset(exp, num_datapoints=2)
    _make_iteration(exp, order=0)

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.ITERATING
    assert exp.iterations.filter(order=1).count() == 0


def test_generate_next_iterating_parks_at_iteration_cap():
    exp = _make_experiment(
        status=OptimizerExperiment.Status.ITERATING,
        current_iteration=5,
        num_iterations=5,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.ITERATING
    assert exp.iterations.filter(order=6).count() == 0


def test_generate_next_iterating_parks_in_evaluating_candidate_outputs(monkeypatch):
    monkeypatch.setattr(OptimizerExperiment, "_has_runnable_evaluators", lambda self: True)
    exp = _make_experiment(status=OptimizerExperiment.Status.ITERATING, current_iteration=0)
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS
    assert exp.current_iteration == 0  # not incremented yet — still waiting on grading


def test_generate_next_iterating_marks_evaluated_when_iteration_already_scored(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment(
        status=OptimizerExperiment.Status.ITERATING, current_iteration=0, scores={"best": 90.0}
    )
    iteration = _make_iteration(exp, order=1)
    iteration.status = OptimizerIteration.Status.EVALUATED
    iteration.save(update_fields=["status"])

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
    assert exp.current_iteration == 0


def test_generate_next_evaluating_candidate_blocked_while_eval_pending():
    exp = _make_experiment(
        status=OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS,
        current_iteration=0,
    )
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    exp.state = {"eval_pending": {str(candidate.id): "run-id"}}
    exp.save(update_fields=["state"])

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS


def test_advance_during_candidate_eval_survives_bumped_current_iteration(monkeypatch):
    """The ledger bumps current_iteration to the order under evaluation before the
    evals finish; a reconciler advance in that window must wait on that iteration,
    not crash looking up current_iteration + 1."""
    exp = _make_experiment(
        status=OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS,
        current_iteration=1,
    )
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    exp.state = {"eval_pending": {str(candidate.id): "run-id"}}
    exp.save(update_fields=["state"])

    exp.advance()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS


def test_generate_next_evaluating_candidate_falls_back_to_stub(monkeypatch):
    monkeypatch.setattr(OptimizerExperiment, "start_iteration_eval", lambda self, order: False)
    recorded = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "_record_iteration_scores",
        lambda self, iteration: recorded.append(iteration.order),
    )
    exp = _make_experiment(
        status=OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS, current_iteration=0
    )
    _make_iteration(exp, order=1)

    exp.generate_next()

    exp.refresh_from_db()
    assert recorded == [1]
    assert exp.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS


def test_generate_next_evaluated_candidate_outputs_parks_for_client():
    exp = _make_experiment(
        status=OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS,
        current_iteration=0,
        num_iterations=5,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.current_iteration == 0
    assert exp.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS


def test_should_continue_iterating_true_by_default():
    exp = _make_experiment(current_iteration=0, num_iterations=5, stalled_iterations=0)
    assert exp.should_continue_iterating() is True


def test_should_continue_iterating_false_at_iteration_cap():
    exp = _make_experiment(current_iteration=5, num_iterations=5)
    assert exp.should_continue_iterating() is False


def test_should_continue_iterating_false_when_stalled_too_long():
    exp = _make_experiment(
        current_iteration=1,
        num_iterations=5,
        max_iterations_without_improvement=3,
        stalled_iterations=3,
    )
    assert exp.should_continue_iterating() is False


def test_record_iteration_scores_resets_stalled_on_improvement():
    exp = _make_experiment(scores={"best": 60.0}, stalled_iterations=2)
    iteration = _make_iteration(exp, order=1)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        score=70.0,
        status=OptimizerCandidate.Status.EVALUATED,
    )

    exp._record_iteration_scores(iteration)

    assert exp.stalled_iterations == 0
    assert exp.scores["best"] == pytest.approx(70.0)
    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.COMPLETED


def test_record_iteration_scores_increments_stalled_without_improvement():
    exp = _make_experiment(scores={"best": 90.0}, stalled_iterations=0)
    iteration = _make_iteration(exp, order=1)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        score=90.5,  # improvement smaller than MIN_ITERATION_IMPROVEMENT
        status=OptimizerCandidate.Status.EVALUATED,
    )

    exp._record_iteration_scores(iteration)

    assert exp.stalled_iterations == 1
    assert exp.scores["best"] == pytest.approx(90.5)


def test_small_improvement_updates_winner_without_resetting_plateau():
    exp = _make_experiment(scores={"baseline": 90.0, "best": 97.2}, stalled_iterations=1)
    iteration = _make_iteration(exp, order=2)
    winner = OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        code_path="+better",
        score=97.5,
        status=OptimizerCandidate.Status.EVALUATED,
    )

    exp._record_iteration_scores(iteration)
    exp.generate_winner()

    assert exp.stalled_iterations == 2
    assert exp.scores["best"] == pytest.approx(97.5)
    assert exp.state["winner_score"] == pytest.approx(97.5)
    assert exp.state["winner_candidate_id"] == str(winner.id)


def test_generate_winner_uses_best_score():
    exp = _make_experiment(scores={"baseline": 50.0, "best": 82.0})
    exp.generate_winner()
    assert exp.state["winner_score"] == pytest.approx(82.0)


def test_generate_winner_falls_back_to_baseline_when_no_best():
    exp = _make_experiment(scores={"baseline": 50.0})
    exp.generate_winner()
    assert exp.state["winner_score"] == pytest.approx(50.0)


def test_generate_winner_defaults_to_zero_when_no_scores():
    exp = _make_experiment(scores={})
    exp.generate_winner()
    assert exp.state["winner_score"] == 0.0


def test_advance_stops_once_status_and_iteration_are_stable(monkeypatch):
    exp = _make_experiment(status=OptimizerExperiment.Status.COMPLETED, current_iteration=2)
    calls = []
    original = OptimizerExperiment.generate_next

    def _spy(self):
        calls.append(1)
        return original(self)

    monkeypatch.setattr(OptimizerExperiment, "generate_next", _spy)

    exp.advance()

    # COMPLETED is terminal: advance() stops after the first no-op generate_next.
    assert len(calls) == 1


def test_advance_parks_on_evaluated_candidate_outputs():
    exp = _make_experiment(
        status=OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS,
        current_iteration=4,
        num_iterations=5,
        scores={"baseline": 50.0, "best": 88.0},
    )

    exp.advance()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
    assert exp.current_iteration == 4


def test_advance_caps_iterations_and_returns_without_raising(monkeypatch):
    exp = _make_experiment(status=OptimizerExperiment.Status.ITERATING, current_iteration=0)
    calls = []

    def _always_changes(self):
        calls.append(1)
        self.current_iteration += 1
        self.save(update_fields=["current_iteration"])

    monkeypatch.setattr(OptimizerExperiment, "generate_next", _always_changes)

    exp.advance()

    assert len(calls) == 10_000


def test_cancel_marks_pending_and_running_children_failed():
    exp = _make_experiment(status=OptimizerExperiment.Status.ITERATING)
    iteration = _make_iteration(exp, order=1)
    iteration.status = OptimizerIteration.Status.RUNNING_COMMANDS
    iteration.save(update_fields=["status"])
    candidate = _make_candidate(exp, iteration)
    candidate.status = OptimizerCandidate.Status.RUNNING_COMMANDS
    candidate.save(update_fields=["status"])
    running_cmd = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RUNNING,
    )
    done_cmd = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=1,
        status=OptimizerCommand.Status.EVALUATED,
    )

    exp.cancel()

    exp.refresh_from_db()
    iteration.refresh_from_db()
    candidate.refresh_from_db()
    running_cmd.refresh_from_db()
    done_cmd.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.CANCELLED
    assert iteration.status == OptimizerIteration.Status.FAILED
    assert candidate.status == OptimizerCandidate.Status.FAILED
    assert running_cmd.status == OptimizerCommand.Status.FAILED
    assert running_cmd.error == "experiment cancelled"
    # Already-terminal children are left alone.
    assert done_cmd.status == OptimizerCommand.Status.EVALUATED


@pytest.mark.parametrize(
    "terminal_status",
    [
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
    ],
)
def test_cancel_is_noop_when_already_terminal(terminal_status):
    exp = _make_experiment(status=terminal_status)
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    cmd = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.PENDING,
    )

    exp.cancel()

    exp.refresh_from_db()
    cmd.refresh_from_db()
    assert exp.status == terminal_status
    assert cmd.status == OptimizerCommand.Status.PENDING


def test_start_iteration_eval_stub_when_no_run_created(monkeypatch):
    monkeypatch.setattr(OptimizerExperiment, "build_candidate_eval_run", lambda self, cand: None)
    stubbed = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "_evaluate_commands_stub",
        lambda self, it: stubbed.append(it.id),
    )
    exp = _make_experiment()
    iteration = _make_iteration(exp, order=0)
    _make_candidate(exp, iteration)

    result = exp.start_iteration_eval(0)

    assert result is False
    assert stubbed == [iteration.id]
    assert exp._iteration_eval_pending(0) is False


def test_start_iteration_eval_dispatches_async_when_run_created(monkeypatch):
    class _FakeRun:
        id = uuid.uuid4()

    fake_run = _FakeRun()
    monkeypatch.setattr(
        OptimizerExperiment, "build_candidate_eval_run", lambda self, cand: fake_run
    )
    dispatched = []
    monkeypatch.setattr(
        "overbae.tasks.eval.dispatch_preseeded_eval_run",
        lambda run, on_complete=None: dispatched.append((run, on_complete)),
    )
    exp = _make_experiment()
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)

    result = exp.start_iteration_eval(0)

    exp.refresh_from_db()
    assert result is True
    assert len(dispatched) == 1
    assert dispatched[0][0] is fake_run
    assert exp._iteration_eval_pending(0) is True
    assert exp.state["eval_pending"][str(candidate.id)] == str(fake_run.id)


def test_on_iteration_eval_complete_records_baseline_and_clears_pending():
    exp = _make_experiment(state={"eval_pending": {"0": "run-id"}})
    _make_iteration(exp, order=0)  # eval_run stays None: skip real folding

    exp.on_iteration_eval_complete(0)

    exp.refresh_from_db()
    assert exp._iteration_eval_pending(0) is False
    assert exp.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS
    assert "baseline" in exp.scores


def test_on_iteration_eval_complete_records_candidate_iteration():
    exp = _make_experiment(state={"eval_pending": {"1": "run-id"}}, scores={"best": 0.0})
    iteration = _make_iteration(exp, order=1)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        score=77.0,
        status=OptimizerCandidate.Status.EVALUATED,
    )

    exp.on_iteration_eval_complete(1)

    exp.refresh_from_db()
    assert exp._iteration_eval_pending(1) is False
    assert exp.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
    assert str(iteration.order) in exp.scores


def test_optimizer_on_candidate_eval_complete_folds_scores_and_resumes_fsm(monkeypatch):
    folded = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "_fold_candidate_eval_scores",
        lambda self, cand: folded.append(cand.id) or 0.0,
    )
    completed = []
    monkeypatch.setattr(
        OptimizerExperiment,
        "on_iteration_eval_complete",
        lambda self, order: completed.append((self.id, order)),
    )
    dispatched = []
    monkeypatch.setattr(
        optimizer_module.run_experiment_advance, "delay", lambda exp_id: dispatched.append(exp_id)
    )
    exp = _make_experiment()
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    exp.state = {"eval_pending": {str(candidate.id): "run-id"}}
    exp.save(update_fields=["state"])

    optimizer_module.optimizer_on_candidate_eval_complete(
        experiment_id=str(exp.id), candidate_id=str(candidate.id), iteration_order=0
    )

    exp.refresh_from_db()
    assert folded == [candidate.id]
    assert completed == [(exp.id, 0)]
    assert dispatched == [str(exp.id)]
    assert exp.state["eval_pending"] == {}


def test_optimizer_on_candidate_eval_complete_missing_experiment_is_noop(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        optimizer_module.run_experiment_advance, "delay", lambda exp_id: dispatched.append(exp_id)
    )

    optimizer_module.optimizer_on_candidate_eval_complete(
        experiment_id=str(uuid.uuid4()), candidate_id=str(uuid.uuid4()), iteration_order=0
    )

    assert dispatched == []


def test_optimize_capability_creates_scheduled_experiment_without_dispatch(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        optimizer_module.run_experiment_advance, "delay", lambda exp_id: dispatched.append(exp_id)
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}", entrypoint_fn="trigger"
    )
    dataset = frozen_dataset(project, EVAL_ROWS, capability=capability)

    experiment = optimizer_module.optimize_capability(capability, dataset=dataset)

    assert experiment.status == OptimizerExperiment.Status.SCHEDULED
    assert experiment.entrypoint == "trigger"
    assert experiment.current_iteration == 0
    assert dispatched == []


# No optimizer timeout reaper exists — ``OptimizerCommand.timeout`` is never
# read. These tests pin the current gap so a future reaper lands as an
# intentional change.


def test_stale_running_command_blocks_fsm_forever_no_timeout_reaper():
    """Command age is never checked — generate_next only tests for PENDING/RUNNING."""
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE)
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RUNNING,
    )

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.BASELINE


def test_reconciler_redrive_does_not_unstick_a_stale_running_command(monkeypatch):
    """BASELINE is client-driven — reconciler only re-drives EVALUATING_*."""
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)
    monkeypatch.setattr(
        "overbae.models.optimizer.run_experiment_advance.delay",
        lambda exp_id: optimizer_module.run_experiment_advance(exp_id),
    )
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE)
    iteration = _make_iteration(exp, order=0)
    candidate = _make_candidate(exp, iteration)
    OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.RUNNING,
    )

    result = reconcile_optimizer_experiments()
    exp.refresh_from_db()

    assert result == {"dispatched": 0}
    assert exp.status == OptimizerExperiment.Status.BASELINE


def test_advance_hitting_iteration_cap_does_not_mark_experiment_failed(monkeypatch):
    """advance() gives up silently at the cap: no exception, no FAILED, no failure_reason."""
    exp = _make_experiment(status=OptimizerExperiment.Status.ITERATING, current_iteration=0)

    def _always_changes(self):
        self.current_iteration += 1
        self.save(update_fields=["current_iteration"])

    monkeypatch.setattr(OptimizerExperiment, "generate_next", _always_changes)

    exp.advance()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.ITERATING
    assert exp.failure_reason == ""
    assert exp.current_iteration == 10_000


def test_reconciler_dispatches_only_evaluating_experiments(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)
    dispatched = []
    monkeypatch.setattr(
        "overbae.models.optimizer.run_experiment_advance.delay",
        lambda exp_id: dispatched.append(exp_id),
    )
    evaluating = [
        _make_experiment(status=s)
        for s in (
            OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS,
            OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS,
        )
    ]
    for s in (
        OptimizerExperiment.Status.SCHEDULED,
        OptimizerExperiment.Status.ITERATING,
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
        OptimizerExperiment.Status.PAUSED,
    ):
        _make_experiment(status=s)

    result = reconcile_optimizer_experiments()

    assert result == {"dispatched": 2}
    assert set(dispatched) == {str(e.id) for e in evaluating}


def test_reconciler_is_a_noop_when_nothing_to_drive(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)
    dispatched = []
    monkeypatch.setattr(
        "overbae.models.optimizer.run_experiment_advance.delay",
        lambda exp_id: dispatched.append(exp_id),
    )
    _make_experiment(status=OptimizerExperiment.Status.COMPLETED)

    result = reconcile_optimizer_experiments()

    assert result == {"dispatched": 0}
    assert dispatched == []


def test_reconciler_skips_entirely_when_lock_already_held(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _held_lock)
    dispatched = []
    monkeypatch.setattr(
        "overbae.models.optimizer.run_experiment_advance.delay",
        lambda exp_id: dispatched.append(exp_id),
    )
    _make_experiment(status=OptimizerExperiment.Status.ITERATING)

    result = reconcile_optimizer_experiments()

    assert result == {"status": "skipped", "reason": "previous_task_still_running"}
    assert dispatched == []


def test_run_experiment_advance_skips_advance_when_lock_not_acquired(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _held_lock)
    advanced = []
    monkeypatch.setattr(OptimizerExperiment, "advance", lambda self: advanced.append(self.id))
    exp = _make_experiment(status=OptimizerExperiment.Status.ITERATING)

    optimizer_module.run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert advanced == []  # another worker holds the lock — we must not race it
    assert exp.status == OptimizerExperiment.Status.ITERATING


def test_candidate_recompute_rolls_up_to_commands_done_even_when_all_failed():
    """``recompute_status`` folds FAILED in with RAN/EVALUATED, so an all-errored
    candidate still proceeds to ``evaluate()`` instead of failing the batch.
    """
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    candidate = _make_candidate(exp, iteration)
    for i in range(3):
        OptimizerCommand.objects.create(
            experiment=exp,
            iteration=iteration,
            candidate=candidate,
            datapoint_index=i,
            status=OptimizerCommand.Status.FAILED,
            error="capability crashed",
        )

    candidate.recompute_status()

    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.COMMANDS_DONE


def test_iteration_recompute_evaluating_even_when_every_candidate_failed():
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    OptimizerCandidate.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate_index=0,
        status=OptimizerCandidate.Status.FAILED,
    )

    iteration.recompute_status()

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.EVALUATING


def test_iteration_with_no_candidates_never_leaves_pending():
    """recompute_status early-returns on an empty candidate set, so a zero-candidate
    iteration sits at PENDING with nothing in the FSM noticing.
    """
    exp = _make_experiment()
    iteration = OptimizerIteration.objects.create(experiment=exp, order=1)
    assert iteration.status == OptimizerIteration.Status.PENDING

    iteration.recompute_status()

    iteration.refresh_from_db()
    assert iteration.status == OptimizerIteration.Status.PENDING


def test_candidate_with_no_commands_never_leaves_pending():
    exp = _make_experiment()
    iteration = _make_iteration(exp)
    candidate = OptimizerCandidate.objects.create(
        experiment=exp, iteration=iteration, candidate_index=0
    )
    assert candidate.status == OptimizerCandidate.Status.PENDING

    candidate.recompute_status()

    candidate.refresh_from_db()
    assert candidate.status == OptimizerCandidate.Status.PENDING


def test_generate_next_scheduled_is_noop_without_dataset(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment(status=OptimizerExperiment.Status.SCHEDULED)
    assert exp.dataset is None

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.SCHEDULED


def test_generate_next_baseline_parks_when_dataset_missing():
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE)
    assert exp.dataset is None

    exp.generate_next()

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.BASELINE


def test_run_experiment_advance_parks_when_baseline_has_no_iteration(monkeypatch):
    monkeypatch.setattr("overbae.tasks.utils.task_lock.acquire_task_lock", _noop_lock)
    exp = _make_experiment(status=OptimizerExperiment.Status.BASELINE)
    assert exp.dataset is None

    optimizer_module.run_experiment_advance(str(exp.id))

    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.BASELINE
    assert exp.failure_reason == ""


def test_generate_next_fails_experiment_when_iteration_failed(monkeypatch):
    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    exp.status = OptimizerExperiment.Status.ITERATING
    exp.save()
    iteration = _make_iteration(exp, order=1)
    iteration.status = OptimizerIteration.Status.FAILED
    iteration.save(update_fields=["status"])
    candidate = _make_candidate(exp, iteration)
    candidate.status = OptimizerCandidate.Status.FAILED
    candidate.save(update_fields=["status"])
    pending = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.PENDING,
    )

    exp.generate_next()

    exp.refresh_from_db()
    pending.refresh_from_db()
    candidate.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.FAILED
    assert "iteration 1 failed" in exp.failure_reason
    assert pending.status == OptimizerCommand.Status.FAILED
    assert candidate.status == OptimizerCandidate.Status.FAILED


def test_complete_experiment_stops_pending_children(monkeypatch):
    from overbae.services.optimizer_ledger import complete_experiment

    monkeypatch.setattr("overbae.models.optimizer.run_experiment_advance.delay", lambda *a: None)
    exp = _make_experiment()
    exp.status = OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
    exp.scores = {"baseline": 60.0, "best": 75.0}
    exp.save()
    iteration = _make_iteration(exp, order=1)
    candidate = _make_candidate(exp, iteration)
    pending = OptimizerCommand.objects.create(
        experiment=exp,
        iteration=iteration,
        candidate=candidate,
        datapoint_index=0,
        status=OptimizerCommand.Status.PENDING,
    )

    complete_experiment(exp)

    exp.refresh_from_db()
    pending.refresh_from_db()
    candidate.refresh_from_db()
    iteration.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.COMPLETED
    assert pending.status == OptimizerCommand.Status.FAILED
    assert candidate.status == OptimizerCandidate.Status.FAILED
    assert iteration.status == OptimizerIteration.Status.FAILED
