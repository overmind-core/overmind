"""Normalize provider observations without changing trace identity or parentage."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from overbae.api.span_usage import usage_span_attributes
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.schema import (
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
    source: str
    span_types: Mapping[str, str] = field(default_factory=dict)
    metadata_skip: frozenset[str] = frozenset()

    def span_type(self, observation_type: str | None) -> str:
        return self.span_types.get((observation_type or "").upper(), _DEFAULT_SPAN_TYPE)


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


def observations_to_span_dicts(
    observations: list[ObservationRecord],
    *,
    credential,
    conventions: SourceConventions,
    project=None,
) -> list[dict[str, Any]]:
    """Convert one provider trace's observations into Span-shaped dicts.

    Capability assignment is a separate review of stored trace groups.
    """
    if not observations:
        return []

    cred_id = str(credential.id)
    return [
        _span_dict(
            obs,
            credential=credential,
            cred_id=cred_id,
            span_id=span_id_for(cred_id, obs.id),
            trace_id=trace_id_for(cred_id, obs.trace_id or obs.id),
            parent_span_id=span_id_for(cred_id, obs.parent_observation_id)
            if obs.parent_observation_id
            else None,
            is_root=not obs.parent_observation_id,
            external_trace_id=obs.trace_id or obs.id,
            conventions=conventions,
        )
        for obs in observations
    ]


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
    if obs.row_version is not None:
        attrs[CONNECTOR_VERSION_ATTR] = obs.row_version
    session = obs.session_id
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
        "capability": None,
    }
