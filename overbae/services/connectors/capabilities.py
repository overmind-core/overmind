"""Map provider capability signals onto Overmind Capabilities.

No provider has a capability ID field — identity is derived from a configured
signal (a recurring observation name, metadata key, tag, or trace name).
Mapping is per-subtree: each boundary observation claims itself and its
descendants until a nested boundary takes over. The root capability owns the
entry_point span.

Capability mapping is mutable and retroactive (lives on ConnectorCredential, not
the versioned sync config). ``connector.agent_key`` stamped at ingest makes
relabeling a pure DB update.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from typing import Any

from django.utils.text import slugify

from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
    CapabilityMapping,
    CapabilityMappingSource,
)

logger = logging.getLogger(__name__)


def extract_signal_value(
    observations: list[ObservationRecord],
    source: CapabilityMappingSource | None,
    key: str | None = None,
) -> str | None:
    """Single-value fallback (v1 / whole-trace). Prefer :func:`assign_capability_keys`."""
    if not observations:
        return None
    root = next((o for o in observations if o.is_root_observation), observations[0])
    if source == "metadata":
        if not key:
            return None
        meta = root.metadata or {}
        val = meta.get(key)
        return str(val) if val is not None else None
    if source == "tag":
        tags = root.tags or []
        if key:
            prefix = f"{key}:"
            for tag in tags:
                if tag.startswith(prefix):
                    return tag[len(prefix) :]
            return None
        return tags[0] if tags else None
    if source == "trace_name":
        return root.trace_name or root.name
    return None


def is_capability_boundary(
    mapping: CapabilityMapping | dict[str, Any],
) -> Callable[[ObservationRecord], bool]:
    """Which observations start a capability, and therefore root an Overmind trace.

    A recurring observation name or a metadata key marks a boundary. Trace-wide
    signals (tag, trace name) cannot mark one at all, so those traces stay
    whole, and neither can an unset source.
    """
    source = (mapping or {}).get("source")
    key = (mapping or {}).get("key")
    if source == "observation_name":
        names = set((mapping or {}).get("names") or [])
        return lambda obs: bool(obs.name) and obs.name in names
    if source == "metadata" and key:
        return lambda obs: (obs.metadata or {}).get(key) is not None
    return lambda obs: False


def boundary_value(
    obs: ObservationRecord, mapping: CapabilityMapping | dict[str, Any]
) -> str | None:
    """The capability key an observation contributes where it starts a capability."""
    if (mapping or {}).get("source") == "metadata":
        value = (obs.metadata or {}).get((mapping or {}).get("key"))
        return str(value) if value is not None else None
    return obs.name or None


def assign_capability_keys(
    observations: list[ObservationRecord],
    mapping: CapabilityMapping | dict[str, Any],
) -> dict[str, str | None]:
    """Per-subtree capability keys: observation id -> discovered signal value.

    Walks the tree so each observation inherits its nearest enclosing capability
    boundary. Trace-wide sources assign one value to every observation.
    """
    source: CapabilityMappingSource | None = mapping.get("source")
    key = mapping.get("key")

    if source in ("tag", "trace_name"):
        value = extract_signal_value(observations, source, key)
        return {obs.id: value for obs in observations}

    boundary = is_capability_boundary(mapping)

    by_id = {obs.id: obs for obs in observations}
    children: dict[str | None, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        children[obs.parent_observation_id].append(obs)

    # Roots: no parent, or parent not in this batch, or isRootObservation.
    roots = [
        obs
        for obs in observations
        if obs.is_root_observation
        or obs.parent_observation_id is None
        or obs.parent_observation_id not in by_id
    ]
    if not roots:
        roots = list(observations)

    result: dict[str, str | None] = {}

    def walk(obs: ObservationRecord, inherited: str | None) -> None:
        # Providers can emit a parent cycle; an already-keyed observation ends the walk.
        if obs.id in result:
            return
        current = (boundary_value(obs, mapping) or None) if boundary(obs) else inherited
        result[obs.id] = current
        for child in children.get(obs.id, []):
            walk(child, current)

    for root in roots:
        walk(root, None)

    # Any orphan not reached (shouldn't happen) gets the whole-trace fallback.
    fallback = extract_signal_value(observations, source, key)
    for obs in observations:
        result.setdefault(obs.id, fallback)
    return result


def discover_capabilities(
    traces: Iterable[list[ObservationRecord]],
    mapping: CapabilityMapping | dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return distinct capability-key candidates with occurrence counts.

    When *mapping* is omitted, discovers the trace-wide signals and the metadata
    keys on offer, so the wizard can show what exists before the user picks one.
    Boundary names come from the profiler's shapes instead.
    """
    sources: list[tuple[CapabilityMappingSource, str | None]]
    if mapping and mapping.get("source"):
        sources = [(mapping["source"], mapping.get("key"))]
    else:
        sources = [
            ("trace_name", None),
            ("tag", None),
            ("metadata", None),
        ]

    tallies: dict[tuple[str, str | None, str], int] = Counter()
    meta_keys: Counter[str] = Counter()
    scanned = 0

    for obs_list in traces:
        for source, key in sources:
            if source == "metadata" and key is None:
                # Collect metadata keys for the wizard to pick from.
                scanned += len(obs_list)
                for obs in obs_list:
                    for mk in obs.metadata or {}:
                        meta_keys[mk] += 1
                continue
            # Carry names through: the observation_name predicate is empty without them.
            keys = assign_capability_keys(
                obs_list,
                {"source": source, "key": key, "names": (mapping or {}).get("names") or []},
            )
            # Count once per trace per distinct value, not once per span. Nested
            # boundaries matter: a two-capability trace must offer both, not just the root's.
            for value in {v for v in keys.values() if v}:
                tallies[(source, key, value)] += 1

    results = [
        {"source": source, "key": key, "value": value, "count": count}
        for (source, key, value), count in tallies.items()
    ]
    results.sort(key=lambda r: (-r["count"], r["source"], r["value"] or ""))
    if meta_keys:
        # Langfuse copies trace-level and resource attributes onto every observation,
        # so a key on all of them marks every span a boundary. Discriminating keys
        # lead, and coverage lets the caller say why the rest are useless.
        ranked = sorted(meta_keys.items(), key=lambda kv: (kv[1] >= scanned, -kv[1], kv[0]))
        results.append(
            {
                "source": "metadata",
                "key": None,
                "value": None,
                "count": sum(meta_keys.values()),
                "metadata_keys": [
                    {
                        "name": name,
                        "observations": count,
                        "coverage": round(count / scanned, 3) if scanned else 0.0,
                    }
                    for name, count in ranked[:50]
                ],
            }
        )
    return results


def resolve_capability(agent_key: str | None, mapping: CapabilityMapping | dict[str, Any], project):
    """Map a discovered provider key to a capability, or None.

    Precedence: explicit assignment → ``auto_create`` (the user opted in on the
    wizard) → fallback. Assignments and fallbacks resolve through the alias
    table, so a renamed or merged capability keeps its mapping."""
    from overbae.services.capabilities import identity

    if not agent_key:
        fallback = mapping.get("fallback_capability_id")
        return identity.lookup(project.id, str(fallback)) if fallback else None

    assigned = (mapping.get("assignments") or {}).get(agent_key)
    if assigned:
        capability = identity.lookup(project.id, str(assigned))
        if capability is not None:
            return capability

    if mapping.get("auto_create"):
        return _observed_capability(project, agent_key)

    fallback = mapping.get("fallback_capability_id")
    return identity.lookup(project.id, str(fallback)) if fallback else None


def _observed_capability(project, capability_name: str):
    """The wizard's opt-in: a provider key names a capability. An existing or
    leftover row of that identity is reused (reactivated in place); a new row is
    runtime-observed so a repo scan never retires it."""
    from overbae.models import Capability
    from overbae.services.capabilities import identity

    known = identity.lookup(project.id, capability_name, include_leftover=True)
    if known is not None:
        if known.status == Capability.Status.LEFTOVER:
            known.set_status(Capability.Status.CURRENT)
            identity.enqueue_rebind(known.project_id)
        return known
    slug = slugify(capability_name)
    if not slug:
        return None
    capability = Capability.objects.create(
        project=project, name=capability_name[:255], slug=slug[:255], observed=True
    )
    logger.info("Observed capability %s/%s from connector mapping", project.slug, slug)
    return capability


def relabel_connector_capabilities(credential, old_mapping: dict | None = None) -> int:
    """Apply current ``credential.capability_mapping`` to existing connector spans.

    Pure DB update keyed by ``connector.credential_id`` + ``connector.agent_key``.
    Returns the number of spans whose ``capability_id`` changed.
    """
    from overbae.models import Conversation, Span

    mapping = credential.capability_mapping or {}
    assignments = mapping.get("assignments") or {}
    if (
        not assignments
        and not mapping.get("auto_create")
        and not mapping.get("fallback_capability_id")
    ):
        return 0

    cred_id = str(credential.id)
    project = credential.project
    qs = Span.objects.filter(
        project=project,
        resource_attrs__contains={CONNECTOR_CREDENTIAL_ID_ATTR: cred_id},
    )

    # Group by agent_key for bulk updates.
    keys = (
        qs.exclude(**{f"attributes__{CONNECTOR_CAPABILITY_KEY_ATTR}__isnull": True})
        .values_list(f"attributes__{CONNECTOR_CAPABILITY_KEY_ATTR}", flat=True)
        .distinct()
    )
    # values_list on JSON key may not work on all backends the same way —
    # fall back to a Python scan of distinct keys when needed.
    distinct_keys: set[str] = set()
    try:
        for k in keys:
            if k:
                distinct_keys.add(str(k))
    except Exception:
        for attrs in qs.values_list("attributes", flat=True).iterator(chunk_size=500):
            if isinstance(attrs, dict) and attrs.get(CONNECTOR_CAPABILITY_KEY_ATTR):
                distinct_keys.add(str(attrs[CONNECTOR_CAPABILITY_KEY_ATTR]))

    # Also include keys present in the new assignments (even if no spans yet).
    distinct_keys |= set(assignments.keys())

    updated = 0
    touched_trace_ids: set[str] = set()
    for agent_key in distinct_keys:
        capability = resolve_capability(agent_key, mapping, project)
        capability_id = capability.id if capability else None
        matching = list(
            qs.filter(**{f"attributes__{CONNECTOR_CAPABILITY_KEY_ATTR}": agent_key}).iterator(
                chunk_size=200
            )
        )
        # JSON containment filter is more reliable across backends:
        if not matching:
            matching = [
                s
                for s in qs.iterator(chunk_size=500)
                if (s.attributes or {}).get(CONNECTOR_CAPABILITY_KEY_ATTR) == agent_key
            ]
        to_update = []
        for span in matching:
            if span.capability_id != capability_id:
                span.capability_id = capability_id
                to_update.append(span)
                if span.trace_id:
                    touched_trace_ids.add(span.trace_id)
        if to_update:
            Span.objects.bulk_update(to_update, ["capability"])
            updated += len(to_update)

    # Re-point Conversation.capability from root spans that now have a capability.
    if updated:
        for conv in Conversation.objects.filter(
            project=project, capability__isnull=True
        ).iterator():
            root = (
                Span.objects.filter(
                    project=project,
                    conversation=conv,
                    parent_span_id__isnull=True,
                    capability__isnull=False,
                )
                .order_by("start_time_ns")
                .first()
            )
            if root is not None:
                conv.capability_id = root.capability_id
                conv.save(update_fields=["capability"])

    return updated
