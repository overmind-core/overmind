"""One resolver for every identity a capability is known by.

Telemetry stamps ``overmind.capability.id`` (``overmind.capability.name`` is a label); URLs and
Console routes carry ids, slugs, and names; renames change all of them. Resolution
never creates a row: an identity the project does not have lands on the graph
floor (``None``), which the callers keep visible as "unbound"."""

from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from overbae.models import Capability, IdentityAlias

_AGENT_KINDS = (IdentityAlias.Kind.ID, IdentityAlias.Kind.NAME, IdentityAlias.Kind.SLUG)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return False
    return True


def lookup(project_id, value: str, *, include_leftover: bool = False) -> Capability | None:
    """Resolve an id, name, or slug to its capability.

    Returns only current capabilities unless ``include_leftover`` is set; a
    soft-deleted row never resolves. Never creates one. Case-insensitive; also
    tries the slugified form so a typed name and a scan slug meet. Ingest
    passes ids only; name and slug resolution serve the API and Console."""
    probe = (value or "").strip()
    if not probe:
        return None
    candidates = {probe.lower()}
    slugged = slugify(probe)
    if slugged:
        candidates.add(slugged)
    if _is_uuid(probe):
        direct = Capability.objects.filter(project_id=project_id, id=probe).first()
        if direct is not None:
            return _settle(direct, include_leftover)
    alias = (
        IdentityAlias.objects.filter(
            project_id=project_id, kind__in=_AGENT_KINDS, value__in=candidates
        )
        .select_related("capability")
        .order_by("created_at")
        .first()
    )
    if alias is None:
        return None
    return _settle(alias.capability, include_leftover)


def _settle(capability: Capability, include_leftover: bool) -> Capability | None:
    if capability.status == Capability.Status.CURRENT:
        return capability
    if include_leftover and capability.status == Capability.Status.LEFTOVER:
        return capability
    return None


def unique_slug(project_id, base: str) -> str:
    slug = slugify(base) or "capability"
    if not Capability.objects.filter(project_id=project_id, slug=slug).exists():
        return slug
    for i in range(2, 100):
        candidate = f"{slug}-{i}"
        if not Capability.objects.filter(project_id=project_id, slug=candidate).exists():
            return candidate
    return f"{slug}-{timezone.now().strftime('%H%M%S')}"


def enqueue_rebind(project_id) -> None:
    """After a commit that makes new identities resolvable, re-bind the backlog
    of unbound spans. On-commit so tests and rollbacks never enqueue."""

    def _send():
        from overbae.tasks.capability_rebind import rebind_unbound_spans  # noqa: PLC0415

        rebind_unbound_spans.delay(project_id=str(project_id))

    transaction.on_commit(_send)
