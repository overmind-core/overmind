"""Provider-agnostic connector adapter protocol.

Pipeline code talks only to ``ConnectorAdapter``. Resume state in
``ConnectorCredential.sync_cursor`` is opaque per-provider JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from overbae.services.connectors.mapping import SourceConventions


@dataclass(frozen=True)
class SourceProject:
    id: str
    name: str


@dataclass(frozen=True)
class Capabilities:
    """Declared provider features the wizard can render."""

    exact_count: bool = False
    capability_sources: tuple[str, ...] = (
        "observation_name",
        "trace_name",
        "tag",
        "metadata",
    )
    needs_source_project: bool = True
    # Shown next to the import range where the provider drops old data silently,
    # so a longer range would return nothing instead of erroring.
    retention_note: str = ""
    # False for a single bearer token. Read before verify, so it must stay
    # answerable from the adapter class alone — see registry.capabilities_for.
    needs_secret: bool = True


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    detail: str = ""
    api_version: str = ""
    projects: list[SourceProject] = field(default_factory=list)
    capabilities: Capabilities | None = None


@dataclass
class IngestUnit:
    """One trace's worth of provider-native records."""

    records: list[Any]
    external_trace_id: str = ""
    newest_ts: str | None = None


@dataclass
class Page:
    """One resumable fetch slice."""

    units: list[IngestUnit]
    next_state: dict[str, Any]
    done: bool
    # Optional window bounds for ConnectorSyncRun bookkeeping.
    window_from: Any = None
    window_to: Any = None
    mode: str = "backfill"  # "backfill" | "live"


@runtime_checkable
class ConnectorAdapter(Protocol):
    source: str
    capabilities: Capabilities
    # The provider's own span vocabulary, read by the capability profiler.
    conventions: SourceConventions

    def verify(self) -> VerifyResult: ...

    def list_source_projects(self) -> list[SourceProject]: ...

    def count(
        self,
        *,
        lookback_days: int | None,
        source_project_id: str = "",
        window_from: Any = None,
        window_to: Any = None,
    ) -> int | None: ...

    # source_project_id is the project the wizard is currently offering, which for a
    # needs_source_project adapter is only in the browser: the sync config is not
    # written until setup finishes, so discovery cannot read it back.
    def sample_units(
        self,
        *,
        lookback_days: int,
        limit: int = 200,
        source_project_id: str = "",
        window_from: Any = None,
        window_to: Any = None,
    ) -> list[Any]: ...

    def fetch_page(self, state: dict[str, Any]) -> Page: ...

    def to_span_dicts(
        self,
        unit: IngestUnit,
        *,
        credential: Any,
        project: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
