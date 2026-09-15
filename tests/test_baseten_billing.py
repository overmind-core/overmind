from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset

from overbae.models import Project
from overbae.models.finetuning import FinetuningJob
from overbae.services.baseten_billing import (
    EARLIEST_QUERYABLE,
    accumulate_training_costs,
    project_finetuning_costs,
    sync_finetuning_job_costs,
    usage_summary_chunks,
)

pytestmark = pytest.mark.django_db


def _training_summary(*items: dict) -> dict:
    return {
        "training_usage": {"breakdown": list(items), "total": 0, "credits_used": 0, "subtotal": 0}
    }


def _item(resource_id: str, *, subtotal: float, minutes: int, kind: str = "TRAINING_JOB") -> dict:
    return {
        "billable_resource": {"id": resource_id, "kind": kind, "is_deleted": False},
        "subtotal": subtotal,
        "minutes": minutes,
    }


def _job(**kwargs) -> FinetuningJob:
    project = kwargs.pop("project", None) or Project.objects.create(
        name=f"billing-{uuid.uuid4().hex[:6]}",
        slug=f"billing-{uuid.uuid4().hex[:8]}",
    )
    dataset = kwargs.pop("dataset", None) or frozen_dataset(project, TRAIN_ROWS, name="ds")
    defaults = {
        "project": project,
        "dataset": dataset,
        "base_model": "Qwen/Qwen3-8B",
        "provider": FinetuningJob.Provider.BASETEN,
        "remote_job_id": "btproj:job-a",
        "status": FinetuningJob.Status.SUCCEEDED,
    }
    defaults.update(kwargs)
    return FinetuningJob.objects.create(**defaults)


def test_chunks_never_exceed_31_days():
    since = datetime(2026, 1, 1, tzinfo=UTC)
    until = datetime(2026, 4, 15, tzinfo=UTC)
    chunks = usage_summary_chunks(since, until)
    assert chunks
    for start, end in chunks:
        assert end - start <= timedelta(days=31)
        assert (end - start).days <= 30 or end == until


def test_chunks_clamp_to_earliest_queryable():
    since = datetime(2025, 6, 1, tzinfo=UTC)
    until = datetime(2026, 1, 10, tzinfo=UTC)
    chunks = usage_summary_chunks(since, until)
    assert chunks[0][0] == EARLIEST_QUERYABLE


def test_chunks_empty_when_until_before_since():
    assert (
        usage_summary_chunks(
            datetime(2026, 2, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )
        == []
    )


def test_accumulate_sums_same_resource_across_chunks():
    s1 = _training_summary(_item("job-a", subtotal=3.25, minutes=30))
    s2 = _training_summary(_item("job-a", subtotal=6.50, minutes=60))
    costs = accumulate_training_costs([s1, s2])
    assert costs["job-a"].cost_usd == Decimal("9.75")
    assert costs["job-a"].billed_minutes == 90


def test_accumulate_ignores_non_training_kinds():
    s = _training_summary(
        _item("dep-1", subtotal=100, minutes=10, kind="MODEL_DEPLOYMENT"),
        _item("job-a", subtotal=1.5, minutes=15),
    )
    costs = accumulate_training_costs([s])
    assert set(costs) == {"job-a"}
    assert costs["job-a"].cost_usd == Decimal("1.5")


def test_sync_matches_remote_job_id_suffix():
    job = _job(remote_job_id="proj123:job-a")
    together = _job(
        project=job.project,
        dataset=job.dataset,
        provider=FinetuningJob.Provider.TOGETHER_AI,
        remote_job_id="together-ft-xyz",
    )
    other = _job(
        project=job.project,
        dataset=job.dataset,
        remote_job_id="proj123:job-other",
    )

    summary = _training_summary(
        _item("job-a", subtotal=12.34, minutes=100),
        _item("unknown", subtotal=9, minutes=9),
    )

    with (
        patch("overbae.services.baseten_billing._api_key", return_value="test-key"),
        patch(
            "overbae.services.baseten_billing.fetch_usage_summary",
            return_value=summary,
        ) as fetch,
        patch(
            "overbae.services.baseten_billing.usage_summary_chunks",
            return_value=[(datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 15, tzinfo=UTC))],
        ),
    ):
        result = sync_finetuning_job_costs(
            since=datetime(2026, 6, 1, tzinfo=UTC),
            until=datetime(2026, 6, 15, tzinfo=UTC),
        )

    assert result["matched"] == 1
    assert result["updated"] == 1
    fetch.assert_called_once()

    job.refresh_from_db()
    assert job.cost_usd == Decimal("12.3400")
    assert job.billed_minutes == 100
    assert job.cost_synced_at is not None

    together.refresh_from_db()
    assert together.cost_usd is None
    other.refresh_from_db()
    assert other.cost_usd is None


def test_sync_noop_without_api_key():
    _job(remote_job_id="proj:job-a")
    with patch("overbae.services.baseten_billing._api_key", return_value=""):
        result = sync_finetuning_job_costs(
            since=datetime(2026, 6, 1, tzinfo=UTC),
            until=datetime(2026, 6, 15, tzinfo=UTC),
        )
    assert result == {"fetched": 0, "matched": 0, "updated": 0, "chunks": 0}


def test_sync_accumulates_across_real_chunks():
    job = _job(remote_job_id="p:long-job")

    def fake_fetch(*, start, end):
        mid = datetime(2026, 2, 1, tzinfo=UTC)
        if end <= mid:
            return _training_summary(_item("long-job", subtotal=2.0, minutes=20))
        return _training_summary(_item("long-job", subtotal=3.0, minutes=30))

    with (
        patch("overbae.services.baseten_billing._api_key", return_value="k"),
        patch(
            "overbae.services.baseten_billing.fetch_usage_summary",
            side_effect=fake_fetch,
        ),
    ):
        result = sync_finetuning_job_costs(
            since=datetime(2026, 1, 1, tzinfo=UTC),
            until=datetime(2026, 3, 1, tzinfo=UTC),
        )

    assert result["chunks"] >= 2
    job.refresh_from_db()
    assert job.cost_usd == Decimal("5.0000")
    assert job.billed_minutes == 50


def test_project_finetuning_costs_sums_only_that_project():
    p1 = Project.objects.create(name="p1", slug=f"p1-{uuid.uuid4().hex[:8]}")
    p2 = Project.objects.create(name="p2", slug=f"p2-{uuid.uuid4().hex[:8]}")
    ds1 = frozen_dataset(p1, TRAIN_ROWS, name="d1")
    ds2 = frozen_dataset(p2, TRAIN_ROWS, name="d2")

    _job(project=p1, dataset=ds1, cost_usd=Decimal("10.5"), billed_minutes=50, remote_job_id="a:1")
    _job(project=p1, dataset=ds1, cost_usd=Decimal("2.25"), billed_minutes=10, remote_job_id="a:2")
    _job(project=p1, dataset=ds1, remote_job_id="a:3")
    _job(
        project=p2,
        dataset=ds2,
        cost_usd=Decimal("100"),
        billed_minutes=999,
        remote_job_id="b:1",
    )
    _job(
        project=p1,
        dataset=ds1,
        provider=FinetuningJob.Provider.TOGETHER_AI,
        cost_usd=Decimal("999"),
        remote_job_id="tog-1",
    )

    agg = project_finetuning_costs(p1.id)
    assert agg["job_count"] == 3  # baseten only
    assert agg["jobs_with_cost"] == 2
    assert agg["jobs_missing_cost"] == 1
    assert agg["cost_usd"] == Decimal("12.75")
    assert agg["billed_minutes"] == 60
