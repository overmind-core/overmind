"""Baseten training cost sync from the management billing API.

Fetches ``GET /v1/billing/usage_summary`` (max 31-day windows), matches
``TRAINING_JOB`` breakdown items to :class:`FinetuningJob` via
``remote_job_id`` suffix, and stores raw per-job compute USD
(``cost_usd`` / ``billed_minutes``). Account-level credits are ignored.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import requests
from django.conf import settings
from django.db.models import Count, Q, Sum
from django.utils import timezone

from overbae.models import BillingService
from overbae.models.finetuning import FinetuningJob
from overbae.services.billing_ledger import charge_credits

logger = logging.getLogger(__name__)

_API_BASE = "https://api.baseten.co"
EARLIEST_QUERYABLE = datetime(2026, 1, 1, tzinfo=UTC)
# Baseten rejects ranges longer than 31 days inclusive — use 30-day steps.
_MAX_CHUNK = timedelta(days=30)
_TRAINING_JOB = "TRAINING_JOB"


@dataclass(frozen=True)
class _ResourceCost:
    cost_usd: Decimal
    billed_minutes: int


def _api_key() -> str:
    return getattr(settings, "BASETEN_API_KEY", "") or ""


def _parse_money(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _baseten_job_id(remote_job_id: str) -> str | None:
    """Extract Baseten job id from ``{project_id}:{job_id}``."""
    if not remote_job_id or ":" not in remote_job_id:
        return None
    _, job_id = remote_job_id.split(":", 1)
    return job_id or None


def usage_summary_chunks(
    since: datetime,
    until: datetime,
) -> list[tuple[datetime, datetime]]:
    """Split ``[since, until]`` into ≤31-day windows, clamped to earliest date."""
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    since = max(since, EARLIEST_QUERYABLE)
    if until <= since:
        return []
    chunks: list[tuple[datetime, datetime]] = []
    cursor = since
    while cursor < until:
        end = min(cursor + _MAX_CHUNK, until)
        chunks.append((cursor, end))
        cursor = end
    return chunks


def fetch_usage_summary(*, start: datetime, end: datetime) -> dict[str, Any]:
    key = _api_key()
    if not key:
        raise RuntimeError("BASETEN_API_KEY is not configured in Django settings.")
    resp = requests.get(
        f"{_API_BASE}/v1/billing/usage_summary",
        headers={"Authorization": f"Bearer {key}"},
        params={
            "start_date": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end_date": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def accumulate_training_costs(summaries: list[dict[str, Any]]) -> dict[str, _ResourceCost]:
    """Sum training breakdown items across summaries, keyed by billable_resource.id."""
    totals: dict[str, list[Decimal | int]] = defaultdict(lambda: [Decimal("0"), 0])
    for summary in summaries:
        training = summary.get("training_usage") or {}
        for item in training.get("breakdown") or []:
            resource = item.get("billable_resource") or {}
            if resource.get("kind") != _TRAINING_JOB:
                continue
            rid = resource.get("id")
            if not rid:
                continue
            totals[str(rid)][0] += _parse_money(item.get("subtotal"))
            totals[str(rid)][1] += int(item.get("minutes") or 0)
    return {
        rid: _ResourceCost(cost_usd=vals[0], billed_minutes=int(vals[1]))
        for rid, vals in totals.items()
    }


def _eligible_jobs_qs():
    return FinetuningJob.objects.filter(
        provider=FinetuningJob.Provider.BASETEN,
    ).exclude(remote_job_id="")


def sync_finetuning_job_costs(*, since: datetime, until: datetime | None = None) -> dict[str, int]:
    until = until or timezone.now()
    if not _api_key():
        logger.warning("BASETEN_API_KEY unset — skipping finetuning cost sync")
        return {"fetched": 0, "matched": 0, "updated": 0, "chunks": 0}

    chunks = usage_summary_chunks(since, until)
    if not chunks:
        return {"fetched": 0, "matched": 0, "updated": 0, "chunks": 0}

    summaries: list[dict[str, Any]] = []
    for start, end in chunks:
        try:
            summaries.append(fetch_usage_summary(start=start, end=end))
        except requests.RequestException:
            logger.exception(
                "Baseten usage_summary failed for %s → %s",
                start.isoformat(),
                end.isoformat(),
            )
            raise

    costs = accumulate_training_costs(summaries)
    if not costs:
        return {"fetched": 0, "matched": 0, "updated": 0, "chunks": len(chunks)}

    jobs = list(_eligible_jobs_qs().select_related("triggered_by"))
    by_remote: dict[str, list] = defaultdict(list)
    for job in jobs:
        rid = _baseten_job_id(job.remote_job_id)
        if rid:
            by_remote[rid].append(job)

    now = timezone.now()
    matched = 0
    updated = 0
    for resource_id, cost in costs.items():
        targets = by_remote.get(resource_id) or []
        if not targets:
            continue
        matched += len(targets)
        for job in targets:
            previous = job.cost_usd or Decimal("0")
            job.cost_usd = cost.cost_usd
            job.billed_minutes = cost.billed_minutes
            job.cost_synced_at = now
            job.save(update_fields=["cost_usd", "billed_minutes", "cost_synced_at", "updated_at"])
            delta = cost.cost_usd - previous
            if delta > 0 and job.triggered_by_id:
                charge_credits(
                    job.triggered_by,
                    delta,
                    BillingService.FINETUNING_JOB,
                    project_id=job.project_id,
                    idempotency_key=f"finetuning-job:{job.id}:{cost.cost_usd}",
                    metadata={
                        "job_id": str(job.id),
                        "cost_usd": str(cost.cost_usd),
                        "delta": str(delta),
                    },
                )
            updated += 1

    return {
        "fetched": len(costs),
        "matched": matched,
        "updated": updated,
        "chunks": len(chunks),
    }


def sync_recent_finetuning_costs(*, lookback_days: int = 14) -> dict[str, int]:
    """Covers at least ``lookback_days``, and extends back to the earliest start of any
    active or recently completed job so multi-week runs are not undercounted.
    """
    now = timezone.now()
    floor = max(now - timedelta(days=lookback_days), EARLIEST_QUERYABLE)
    terminal = {
        FinetuningJob.Status.SUCCEEDED,
        FinetuningJob.Status.FAILED,
        FinetuningJob.Status.CANCELLED,
    }
    qs = _eligible_jobs_qs().filter(
        Q(completed_at__gte=floor)
        | Q(started_at__gte=floor)
        | Q(created_at__gte=floor)
        | ~Q(status__in=terminal)
    )
    starts: list[datetime] = []
    for job in qs.only("started_at", "created_at").iterator():
        start = job.started_at or job.created_at
        if start is not None:
            starts.append(start)
    since = min([floor, *starts]) if starts else floor
    since = max(since, EARLIEST_QUERYABLE)
    return sync_finetuning_job_costs(since=since, until=now)


def sync_job_cost_window(job) -> dict[str, int] | None:
    if getattr(job, "provider", None) != FinetuningJob.Provider.BASETEN:
        return None
    if not getattr(job, "remote_job_id", None):
        return None
    since = job.started_at or job.created_at or timezone.now()
    return sync_finetuning_job_costs(since=since, until=timezone.now())


def project_finetuning_costs(project_id: UUID | str) -> dict[str, Any]:
    qs = FinetuningJob.objects.filter(
        project_id=project_id,
        provider=FinetuningJob.Provider.BASETEN,
    )
    agg = qs.aggregate(
        job_count=Count("id"),
        jobs_with_cost=Count("id", filter=Q(cost_usd__isnull=False)),
        cost_usd=Sum("cost_usd"),
        billed_minutes=Sum("billed_minutes"),
    )
    job_count = agg["job_count"] or 0
    jobs_with_cost = agg["jobs_with_cost"] or 0
    return {
        "project_id": str(project_id),
        "job_count": job_count,
        "jobs_with_cost": jobs_with_cost,
        "jobs_missing_cost": job_count - jobs_with_cost,
        "cost_usd": agg["cost_usd"],
        "billed_minutes": agg["billed_minutes"] or 0,
    }
