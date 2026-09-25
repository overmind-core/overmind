"""Adapter registry contract — every registered adapter must satisfy the protocol."""

from __future__ import annotations

from types import SimpleNamespace

from overbae.services.connectors import (
    ConnectorAdapter,
    capabilities_for,
    capability_source_error,
    get_adapter,
    registered_sources,
)
from overbae.services.connectors.base import Capabilities, IngestUnit


def test_langfuse_adapter_satisfies_protocol():
    cred = SimpleNamespace(
        pk=None,
        api_key="pk",
        api_secret="sk",
        base_url="",
        api_version="v2",
        connector_type="langfuse",
        capability_mapping={},
        project=None,
    )

    # Avoid network: stub client methods after construction.
    adapter = get_adapter(cred)
    assert isinstance(adapter, ConnectorAdapter)
    assert adapter.source == "langfuse"
    assert isinstance(adapter.capabilities, Capabilities)
    assert adapter.capabilities.needs_source_project is True

    # Structural checks on return types (no network).
    unit = IngestUnit(records=[], external_trace_id="t")
    assert adapter.to_span_dicts(unit, credential=cred) == []


def test_braintrust_adapter_satisfies_protocol():
    cred = SimpleNamespace(
        pk=None,
        api_key="bt-st-key",
        api_secret="",
        base_url="",
        api_version="unknown",
        connector_type="braintrust",
        capability_mapping={},
        project=None,
    )

    adapter = get_adapter(cred)
    assert isinstance(adapter, ConnectorAdapter)
    assert adapter.source == "braintrust"
    assert isinstance(adapter.capabilities, Capabilities)
    assert adapter.capabilities.needs_source_project is True
    assert adapter.count(lookback_days=7) is None

    unit = IngestUnit(records=[], external_trace_id="t")
    assert adapter.to_span_dicts(unit, credential=cred) == []


def test_langsmith_adapter_satisfies_protocol():
    cred = SimpleNamespace(
        pk=None,
        api_key="lsv2_sk_key",
        api_secret="",
        base_url="",
        api_version="unknown",
        connector_type="langsmith",
        capability_mapping={},
        project=None,
    )

    adapter = get_adapter(cred)
    assert isinstance(adapter, ConnectorAdapter)
    assert adapter.source == "langsmith"
    assert isinstance(adapter.capabilities, Capabilities)
    assert adapter.capabilities.needs_source_project is True
    assert adapter.count(lookback_days=7) is None

    unit = IngestUnit(records=[], external_trace_id="t")
    assert adapter.to_span_dicts(unit, credential=cred) == []


def test_galileo_adapter_satisfies_protocol():
    cred = SimpleNamespace(
        pk=None,
        api_key="galileo-key",
        api_secret="",
        base_url="",
        api_version="unknown",
        connector_type="galileo",
        capability_mapping={},
        project=None,
    )

    adapter = get_adapter(cred)
    assert isinstance(adapter, ConnectorAdapter)
    assert adapter.source == "galileo"
    assert isinstance(adapter.capabilities, Capabilities)
    assert adapter.capabilities.needs_source_project is True
    assert adapter.capabilities.needs_secret is False
    assert adapter.count(lookback_days=7) is None

    unit = IngestUnit(records=[], external_trace_id="t")
    assert adapter.to_span_dicts(unit, credential=cred) == []


def test_every_adapter_declares_the_vocabulary_the_profiler_reads():
    for source in registered_sources():
        cred = SimpleNamespace(
            pk=None,
            api_key="k",
            api_secret="s",
            base_url="",
            api_version="v2",
            connector_type=source,
            capability_mapping={},
            project=None,
        )
        assert get_adapter(cred).conventions.source == source


def test_which_keys_a_provider_needs_is_answerable_without_a_credential():
    """The setup flow asks for keys before it has any to build an adapter from."""
    assert capabilities_for("langfuse").needs_secret is True
    assert capabilities_for("braintrust").needs_secret is False
    assert capabilities_for("langsmith").needs_secret is False
    assert capabilities_for("galileo").needs_secret is False
    assert capabilities_for("nonesuch") is None


def test_a_connector_refuses_an_capability_source_it_cannot_honour(monkeypatch):
    """Both shipped adapters honour all four sources, so the narrow case is a stub."""
    from overbae.services.connectors import registry

    monkeypatch.setitem(
        registry._REGISTRY,
        "namesonly",
        SimpleNamespace(capabilities=Capabilities(capability_sources=("observation_name",))),
    )

    message = capability_source_error("namesonly", "metadata")
    assert message and "observation_name" in message
    assert capability_source_error("namesonly", "observation_name") is None
    assert capability_source_error("langfuse", "metadata") is None


def test_an_absent_source_or_unknown_connector_is_not_second_guessed():
    assert capability_source_error("braintrust", None) is None
    assert capability_source_error("braintrust", "") is None
    assert capability_source_error("nonesuch", "observation_name") is None
