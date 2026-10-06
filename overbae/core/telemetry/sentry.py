import logging

import sentry_sdk
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.boto3 import Boto3Integration
from sentry_sdk.integrations.celery import CeleryIntegration
from sentry_sdk.integrations.django import DjangoIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.mcp import MCPIntegration
from sentry_sdk.integrations.openai import OpenAIIntegration
from sentry_sdk.integrations.redis import RedisIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, EventScrubber


def init_sentry(
    *,
    dsn: str,
    environment: str,
    release: str,
    traces_sample_rate: float,
    profile_session_sample_rate: float,
    propagate_traces_to: list[str],
) -> None:
    if not dsn:
        return

    def traces_sampler(context) -> float:
        path = (context.get("asgi_scope") or {}).get("path")
        return 0.0 if path == "/health" else traces_sample_rate

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release or None,
        # Metadata only: no bodies, local variables, cookies, IPs or emails.
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        event_scrubber=EventScrubber(denylist=[*DEFAULT_DENYLIST, "x-api-key"]),
        traces_sampler=traces_sampler,
        profile_session_sample_rate=profile_session_sample_rate,
        profile_lifecycle="trace",
        enable_logs=True,
        # Sentry headers must not reach OpenRouter, Stripe or Modal.
        trace_propagation_targets=propagate_traces_to,
        integrations=[
            DjangoIntegration(),
            CeleryIntegration(monitor_beat_tasks=True),
            StarletteIntegration(),
            RedisIntegration(),
            HttpxIntegration(),
            Boto3Integration(),
            AsyncioIntegration(),
            OpenAIIntegration(include_prompts=False),
            MCPIntegration(include_prompts=False),
            LoggingIntegration(
                level=logging.INFO, event_level=logging.ERROR, sentry_logs_level=logging.INFO
            ),
        ],
    )
