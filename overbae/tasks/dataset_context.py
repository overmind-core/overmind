from __future__ import annotations

import logging
import re
from typing import Any

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name="overbae.tasks.dataset_context.refresh_dataset_context",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def refresh_dataset_context(self, dataset_id: str) -> dict:  # noqa: ANN001
    """Dispatched by ``eval.context_extractor.enqueue_refresh``; the API keeps serving the stale
    context until this lands."""
    from overbae.models import Dataset
    from overbae.services.eval.context_extractor import extract_and_save

    try:
        dataset = Dataset.objects.get(id=dataset_id)
    except Dataset.DoesNotExist:
        logger.warning("refresh_dataset_context: dataset %s not found", dataset_id)
        return {"status": "not_found", "dataset_id": dataset_id}

    try:
        ctx = extract_and_save(dataset)
        if ctx.refresh_error:
            type(ctx).objects.filter(pk=ctx.pk).update(refresh_error="")
        return {
            "status": "ok",
            "dataset_id": dataset_id,
            "extracted_at": ctx.extracted_at.isoformat(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "refresh_dataset_context failed for dataset %s: %s",
            dataset_id,
            exc,
            exc_info=True,
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            from overbae.models import DatasetContext

            DatasetContext.objects.filter(dataset_id=dataset_id).update(
                refresh_error=f"Context refresh failed: {exc}"[:1000]
            )
            return {"status": "failed", "dataset_id": dataset_id, "error": str(exc)}


def _row_texts(rows: list[dict[str, Any]], manifest: dict[str, Any] | None) -> list[str]:
    from overbae.services.datasets.text import row_text

    texts: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            texts.append(row_text(r))
        except Exception:  # noqa: BLE001 — one bad row must not sink the pass
            continue
    return texts


def apply_pattern_baselines(
    card: dict[str, Any],
    rows: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministic (no LLM) pass recording ``baseline_match_rate`` + ``matched_rows`` per
    ``detection_pattern``. Invalid patterns and an empty row set record nothing. Mutates ``card``."""
    failure_modes = [fm for fm in (card.get("failure_modes") or []) if isinstance(fm, dict)]
    gate_signals = [
        qs
        for qs in (card.get("quality_signals") or [])
        if isinstance(qs, dict) and qs.get("severity") == "gate"
    ]
    entries = [
        e for e in failure_modes + gate_signals if str(e.get("detection_pattern") or "").strip()
    ]
    if not entries:
        return card

    texts = _row_texts(rows, manifest)
    total = len(texts)
    if not total:
        return card

    for entry in entries:
        pattern = str(entry.get("detection_pattern") or "").strip()
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            logger.warning(
                "dataset_context: skipping invalid detection_pattern %r: %s", pattern, exc
            )
            continue
        matched = sum(1 for t in texts if rx.search(t))
        entry["matched_rows"] = matched
        entry["baseline_match_rate"] = round(matched / total, 4)
    return card
