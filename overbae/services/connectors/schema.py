"""Shared contracts for connector sync config and span provenance.

Provenance attribute keys are stamped on every connector-imported span so
retroactive capability relabeling is a pure DB update.
"""

from __future__ import annotations

# Span provenance — stamped at ingest.
CONNECTOR_SOURCE_ATTR = "connector.source"
CONNECTOR_CREDENTIAL_ID_ATTR = "connector.credential_id"
CONNECTOR_EXTERNAL_ID_ATTR = "connector.external_id"
CONNECTOR_CAPABILITY_KEY_ATTR = "connector.agent_key"
# Provider row version prevents older provider snapshots overwriting newer data.
CONNECTOR_VERSION_ATTR = "connector.version"
CONNECTOR_SOURCE_LANGFUSE = "langfuse"
CONNECTOR_SOURCE_BRAINTRUST = "braintrust"
CONNECTOR_SOURCE_LANGSMITH = "langsmith"
CONNECTOR_SOURCE_GALILEO = "galileo"
