"""Project-scoped MCP tools for signal subscriptions."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.db.models import Q

from overbae.innermind.signals import signal_factory
from overbae.models import SignalEvent, SignalSubscription
from overbae.services.entity_resolution import resolve_capability
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.signals import (
    ListSignalsInput,
    ListSignalsOutput,
    SetSignalSubscriptionInput,
    SetSignalSubscriptionOutput,
    SignalSubscriptionContract,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import resource_link

EVENTS_LINK_TITLE = "Signal events"


def _capability(context: MCPContext, reference: str | None):
    if not reference:
        return None
    capability, error = resolve_capability(context.project, reference)
    if capability is None:
        raise MCPError("capability_not_found", error or "The capability was not found.")
    return capability


def _contract(subscription: SignalSubscription) -> SignalSubscriptionContract:
    return SignalSubscriptionContract(
        id=str(subscription.id),
        capability=subscription.capability.slug if subscription.capability_id else None,
        enabled=subscription.enabled,
        config=subscription.config,
    )


def _events_link() -> dict:
    return resource_link("signal-events", "", EVENTS_LINK_TITLE)


def _list_sync(payload: ListSignalsInput, context: MCPContext) -> ListSignalsOutput:
    capability = _capability(context, payload.capability)
    subscriptions = SignalSubscription.objects.filter(project=context.project).select_related(
        "capability"
    )
    scope = Q(capability__isnull=True)
    if capability is not None:
        scope |= Q(capability=capability)
    by_type: dict[str, list[SignalSubscriptionContract]] = {}
    for subscription in subscriptions.filter(scope):
        by_type.setdefault(subscription.signal_type, []).append(_contract(subscription))
    entries = []
    for entry in signal_factory.catalog():
        overrides = by_type.get(entry["signal_type"], [])
        selected = next((s for s in overrides if s.capability), None) or next(
            (s for s in overrides if s.capability is None), None
        )
        entries.append(
            {
                **entry,
                "enabled": selected.enabled if selected else entry["default_enabled"],
                "subscriptions": overrides[:20],
            }
        )
    enabled = sum(entry["enabled"] for entry in entries)
    return ListSignalsOutput(
        summary=f"{len(entries)} signals, {enabled} enabled.",
        signals=entries,
        unseen_events=SignalEvent.objects.filter(
            project=context.project, seen_at__isnull=True
        ).count(),
        resource_links=[_events_link()],
    )


def _set_sync(
    payload: SetSignalSubscriptionInput, context: MCPContext
) -> SetSignalSubscriptionOutput:
    capability = _capability(context, payload.capability)
    try:
        config = signal_factory.validate_config(payload.signal_type, payload.config)
    except ValueError as error:
        raise MCPError("invalid_input", str(error), fields={"config": "invalid"}) from None
    subscription, created = SignalSubscription.objects.update_or_create(
        project=context.project,
        capability=capability,
        signal_type=payload.signal_type,
        defaults={"enabled": payload.enabled, "config": config},
        create_defaults={
            "enabled": payload.enabled,
            "config": config,
            "created_by": context.user,
        },
    )
    status = "created" if created else "updated"
    return SetSignalSubscriptionOutput(
        summary=f"{payload.signal_type} {'enabled' if subscription.enabled else 'disabled'}.",
        status=status,
        subscription=_contract(subscription),
        signal_type=subscription.signal_type,
        resource_links=[_events_link()],
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_signal_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="list_signals",
            title="List signals",
            description="List automatic signal checks, effective enabled state, defaults and "
            "project/capability overrides, plus the unseen event count. Deterministic checks "
            "run by default; plan_drift requires opt-in model calls. Read events at "
            "overmind://signal-events.",
            input_model=ListSignalsInput,
            output_model=ListSignalsOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        _async_handler(_list_sync),
    )
    catalog.register(
        ToolDefinition(
            name="set_signal_subscription",
            title="Set signal subscription",
            description="Override one signal for the project or one capability. Deterministic "
            "checks run by default; plan_drift requires opt-in model calls. Capability settings "
            "override project settings. Checks run every five minutes over the last day of spans.",
            input_model=SetSignalSubscriptionInput,
            output_model=SetSignalSubscriptionOutput,
            read_only=False,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:observability:write"}),
            cost_class="free",
            async_mode="sync",
        ),
        _async_handler(_set_sync),
    )
