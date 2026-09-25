"""Client-write ledger: template, candidates, outputs, scores — no cursor_sdk."""

from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset

from overbae.models import (
    Capability,
    OptimizerCommand,
    OptimizerExperiment,
    Project,
)
from overbae.services.optimizer_create import create_optimizer_experiment
from overbae.services.optimizer_ledger import (
    complete_experiment,
    create_iteration,
    evaluate_iteration,
    post_results,
    set_command_template,
)

pytestmark = pytest.mark.django_db


def _capability_with_eval_dataset():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(
        project, [{"input": {"q": 1}, "expected_output": "one"}], capability=capability
    )
    return capability, dataset


def test_post_candidate_outputs_and_stub_score():
    capability, dataset = _capability_with_eval_dataset()
    exp = create_optimizer_experiment(user=None, capability=capability, dataset=dataset)
    set_command_template(exp, "echo __DATAPOINT_INPUT__")
    iteration = create_iteration(
        exp,
        order=0,
        name="Baseline",
        candidates=[{"candidate_index": 0, "code_path": "", "is_baseline": True}],
    )
    candidate = iteration.candidates.get()
    post_results(
        exp,
        [
            {
                "candidate_id": str(candidate.id),
                "datapoint_index": 0,
                "success": True,
                "output": "ok",
                "trace_id": "a" * 32,
            }
        ],
    )
    cmd = OptimizerCommand.objects.get(experiment=exp, candidate=candidate)
    assert cmd.status == OptimizerCommand.Status.RAN
    assert cmd.output == "ok"

    exp = evaluate_iteration(exp, 0)
    exp.refresh_from_db()
    assert exp.status == OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS
    assert "baseline" in (exp.scores or {})


def test_order_zero_model_candidate_defaults_to_challenger():
    capability, dataset = _capability_with_eval_dataset()
    exp = create_optimizer_experiment(user=None, capability=capability, dataset=dataset)
    iteration = create_iteration(
        exp,
        order=0,
        candidates=[
            {"candidate_index": 0, "code_path": ""},
            {"candidate_index": 1, "target_model": "openai/gpt-5-mini"},
        ],
    )
    plain, model_bearing = list(iteration.candidates.order_by("candidate_index"))
    assert plain.is_baseline is True
    assert model_bearing.is_baseline is False


def test_model_comparison_stub_accumulates_by_model(monkeypatch):
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    capability, dataset = _capability_with_eval_dataset()
    models = ["openai/gpt-5-mini", "anthropic/claude-sonnet-4"]
    exp = create_optimizer_experiment(
        user=None,
        capability=capability,
        dataset=dataset,
        mode=OptimizerExperiment.Mode.MODEL_COMPARISON,
        model_ids=models,
        openrouter_key_source=OptimizerExperiment.OpenRouterKeySource.LOCAL,
    )
    models = list(exp.model_ids)
    for order, model in enumerate(models):
        iteration = create_iteration(
            exp,
            order=order,
            name=model,
            candidates=[{"candidate_index": 0, "target_model": model, "is_baseline": False}],
        )
        candidate = iteration.candidates.get()
        post_results(
            exp,
            [
                {
                    "candidate_id": str(candidate.id),
                    "datapoint_index": 0,
                    "success": True,
                    "output": f"out-{order}",
                }
            ],
        )
        exp = evaluate_iteration(exp, order)

    exp.refresh_from_db()
    scores = exp.scores or {}
    assert set(scores.get("by_model") or {}) == set(models)
    assert scores.get("models") == scores["by_model"]
    assert "baseline" not in scores
    exp = complete_experiment(exp)
    assert exp.status == OptimizerExperiment.Status.COMPLETED
    assert exp.state["model_comparison"]["selected_winner"] in models
