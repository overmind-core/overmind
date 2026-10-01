from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import time_machine
from conftest import TRAIN_ROWS, frozen_dataset
from factories import make_project, make_user, reconcile_training

from overbae.models import BillingService, BillingTelemetry, Capability
from overbae.models.finetuning import FinetuningJob
from overbae.models.optimizer import OptimizerExperiment
from overbae.services.billing_ledger import balance_usd, charge_llm_usage
from overbae.services.optimizer_ledger import complete_experiment

pytestmark = pytest.mark.django_db


def test_charge_llm_usage_bills_the_reported_cost_and_is_idempotent():
    user = make_user("workshop-charge@example.com")
    before = balance_usd(user)
    stats = {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_tokens": 700,
        "response_cost": 1.25,
        "served_model": "openai/gpt-5.6-terra",
    }
    key = f"data-workshop:test:{uuid.uuid4()}"
    row = charge_llm_usage(
        user,
        stats,
        service=BillingService.DATA_WORKSHOP,
        idempotency_key=key,
        metadata={"source": "test"},
    )
    assert row is not None
    assert row.amount == Decimal("-1.25")
    assert row.metadata["llm_usage"]["served_model"] == "openai/gpt-5.6-terra"
    assert balance_usd(user) == before - Decimal("1.25")

    again = charge_llm_usage(
        user,
        stats,
        service=BillingService.DATA_WORKSHOP,
        idempotency_key=key,
        metadata={"source": "test"},
    )
    assert again is None
    assert BillingTelemetry.objects.filter(idempotency_key=key).count() == 1
    assert balance_usd(user) == before - Decimal("1.25")


def test_charge_llm_usage_falls_back_to_catalog_pricing(fake_llm):
    fake_llm.prices["moonshotai/kimi-k2.5"] = {
        "prompt": "0.000001",
        "completion": "0.000004",
        "input_cache_read": "0.0000001",
    }
    user = make_user("workshop-fallback@example.com")
    row = charge_llm_usage(
        user,
        {
            "prompt_tokens": 1000,
            "completion_tokens": 500,
            "cached_tokens": 700,
            "served_model": "composer-2.5",
        },
        service=BillingService.DATA_WORKSHOP,
        idempotency_key=f"data-workshop:fallback:{uuid.uuid4()}",
    )
    assert row.amount == Decimal("-0.00237")


def test_charge_llm_usage_skips_an_empty_turn():
    user = make_user("workshop-empty@example.com")
    assert (
        charge_llm_usage(
            user, {}, service=BillingService.DATA_WORKSHOP, idempotency_key="data-workshop:empty"
        )
        is None
    )
    assert not BillingTelemetry.objects.filter(idempotency_key="data-workshop:empty").exists()


def test_composite_decision_billing_preserves_known_cost_when_one_attempt_is_unknown():
    user = make_user("decision-partial@example.com")
    row = charge_llm_usage(
        user,
        {
            "response_cost": None,
            "attempts": [
                {"response_cost": None, "served_model": "typesafe/jev-1.13"},
                {"response_cost": 0.02, "served_model": "openai/gpt-5.6-terra"},
            ],
        },
        service=BillingService.DATA_WORKSHOP,
        idempotency_key="semantic-check:partial",
    )
    assert row.amount == Decimal("-0.02")
    assert row.metadata["cost_incomplete"] is True


@pytest.mark.parametrize(
    "stats",
    [
        {"response_cost": 0, "cached": True},
        {"response_cost": 0.0},
    ],
    ids=["cached decision", "provider reported zero"],
)
def test_a_zero_cost_turn_is_not_repriced_from_the_catalog(stats):
    user = make_user("zero-cost@example.com")
    key = f"semantic-check:zero:{uuid.uuid4()}"
    assert (
        charge_llm_usage(
            user,
            {**stats, "prompt_tokens": 100_000, "served_model": "openai/gpt-5.6-terra"},
            service=BillingService.DATA_WORKSHOP,
            idempotency_key=key,
        )
        is None
    )
    assert not BillingTelemetry.objects.filter(idempotency_key=key).exists()


def test_a_modal_job_that_ends_is_charged_its_gpu_hours_once(sft, fake_modal):
    user = make_user("modal-ft@example.com")
    project = make_project()
    started = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=frozen_dataset(project, TRAIN_ROWS, name="ds"),
        base_model="Qwen/Qwen3-8B",
        provider=FinetuningJob.Provider.MODAL,
        status=FinetuningJob.Status.RUNNING,
        triggered_by=user,
        started_at=started,
        remote_job_id="ft-bill:fc-bill",
    )
    sft.runs["ft-bill"] = {"status": "failed", "steps": 2}
    fake_modal.adopt("fc-bill", "sft_train", state="failed", error=RuntimeError("exit 1"))

    with time_machine.travel(started + timedelta(hours=1), tick=False):
        reconcile_training()
    with time_machine.travel(started + timedelta(hours=2), tick=False):
        reconcile_training()

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    assert job.cost_usd == Decimal("3.9500")
    [row] = BillingTelemetry.objects.filter(user=user, service=BillingService.FINETUNING_JOB)
    assert row.amount == Decimal("-3.9500")


def test_completing_an_optimizer_run_charges_its_cursor_usage(fake_llm):
    fake_llm.prices["moonshotai/kimi-k2.5"] = {"prompt": "0.000001", "completion": "0.000004"}
    user = make_user("opt-charge@example.com")
    project = make_project()
    exp = OptimizerExperiment.objects.create(
        project=project,
        capability=Capability.objects.create(project=project, name="A", slug="a"),
        triggered_by=user,
        cursor_usage={"input_tokens": 100_000, "output_tokens": 20_000, "total_tokens": 120_000},
    )

    complete_experiment(exp)

    [row] = BillingTelemetry.objects.filter(user=user, service=BillingService.CURSOR_AGENT)
    assert row.idempotency_key == f"cursor-agent:optimizer:{exp.pk}"
    assert row.amount == Decimal("-0.18")
