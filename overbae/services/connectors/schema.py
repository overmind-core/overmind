"""Shared contracts for connector sync config and span provenance.

Provenance attribute keys are stamped on every connector-imported span so
retroactive capability relabeling is a pure DB update.
"""

from __future__ import annotations

from typing import Literal, TypedDict

# Span provenance — stamped at ingest.
CONNECTOR_SOURCE_ATTR = "connector.source"
CONNECTOR_CREDENTIAL_ID_ATTR = "connector.credential_id"
CONNECTOR_EXTERNAL_ID_ATTR = "connector.external_id"
CONNECTOR_CAPABILITY_KEY_ATTR = "connector.agent_key"
# Provider row version, when the provider rewrites rows in place. Absent for
# insert-only providers, which keeps their spans immutable after first import.
CONNECTOR_VERSION_ATTR = "connector.version"
CONNECTOR_SOURCE_LANGFUSE = "langfuse"
CONNECTOR_SOURCE_BRAINTRUST = "braintrust"
CONNECTOR_SOURCE_LANGSMITH = "langsmith"
CONNECTOR_SOURCE_GALILEO = "galileo"

CapabilityMappingSource = Literal[
    "observation_name",
    "metadata",
    "tag",
    "trace_name",
]


class CapabilityMapping(TypedDict, total=False):
    """Mutable capability-identity mapping on ConnectorCredential.

    ``source`` also decides which observations root a trace, so changing it
    regroups spans and needs a re-import; changing ``assignments`` alone
    relabels existing spans by ``connector.agent_key`` in place.
    """

    source: CapabilityMappingSource
    key: str | None
    names: list[str]  # observation_name source: the names that start a capability
    assignments: dict[str, str]  # discovered value -> capability UUID
    auto_create: bool
    fallback_capability_id: str | None
