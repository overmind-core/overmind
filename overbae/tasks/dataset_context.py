from __future__ import annotations

import logging

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
