"""Provider-neutral observation record shared by every connector adapter.

``capabilities``, ``profiling`` and the span mapping are typed to this shape, so a new
provider gets boundary detection, the ranked shape picker and repo-scan
proposals for free by emitting these instead of its own record type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ObservationRecord:
    """One provider observation/span, normalised."""

    id: str
    trace_id: str | None
    parent_observation_id: str | None
    type: str
    name: str | None
    start_time: str | None
    end_time: str | None
    user_id: str | None = None
    session_id: str | None = None
    environment: str | None = None
    level: str | None = None
    status_message: str | None = None
    version: str | None = None
    input: Any = None
    output: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    usage_details: dict[str, Any] = field(default_factory=dict)
    cost_details: dict[str, Any] = field(default_factory=dict)
    total_cost: float | None = None
    latency: float | None = None
    tags: list[str] = field(default_factory=list)
    release: str | None = None
    trace_name: str | None = None
    is_root_observation: bool | None = None
    # Provider-specific span attributes, stamped under the source's namespace.
    extra_attrs: dict[str, Any] = field(default_factory=dict)
    # Set only by providers that rewrite rows in place; drives the ingest
    # overwrite check. None keeps a provider insert-only.
    row_version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


LangFuseObservation = ObservationRecord
