"""Baseline = the capability's production incumbent model, not the base model of the family."""

from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset
from django.test import override_settings

from overbae.models import (
    DeployedModel,
    EvalRun,
    FinetuningJob,
    FinetuningJobEval,
    ModelRef,
    Project,
)
from overbae.services.finetuning_eval import (
    baseline_base_deploy_model,
    resolve_baseline_model,
    resolve_baseline_route,
    sync_eval_scores,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("openrouter_catalog")]

GATEWAY = "https://gateway.example.modal.run"


def _job(*, incumbent: str = "") -> FinetuningJob:
    from overbae.models import Capability, EvalSet

    project = Project.objects.create(name=f"bt-{uuid.uuid4().hex[:6]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(project, TRAIN_ROWS, name="train")
    eval_ds = frozen_dataset(project, EVAL_ROWS, name="eval")
    eset = EvalSet.objects.create(project=project, capability=capability, name="set")
    return FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
        eval_dataset=eval_ds,
        eval_set=eset,
        base_model="Qwen/Qwen3-8B",
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        baseline_model=incumbent,
    )


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_frontier_incumbent_routes_via_openrouter_and_skips_deploy():
    job = _job(incumbent="openai/gpt-5.6-sol")
    route = resolve_baseline_route(job)

    assert route.kind == "openrouter"
    assert route.provider == ModelRef.Provider.CUSTOM
    assert route.base_url == "https://openrouter.ai/api/v1"
    assert route.api_key_ref == "OPENROUTER_API_KEY"
    assert route.model_id == "openai/gpt-5.6-sol"
    assert route.ready is True
    # A frontier model has no weights to serve — never Modal-deploy it.
    assert baseline_base_deploy_model(job) is None
    assert "?" not in route.base_url


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_provider_less_incumbent_routes_via_openrouter():
    """A bare model name is still an incumbent — it must not fall back to the base FT model."""
    job = _job(incumbent="gpt-4o-mini")
    route = resolve_baseline_route(job)

    assert route.kind == "openrouter"
    assert route.model_id == "openai/gpt-4o-mini"  # vendor prefix inferred
    assert route.base_url == "https://openrouter.ai/api/v1"
    assert route.api_key_ref == "OPENROUTER_API_KEY"
    assert route.ready is True
    assert baseline_base_deploy_model(job) is None


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_self_hosted_incumbent_routes_via_gateway():
    job = _job(incumbent="ft-prev-qwen3-8b")
    DeployedModel.objects.create(
        project=job.project,
        model_id="ft-prev-qwen3-8b",
        base_model_id="Qwen/Qwen3-8B",
        status=DeployedModel.Status.READY,
        inference_url="https://worker.modal.run?model_path=x",
    )
    route = resolve_baseline_route(job)

    assert route.kind == "gateway"
    assert route.provider == ModelRef.Provider.CUSTOM
    assert route.base_url == f"{GATEWAY}/v1"  # gateway, NOT the worker url
    assert route.api_key_ref == "INFERENCE_API_KEY"
    assert route.model_id == "ft-prev-qwen3-8b"
    assert baseline_base_deploy_model(job) is None


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_no_incumbent_scores_the_open_weights_base_via_openrouter():
    """The untouched base is the before-comparison when the capability has no model,
    and an open-weights base OpenRouter serves needs no GPU deploy."""
    job = _job(incumbent="")
    route = resolve_baseline_route(job)

    assert route.kind == "openrouter"
    assert route.requested == "Qwen/Qwen3-8B"
    assert route.model_id == "qwen/qwen3-8b"  # the served slug, not the HF casing
    assert "Base model" in route.label
    assert route.ready is True
    assert baseline_base_deploy_model(job) is None


def test_resolve_baseline_prefers_snapshot_over_live_capability():
    from overbae.models import Capability

    project = Project.objects.create(name=f"bt-{uuid.uuid4().hex[:6]}")
    capability = Capability.objects.create(
        project=project,
        name="a",
        slug=f"a-{uuid.uuid4().hex[:6]}",
        model="anthropic/claude-sonnet-5",
    )
    dataset = frozen_dataset(project, TRAIN_ROWS, name="t")
    job = FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        provider=FinetuningJob.Provider.BASETEN,
        baseline_model="",  # not snapshotted yet → live capability model
    )
    assert resolve_baseline_model(job) == "anthropic/claude-sonnet-5"

    job.baseline_model = "openai/gpt-5.6-sol"
    assert resolve_baseline_model(job) == "openai/gpt-5.6-sol"


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_delta_is_finetuned_minus_incumbent():
    job = _job(incumbent="openai/gpt-5.6-sol")
    baseline_run = EvalRun.objects.create(
        project=job.project,
        name="baseline",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.COMPLETED,
        summary={"variants": {"v": {"metrics": {"m": {"mean": 0.60}}}}},
    )
    final_run = EvalRun.objects.create(
        project=job.project,
        name="final",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.COMPLETED,
        summary={"variants": {"v": {"metrics": {"m": {"mean": 0.75}}}}},
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=baseline_run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="openai/gpt-5.6-sol",  # the incumbent, NOT job.base_model
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=final_run,
        kind=FinetuningJobEval.Kind.FINAL,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="ft-x-qwen3-8b",
    )

    sync_eval_scores(job)

    baseline = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.BASELINE)
    final = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.FINAL)
    assert baseline.aggregate_score == pytest.approx(0.60)
    assert baseline.model_id == "openai/gpt-5.6-sol"
    assert final.aggregate_score == pytest.approx(0.75)
    assert final.baseline_delta == pytest.approx(0.15)
