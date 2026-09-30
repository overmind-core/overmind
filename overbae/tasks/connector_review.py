from __future__ import annotations

import logging
import math
import time
from copy import copy
from datetime import timedelta
from types import SimpleNamespace

from celery import shared_task
from django.utils import timezone

from overbae.models import ConnectorImportPreview
from overbae.services.connectors import get_adapter
from overbae.services.connectors.imports import PREVIEW_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@shared_task(
    name="overbae.tasks.connector_review.preview_connector_import",
    time_limit=PREVIEW_TIMEOUT_SECONDS + 30,
    soft_time_limit=PREVIEW_TIMEOUT_SECONDS,
)
def preview_connector_import(preview_id):
    if not ConnectorImportPreview.objects.filter(pk=preview_id, status="queued").update(
        status="running"
    ):
        return
    preview = ConnectorImportPreview.objects.select_related("credential").get(pk=preview_id)
    started = time.monotonic()
    try:
        credential = copy(preview.credential)
        credential.pk = None
        config = SimpleNamespace(
            source_project_id=preview.source_project_id,
            lookback_days=None,
            backfill_from=preview.window_from,
            backfill_to=preview.window_to,
        )
        credential.active_config = lambda: config
        adapter = get_adapter(credential)
        if adapter.capabilities.needs_source_project and not preview.source_project_id:
            raise ValueError("source_required")
        traces, spans, cursors = set(), set(), set()
        state = {"mode": "backfill"}
        pages = 0
        # Providers may offer count pages without payloads or whole-trace hydration.
        fetch = getattr(adapter, "fetch_preview_page", adapter.fetch_page)
        while True:
            if time.monotonic() - started >= PREVIEW_TIMEOUT_SECONDS:
                raise TimeoutError("preview_timeout")
            if not ConnectorImportPreview.objects.filter(pk=preview.pk, status="running").exists():
                return
            page = fetch(state)
            pages += 1
            for unit in page.units:
                traces.add(unit.external_trace_id)
                spans.update(record.id for record in unit.records)
            if not ConnectorImportPreview.objects.filter(pk=preview.pk, status="running").update(
                trace_count=len(traces), span_count=len(spans)
            ):
                return
            if page.done:
                break
            cursor = repr(sorted(page.next_state.items()))
            if cursor in cursors:
                raise ValueError("repeated_cursor")
            cursors.add(cursor)
            state = page.next_state
        elapsed = time.monotonic() - started
        # Includes measured provider reads and a broad storage allowance. Provider throttling varies.
        minimum = max(
            1, math.ceil(elapsed + max(0, pages - 1) * 2 + len(traces) / 10 + len(spans) / 1000)
        )
        maximum = max(
            minimum + 1,
            math.ceil(elapsed * 3 + max(0, pages - 1) * 2 + len(traces) * 2 + len(spans) / 50),
        )
        ConnectorImportPreview.objects.filter(pk=preview.pk, status="running").update(
            status="ready",
            trace_count=len(traces),
            span_count=len(spans),
            estimated_seconds_min=minimum,
            estimated_seconds_max=maximum,
            finished_at=timezone.now(),
            expires_at=timezone.now() + timedelta(minutes=30),
        )
    except Exception as exc:
        logger.exception("Connector import preview failed: %s", preview.pk)
        message = "Could not count this range. Check the connection and try a smaller range."
        if getattr(exc, "status_code", None) == 429:
            seconds = math.ceil(getattr(exc, "retry_after", None) or 60)
            message = f"Provider rate limit reached. Wait {seconds} seconds and count again."
        ConnectorImportPreview.objects.filter(pk=preview.pk, status="running").update(
            status="failed",
            error=message,
            finished_at=timezone.now(),
        )
