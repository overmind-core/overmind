from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import time_machine
from conftest import TRAIN_ROWS, frozen_dataset
from factories import make_project, make_user, reconcile_training

from overbae.models import BillingService, BillingTelemetry
from overbae.models.finetuning import FinetuningJob

pytestmark = pytest.mark.django_db


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
