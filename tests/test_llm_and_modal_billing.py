from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.contrib.auth import get_user_model

from overbae.models import BillingService, BillingTelemetry, Project
from overbae.models.finetuning import FinetuningJob
from overbae.services.billing_ledger import balance_usd, charge_llm_usage
from overbae.tasks.finetuning import _transition

pytestmark = pytest.mark.django_db

User = get_user_model()


def _user(email: str) -> User:
    return User.objects.create_user(
        email=email,
        password="x",
        clerk_user_id=f"clerk_{uuid.uuid4().hex[:10]}",
    )


def _project() -> Project:
    return Project.objects.create(
        name=f"p-{uuid.uuid4().hex[:6]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
    )


def test_charge_llm_usage_bills_the_reported_cost_and_is_idempotent():
    user = _user("workshop-charge@example.com")
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


def test_charge_llm_usage_falls_back_to_catalog_pricing(monkeypatch):
    """A provider that reports no cost is priced from the model that served it,
    and cache reads must not be billed as fresh input."""
    user = _user("workshop-fallback@example.com")
    seen = {}

    def _estimate(model, inp, out, cached_tokens=None):
        seen.update(model=model, inp=inp, out=out, cached=cached_tokens)
        return 0.4

    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", _estimate)
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
    assert row is not None and row.amount == Decimal("-0.4")
    assert seen == {"model": "composer-2.5", "inp": 1000, "out": 500, "cached": 700}


def test_charge_llm_usage_skips_an_empty_turn(monkeypatch):
    user = _user("workshop-empty@example.com")
    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", lambda *a, **k: 9.99)
    assert (
        charge_llm_usage(
            user, {}, service=BillingService.DATA_WORKSHOP, idempotency_key="data-workshop:empty"
        )
        is None
    )
    assert BillingTelemetry.objects.filter(idempotency_key="data-workshop:empty").count() == 0


def test_composite_decision_billing_preserves_known_cost_when_one_attempt_is_unknown(monkeypatch):
    user = _user("decision-partial@example.com")
    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", lambda *a, **k: None)
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


def test_cached_decision_is_not_repriced_as_a_fresh_call(monkeypatch):
    user = _user("decision-cache@example.com")
    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", lambda *a, **k: 9.99)
    assert (
        charge_llm_usage(
            user,
            {"response_cost": 0, "cached": True, "prompt_tokens": 100},
            service=BillingService.DATA_WORKSHOP,
            idempotency_key="semantic-check:cached",
        )
        is None
    )


def test_provider_reported_zero_cost_is_not_repriced(monkeypatch):
    user = _user("provider-zero@example.com")
    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", lambda *a, **k: 9.99)
    assert (
        charge_llm_usage(
            user,
            {"response_cost": 0.0, "prompt_tokens": 100, "served_model": "openai/gpt-5.6-terra"},
            service=BillingService.DATA_WORKSHOP,
            idempotency_key="semantic-check:reported-zero",
        )
        is None
    )
    assert not BillingTelemetry.objects.filter(
        idempotency_key="semantic-check:reported-zero"
    ).exists()


def test_modal_terminal_transition_charges_once():
    user = _user("modal-ft@example.com")
    project = _project()
    dataset = frozen_dataset(project, TRAIN_ROWS, name="ds")
    started = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        provider=FinetuningJob.Provider.MODAL,
        status=FinetuningJob.Status.RUNNING,
        triggered_by=user,
        started_at=started,
        remote_job_id="run:fc",
    )

    with (
        patch(
            "overbae.services.finetuning_runner.ModalRunner._select_training_gpu",
            return_value=("H100", 1),
        ),
    ):
        completed = started + timedelta(hours=1)
        with patch("overbae.tasks.finetuning.timezone.now", return_value=completed):
            _transition(job, FinetuningJob.Status.SUCCEEDED)

    job.refresh_from_db()
    assert job.cost_usd == Decimal("3.9500")  # 1h × 1 × H100 $3.95/h
    assert job.cost_synced_at is not None
    rows = BillingTelemetry.objects.filter(user=user, service=BillingService.FINETUNING_JOB)
    assert rows.count() == 1
    assert rows.get().amount == Decimal("-3.9500")

    with (
        patch(
            "overbae.services.finetuning_runner.ModalRunner._select_training_gpu",
            return_value=("H100", 1),
        ),
        patch(
            "overbae.tasks.finetuning.timezone.now",
            return_value=completed + timedelta(minutes=5),
        ),
    ):
        _transition(job, FinetuningJob.Status.SUCCEEDED)

    assert (
        BillingTelemetry.objects.filter(user=user, service=BillingService.FINETUNING_JOB).count()
        == 1
    )


def test_optimizer_charge_cursor_usage(monkeypatch):
    from overbae.models import Capability
    from overbae.models.optimizer import OptimizerExperiment

    user = _user("opt-charge@example.com")
    project = _project()
    capability = Capability.objects.create(project=project, name="A", slug="a")
    exp = OptimizerExperiment.objects.create(
        project=project,
        capability=capability,
        triggered_by=user,
        cursor_usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )
    seen = {}

    def _estimate(model, inp, out, cached_tokens=None):
        seen.update(model=model, inp=inp)
        return 0.55

    monkeypatch.setattr("overbae.services.model_catalog.estimate_cost", _estimate)
    exp._charge_cursor_usage()
    exp._charge_cursor_usage()  # idempotent
    rows = BillingTelemetry.objects.filter(user=user, service=BillingService.CURSOR_AGENT)
    assert rows.count() == 1
    assert rows.get().idempotency_key == f"cursor-agent:optimizer:{exp.pk}"
    assert rows.get().amount == Decimal("-0.55")
    assert seen == {"model": "composer-2.5", "inp": 100}
