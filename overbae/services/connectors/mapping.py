"""Map observation records onto Overmind Span-shaped dicts.

Capabilities are trace roots: every capability-boundary observation roots one Overmind trace
holding its subtree as the provider recorded it, stopping where a nested capability
starts its own. The provider's structure is otherwise preserved — a 20-email scan
under one capability stays one 41-span trace.

Every observation lands in exactly one trace, so span ids stay 1:1 with
observation ids. Only trace ids change, and only for traces with several capabilities.

Every provider reaches this module through ``SourceConventions``; nothing here
may know a provider by name.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from overbae.api.span_usage import usage_span_attributes
from overbae.services.connectors.capabilities import (
    assign_capability_keys,
    is_capability_boundary,
    resolve_capability,
)
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
    CONNECTOR_EXTERNAL_ID_ATTR,
    CONNECTOR_SOURCE_ATTR,
    CONNECTOR_VERSION_ATTR,
)
from overbae.services.connectors.spans import span_id_for, trace_id_for

_DEFAULT_SPAN_TYPE = "llm_call"
# Providers bound none of these; the columns do, and an over-long value would
# fail the whole page's insert rather than just its own span.
_MAX_NAME = 255  # Span.name and Span.service_name
_MAX_OPERATION = 512  # Span.operation


def _fit(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass(frozen=True)
class SourceConventions:
    """One provider's vocabulary, expressed in Overmind's.

    Every table here is keyed by upper-cased provider type, so a provider that
    spells its own in lower case ("llm", "function") matches the same entries as
    one that shouts them. An unmapped type is an LLM call, which is what most
    spans are.

    *capability_type* and *model_call_types* are read by the capability profiler rather
    than by ingest: they are how it tells a capability from the model calls it makes.
    A provider with no capability type of its own leaves *capability_type* empty.
    """

    source: str
    span_types: Mapping[str, str] = field(default_factory=dict)
    metadata_skip: frozenset[str] = frozenset()
    capability_type: str = ""
    model_call_types: frozenset[str] = frozenset()

    def span_type(self, observation_type: str | None) -> str:
        return self.span_types.get((observation_type or "").upper(), _DEFAULT_SPAN_TYPE)

    def is_capability_type(self, observation_type: str | None) -> bool:
        return (
            bool(self.capability_type) and (observation_type or "").upper() == self.capability_type
        )

    def is_model_call(self, observation_type: str | None) -> bool:
        return (observation_type or "").upper() in self.model_call_types


@dataclass
class _Partition:
    """One Overmind trace: a root observation plus the subtree it owns."""

    root: ObservationRecord
    members: list[ObservationRecord]


def _ns(iso_or_epoch: str | float | None) -> int:
    if iso_or_epoch is None:
        return int(time.time() * 1e9)
    if isinstance(iso_or_epoch, (int, float)):
        return int(float(iso_or_epoch) * 1e9)
    try:
        dt = datetime.fromisoformat(str(iso_or_epoch).replace("Z", "+00:00"))
        return int(dt.timestamp() * 1e9)
    except (ValueError, AttributeError):
        return int(time.time() * 1e9)


def _usage_from_observation(obs: ObservationRecord) -> dict[str, Any]:
    usage = obs.usage_details or {}
    # v2 usageDetails uses input/output/total; v1 traces used promptTokens etc.
    attrs = usage_span_attributes(
        {
            "prompt_tokens": usage.get("input") or usage.get("promptTokens"),
            "completion_tokens": usage.get("output") or usage.get("completionTokens"),
            "total_tokens": usage.get("total") or usage.get("totalTokens"),
            "cost": obs.total_cost
            if obs.total_cost is not None
            else (obs.cost_details or {}).get("total"),
        },
        model=obs.model,
    )
    # Non-generation rows report zero-filled usage rather than null, and a zero
    # is not a measurement worth storing on every span in the trace.
    return {k: v for k, v in attrs.items() if v not in (0, 0.0, "")}


def _metadata_attrs(obs: ObservationRecord, conventions: SourceConventions) -> dict[str, Any]:
    meta = obs.metadata if isinstance(obs.metadata, dict) else {}
    return {
        f"{conventions.source}.metadata.{k}": v
        for k, v in meta.items()
        if k not in conventions.metadata_skip
    }


def _children_index(
    observations: list[ObservationRecord],
) -> dict[str | None, list[ObservationRecord]]:
    order = {obs.id: i for i, obs in enumerate(observations)}
    children: dict[str | None, list[ObservationRecord]] = defaultdict(list)
    for obs in observations:
        children[obs.parent_observation_id].append(obs)
    for siblings in children.values():
        siblings.sort(key=lambda o: (o.start_time or "", order[o.id]))
    return children


def _subtree(
    root: ObservationRecord,
    children: dict[str | None, list[ObservationRecord]],
    is_boundary: Callable[[ObservationRecord], bool],
) -> list[ObservationRecord]:
    """Root plus descendants, stopping at nested capabilities (they own their traces).

    ``seen`` guards against a parent cycle in provider data, which would otherwise
    spin forever inside a worker.
    """
    collected: list[ObservationRecord] = []
    seen: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.id in seen:
            continue
        seen.add(node.id)
        collected.append(node)
        stack.extend(c for c in children.get(node.id, []) if not is_boundary(c))
    return collected


def partition_by_capability(
    observations: list[ObservationRecord],
    is_boundary: Callable[[ObservationRecord], bool],
) -> list[_Partition]:
    """Partition one provider trace into capability-rooted Overmind traces.

    A capability whose children are all capabilities (an orchestrator, or a planner with
    nothing to plan) roots a trace of its own — the run happened.
    """
    by_id = {obs.id: obs for obs in observations}
    children = _children_index(observations)
    roots = [
        obs
        for obs in observations
        if obs.is_root_observation
        or obs.parent_observation_id is None
        or obs.parent_observation_id not in by_id
    ] or list(observations)

    heads = [obs for obs in observations if is_boundary(obs)]
    # A trace with no capability boundary at all keeps its whole-tree shape.
    heads += [obs for obs in roots if not is_boundary(obs)]

    partitions = [
        _Partition(root=head, members=_subtree(head, children, is_boundary)[1:]) for head in heads
    ]
    partitions.sort(key=lambda p: (p.root.start_time or "", p.root.id))
    return partitions


def observations_to_span_dicts(
    observations: list[ObservationRecord],
    *,
    credential,
    conventions: SourceConventions,
    project=None,
    mapping: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Convert one provider trace's observations into Span-shaped dicts.

    Resolves per-subtree capabilities when *mapping* (or credential.capability_mapping) is set.
    """
    if not observations:
        return []

    cred_id = str(credential.id)
    capability_mapping = mapping if mapping is not None else (credential.capability_mapping or {})
    capability_type = conventions.capability_type
    capability_keys = (
        assign_capability_keys(observations, capability_mapping, capability_type=capability_type)
        if capability_mapping
        else {}
    )
    project = project or credential.project

    capability_cache: dict[str | None, Any] = {}

    def _capability_for(key: str | None):
        if key not in capability_cache:
            capability_cache[key] = (
                resolve_capability(key, capability_mapping, project) if capability_mapping else None
            )
        return capability_cache[key]

    external_trace_id = observations[0].trace_id or observations[0].id
    partitions = partition_by_capability(
        observations,
        is_capability_boundary(
            capability_mapping, observations=observations, capability_type=capability_type
        ),
    )
    # A single capability needs no disambiguation, so ids stay as they were.
    single = len(partitions) == 1

    spans: list[dict[str, Any]] = []
    for part in partitions:
        trace_key = external_trace_id if single else f"{external_trace_id}#{part.root.id}"
        om_trace_id = trace_id_for(cred_id, trace_key)

        for obs in (part.root, *part.members):
            is_root = obs.id == part.root.id
            spans.append(
                _span_dict(
                    obs,
                    credential=credential,
                    cred_id=cred_id,
                    span_id=span_id_for(cred_id, obs.id),
                    trace_id=om_trace_id,
                    parent_span_id=(
                        None if is_root else span_id_for(cred_id, obs.parent_observation_id)
                    ),
                    is_root=is_root,
                    external_trace_id=external_trace_id,
                    group_id=None if single else external_trace_id,
                    agent_key=capability_keys.get(obs.id),
                    capability=_capability_for(capability_keys.get(obs.id)),
                    capability_mapping=capability_mapping,
                    conventions=conventions,
                )
            )
    return spans


def _span_dict(
    obs: ObservationRecord,
    *,
    credential,
    cred_id: str,
    span_id: str,
    trace_id: str,
    parent_span_id: str | None,
    is_root: bool,
    external_trace_id: str,
    group_id: str | None,
    agent_key: str | None,
    capability,
    capability_mapping: dict[str, Any],
    conventions: SourceConventions,
) -> dict[str, Any]:
    source = conventions.source
    start_ns = _ns(obs.start_time)
    if obs.end_time:
        end_ns = _ns(obs.end_time)
    elif obs.latency is not None:
        # Providers that report a duration instead of an end report it in seconds.
        end_ns = start_ns + int(float(obs.latency) * 1e9)
    else:
        end_ns = start_ns

    attrs: dict[str, Any] = {
        CONNECTOR_SOURCE_ATTR: source,
        CONNECTOR_EXTERNAL_ID_ATTR: obs.id,
        f"{source}.trace_id": external_trace_id,
        f"{source}.observation_id": obs.id,
        f"{source}.observation_type": obs.type,
    }
    if agent_key:
        attrs[CONNECTOR_CAPABILITY_KEY_ATTR] = agent_key
    if obs.row_version is not None:
        attrs[CONNECTOR_VERSION_ATTR] = obs.row_version
    # Split traces are fragments of one upstream run, so absent a real session the
    # provider trace id groups them back into one; unsplit traces stay sessionless.
    session = obs.session_id or group_id
    if session:
        attrs["conversation.id"] = str(session)
    if obs.session_id:
        attrs[f"{source}.session_id"] = str(obs.session_id)
    if obs.user_id:
        attrs[f"{source}.user_id"] = obs.user_id
    if obs.tags:
        attrs[f"{source}.tags"] = obs.tags
    if obs.environment:
        attrs[f"{source}.environment"] = obs.environment
    if obs.version:
        attrs[f"{source}.version"] = obs.version
    if obs.input is not None:
        attrs["overmind.input.data"] = obs.input
    if obs.output is not None:
        attrs["overmind.output.data"] = obs.output
    attrs.update({f"{source}.{k}": v for k, v in (obs.extra_attrs or {}).items()})
    attrs.update(_metadata_attrs(obs, conventions))
    attrs.update(_usage_from_observation(obs))

    level = (obs.level or "").upper()
    if level and level != "DEFAULT":
        attrs[f"{source}.level"] = level

    if capability is not None:
        attrs["overmind.capability.id"] = str(capability.id)
    if agent_key and (capability_mapping.get("source") == "metadata" or capability):
        attrs["overmind.capability.name"] = agent_key

    return {
        "span_id": span_id,
        "trace_id": trace_id,
        "parent_span_id": parent_span_id,
        "span_type": "entry_point" if is_root else conventions.span_type(obs.type),
        "name": _fit(obs.name or obs.type or source, _MAX_NAME),
        "kind": 0,
        "start_time_ns": start_ns,
        "end_time_ns": end_ns,
        "duration_ns": max(0, end_ns - start_ns),
        # WARNING is a degraded-but-complete run: OTel 1 means OK and 2 stops
        # scoring, so it stays UNSET and shows up via <source>.level + the message.
        "status_code": 2 if level == "ERROR" else 0,
        "status_message": obs.status_message or "",
        "service_name": _fit(f"{source}/{credential.name}", _MAX_NAME),
        "operation": _fit(obs.name or "", _MAX_OPERATION),
        "resource_attrs": {
            CONNECTOR_SOURCE_ATTR: source,
            CONNECTOR_CREDENTIAL_ID_ATTR: cred_id,
        },
        "scope_name": source,
        "scope_version": "",
        "attributes": attrs,
        "events": [],
        "links": [],
        "capability": capability,
    }
