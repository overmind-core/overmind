"""Routing for the selected incumbent benchmark, separately from matched-base evaluations."""

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
    baseline_needs_base_deploy,
    ensure_target_eval,
    resolve_baseline_model,
    sync_eval_scores,
)

pytestmark = pytest.mark.django_db

GATEWAY = "https://gateway.example.modal.run"


def _job(*, incumbent: str = "") -> FinetuningJob:
    from overbae.models import Capability, EvalSet, EvalSetMember, Evaluator

    project = Project.objects.create(name=f"bt-{uuid.uuid4().hex[:6]}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    dataset = frozen_dataset(project, TRAIN_ROWS, name="train")
    eval_ds = frozen_dataset(project, EVAL_ROWS, name="eval")
    eset = EvalSet.objects.create(project=project, capability=capability, name="set")
    EvalSetMember.objects.create(
        eval_set=eset,
        evaluator=Evaluator.objects.create(
            project=project, name="Match", kind="deterministic", config={"check": "exact_match"}
        ),
        role=EvalSetMember.Role.GENERATIVE,
    )
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
        eval_incumbent_before=True,
        eval_model_before=False,
    )


def _baseline_route(job):
    row = ensure_target_eval(job, kind="baseline")
    return row.eval_run.variants.get().model_ref if row else None


@pytest.mark.parametrize(
    ("incumbent", "model_id"),
    [("openai/gpt-5.6-sol", "openai/gpt-5.6-sol"), ("gpt-4o-mini", "openai/gpt-4o-mini")],
    ids=["frontier slug", "bare name"],
)
@override_settings(INFERENCE_API_URL=GATEWAY)
def test_a_frontier_incumbent_is_benchmarked_on_openrouter_without_a_deployment(
    incumbent, model_id
):
    job = _job(incumbent=incumbent)
    route = _baseline_route(job)

    assert route.provider == ModelRef.Provider.CUSTOM
    assert route.base_url == "https://openrouter.ai/api/v1"
    assert route.api_key_ref == "OPENROUTER_API_KEY"
    assert route.model_id == model_id
    assert baseline_needs_base_deploy(job) is False


@pytest.mark.parametrize(
    "status, url, gateway, ready",
    [
        (DeployedModel.Status.READY, "https://worker.modal.run", GATEWAY, True),
        (DeployedModel.Status.DEPLOYING, "https://worker.modal.run", GATEWAY, False),
        (DeployedModel.Status.READY, "", GATEWAY, False),
        (DeployedModel.Status.READY, "https://worker.modal.run", "", False),
    ],
)
def test_a_self_hosted_incumbent_is_benchmarked_through_the_gateway_once_ready(
    status, url, gateway, ready, settings
):
    settings.INFERENCE_API_URL = gateway
    job = _job(incumbent="ft-prev-qwen3-8b")
    DeployedModel.objects.create(
        project=job.project,
        model_id="ft-prev-qwen3-8b",
        base_model_id="Qwen/Qwen3-8B",
        status=status,
        inference_url=url,
    )
    route = _baseline_route(job)

    assert baseline_needs_base_deploy(job) is False
    if not ready:
        assert route is None
        return
    assert route.provider == ModelRef.Provider.CUSTOM
    assert route.base_url == f"{gateway}/v1"
    assert route.api_key_ref == "INFERENCE_API_KEY"
    assert route.model_id == "ft-prev-qwen3-8b"


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_no_incumbent_waits_for_a_base_model_deployment(fake_llm):
    fake_llm.catalog_models = []
    job = _job(incumbent="")

    assert _baseline_route(job) is None
    assert baseline_needs_base_deploy(job) is True


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


def _benchmarked_job(capability) -> FinetuningJob:
    return FinetuningJob.objects.create(
        project=capability.project,
        capability=capability,
        dataset=frozen_dataset(capability.project, TRAIN_ROWS, name="t"),
        base_model="Qwen/Qwen3-8B",
        provider=FinetuningJob.Provider.BASETEN,
        baseline_model="",
    )


def _served(project, model_id: str, status=DeployedModel.Status.READY) -> DeployedModel:
    return DeployedModel.objects.create(
        project=project, model_id=model_id, status=status, base_model_id="Qwen/Qwen3-8B"
    )


def test_the_live_model_does_not_become_the_benchmark():
    from overbae.models import Capability

    project = Project.objects.create(name=f"bt-{uuid.uuid4().hex[:6]}")
    capability = Capability.objects.create(
        project=project,
        name="a",
        slug=f"a-{uuid.uuid4().hex[:6]}",
        model="openai/gpt-5.6-sol",
        active_model=_served(project, "ft-live-qwen3-8b"),
    )
    assert resolve_baseline_model(_benchmarked_job(capability)) == "openai/gpt-5.6-sol"


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_a_warming_self_hosted_benchmark_waits_instead_of_calling_openrouter():
    from overbae.models import Capability

    project = Project.objects.create(name=f"bt-{uuid.uuid4().hex[:6]}")
    warming = _served(project, "ft-warming-qwen3-8b", DeployedModel.Status.WARMING)
    capability = Capability.objects.create(
        project=project,
        name="a",
        slug=f"a-{uuid.uuid4().hex[:6]}",
        active_model=warming,
        benchmark_model=warming,
    )

    job = _benchmarked_job(capability)
    assert _baseline_route(job) is None
    assert resolve_baseline_model(job) == "ft-warming-qwen3-8b"
    assert baseline_needs_base_deploy(job) is False
