"""Re-bind spans that ingest left unbound once their identity exists.

Ingest never creates a capability: a span whose ``overmind.capability.id``
the project does not know stays unbound. A scan or a hand-made capability can
later make that identity resolvable; this task binds the backlog in place and
re-projects the touched graph edges. Idempotent — ``identity.lookup`` only ever
returns an existing row."""

from __future__ import annotations

import logging

from celery import shared_task

from overbae.api import overmind_attrs as oc_attrs
from overbae.services.capabilities import identity

logger = logging.getLogger(__name__)

# Bounded window: the newest unbound spans are the ones a user is looking at;
# a years-old backlog re-binds over successive runs.
_WINDOW = 20_000
_BATCH = 500

_GENERIC_NAMES = frozenset(
    {"overclaw-cli", "overclaw", "overmind-cli", "overmind", "unknown-service"}
)


def _probes(span) -> list[str]:
    """Identity values in the order ingest resolves them: span-level id, then
    resource id. Ids only — the name is a display label and never binds."""
    attrs = span.attributes if isinstance(span.attributes, dict) else {}
    resource = span.resource_attrs if isinstance(span.resource_attrs, dict) else {}
    raw = [attrs.get(oc_attrs.CAPABILITY_ID), resource.get(oc_attrs.CAPABILITY_ID)]
    return [str(v) for v in raw if v and str(v) not in _GENERIC_NAMES]


@shared_task(name="overbae.tasks.capability_rebind.rebind_unbound_spans")
def rebind_unbound_spans(*, project_id: str) -> dict:
    from overbae.models import Span  # noqa: PLC0415

    cache: dict[str, object] = {}
    bound = 0
    mutations = []
    batch: list[Span] = []
    spans = (
        Span.objects.filter(project_id=project_id, capability__isnull=True)
        .order_by("-start_time_ns")
        .iterator(chunk_size=_BATCH)
    )
    for i, span in enumerate(spans):
        if i >= _WINDOW:
            break
        for probe in _probes(span):
            key = probe.lower()
            if key not in cache:
                cache[key] = identity.lookup(project_id, probe)
            if cache[key] is not None:
                span.capability = cache[key]
                batch.append(span)
                break
        if len(batch) >= _BATCH:
            bound += _flush(Span, batch, mutations)
            batch = []
    bound += _flush(Span, batch, mutations)
    if bound:
        logger.info("[rebind] project %s: %d spans bound", project_id, bound)
    return {"bound": bound}


def _flush(span_model, batch: list, mutations: list) -> int:
    if not batch:
        return 0
    span_model.objects.bulk_update(batch, ["capability"])
    return len(batch)
