"""Connector adapter registry — source slug → adapter factory."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from overbae.services.connectors.base import Capabilities, ConnectorAdapter

_AdapterFactory = Callable[[Any], "ConnectorAdapter"]

_REGISTRY: dict[str, _AdapterFactory] = {}


def register(source: str, factory: _AdapterFactory) -> None:
    _REGISTRY[source] = factory


def get_adapter(credential) -> ConnectorAdapter:
    factory = _REGISTRY.get(credential.connector_type)
    if factory is None:
        raise ValueError(f"No connector adapter registered for '{credential.connector_type}'")
    return factory(credential)


def registered_sources() -> frozenset[str]:
    return frozenset(_REGISTRY)


def capabilities_for(connector_type: str) -> Capabilities | None:
    """What a provider can do, without a credential to build an adapter from.

    Adapters declare capabilities on the class, so callers deciding what to ask
    the user for — before there are any keys to verify — can read them here.
    """
    return getattr(_REGISTRY.get(connector_type), "capabilities", None)


def capability_source_error(connector_type: str, source: str | None) -> str | None:
    """Why *source* cannot identify capabilities for this connector, or None if it can.

    Capabilities are otherwise advisory to the wizard. This makes them binding at
    the write boundary: a source the adapter cannot honour matches no span, so
    saving it would attribute nothing at all and report success doing it.
    """
    declared = tuple(getattr(capabilities_for(connector_type), "capability_sources", ()))
    if not source or not declared or source in declared:
        return None
    return f"{connector_type} cannot identify capabilities by {source}. It supports: {', '.join(declared)}."


def _register_builtins() -> None:
    from overbae.services.connectors.braintrust.adapter import BraintrustAdapter
    from overbae.services.connectors.galileo.adapter import GalileoAdapter
    from overbae.services.connectors.langfuse.adapter import LangfuseAdapter
    from overbae.services.connectors.langsmith.adapter import LangSmithAdapter

    register("braintrust", BraintrustAdapter)
    register("galileo", GalileoAdapter)
    register("langfuse", LangfuseAdapter)
    register("langsmith", LangSmithAdapter)


_register_builtins()
