"""Universal connector adapter layer.

Pipeline code imports from here. Provider-specific clients live under
``overbae.services.connectors.<provider>``.
"""

from overbae.services.connectors.base import (
    Capabilities,
    ConnectorAdapter,
    IngestUnit,
    Page,
    SourceProject,
    VerifyResult,
)
from overbae.services.connectors.records import ObservationRecord
from overbae.services.connectors.registry import (
    capabilities_for,
    capability_source_error,
    get_adapter,
    registered_sources,
)
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
    CONNECTOR_EXTERNAL_ID_ATTR,
    CONNECTOR_SOURCE_ATTR,
    CONNECTOR_SOURCE_BRAINTRUST,
    CONNECTOR_SOURCE_GALILEO,
    CONNECTOR_SOURCE_LANGFUSE,
    CONNECTOR_SOURCE_LANGSMITH,
    CONNECTOR_VERSION_ATTR,
)
from overbae.services.connectors.spans import span_id_for, trace_id_for
from overbae.services.connectors.windows import TimeWindow, plan_windows

__all__ = [
    "CONNECTOR_CAPABILITY_KEY_ATTR",
    "CONNECTOR_CREDENTIAL_ID_ATTR",
    "CONNECTOR_EXTERNAL_ID_ATTR",
    "CONNECTOR_SOURCE_ATTR",
    "CONNECTOR_SOURCE_BRAINTRUST",
    "CONNECTOR_SOURCE_GALILEO",
    "CONNECTOR_SOURCE_LANGFUSE",
    "CONNECTOR_SOURCE_LANGSMITH",
    "CONNECTOR_VERSION_ATTR",
    "Capabilities",
    "ConnectorAdapter",
    "IngestUnit",
    "ObservationRecord",
    "Page",
    "SourceProject",
    "TimeWindow",
    "VerifyResult",
    "capability_source_error",
    "capabilities_for",
    "get_adapter",
    "plan_windows",
    "registered_sources",
    "span_id_for",
    "trace_id_for",
]
