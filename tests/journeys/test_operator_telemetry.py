"""Operators see every request, task and lifecycle moment without customer payloads."""

from __future__ import annotations

import json

import pytest
import sentry_sdk
from django.conf import settings
from fakes.telemetry import TelemetrySink

from overbae.core.telemetry import get_client
from overbae.core.telemetry.sentry import init_sentry

from .stack import drain

TICKETS = ("Refund order 42 please", "Where is order 42?")


@pytest.fixture
def telemetry(monkeypatch):
    sink = TelemetrySink()
    monkeypatch.setattr(settings, "POSTHOG_PROJECT_TOKEN", "phc_journey")
    monkeypatch.setattr(settings, "POSTHOG_HOST", sink.url)
    monkeypatch.setattr(settings, "SENTRY_ENVIRONMENT", "journey")
    get_client.cache_clear()
    init_sentry(
        dsn=sink.sentry_dsn,
        environment="journey",
        release="journey",
        traces_sample_rate=1.0,
        profile_session_sample_rate=0.0,
        propagate_traces_to=[],
    )
    yield sink
    get_client().shutdown()
    get_client.cache_clear()
    sentry_sdk.flush()
    sentry_sdk.init()
    sink.close()


def test_operators_see_requests_tasks_and_lifecycle_without_payloads(
    cli, mcp_for, sample_agent, live_api, support_desk_llm, rubric_judges, worker, telemetry
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    project_id = str(mcp_for(cli.project_key(sample_agent)).project()["id"])
    sample_agent.run(*TICKETS, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    get_client().flush()
    sentry_sdk.flush()

    requests = [e["properties"] for e in telemetry.events("api request")]
    assert {"/api/projects/", "/api/v1/traces"} <= {r["route"] for r in requests}
    sync = next(r for r in requests if r["route"] == "/api/v1/sync")
    assert sync["status"] == 200
    assert sync["auth_kind"] == "api_key_account"
    traces = next(r for r in requests if r["route"] == "/api/v1/traces")
    assert traces["auth_kind"] == "api_key_project"
    assert traces["$groups"] == {"project": project_id}

    (synced,) = telemetry.events("snapshot synced")
    assert synced["properties"]["project_id"] == project_id
    assert synced["properties"]["capabilities"] > 0

    ingested = telemetry.events("traces ingested")
    assert sum(e["properties"]["spans"] for e in ingested) > 0

    scored = [
        e["properties"]
        for e in telemetry.events("task finished")
        if e["properties"]["task"].endswith("score_trace")
    ]
    assert scored
    assert {task["queue"] for task in scored} == {"io_traces"}
    assert {task["state"] for task in scored} == {"SUCCESS"}

    for event in telemetry.posthog_events:
        assert event["properties"]["environment"] == "journey"
        for key, value in event["properties"].items():
            assert key.startswith("$") or not isinstance(value, (dict, list)), (event, key)

    assert telemetry.sentry_envelopes
    assert any(f'"project_id":"{project_id}"' in e for e in telemetry.sentry_envelopes)
    sent = json.dumps(telemetry.posthog_events) + "".join(telemetry.sentry_envelopes)
    for ticket in TICKETS:
        assert ticket not in sent

    print(
        "TELEMETRY_ARTIFACT",
        json.dumps(
            {
                "command": "make test-journeys test_args='-k test_operators_see_requests_tasks'",
                "requires": "compose postgres + redis",
                "fixtures": "support_desk sample_agent, two tickets, live ASGI app, celery worker",
                "project_id": project_id,
                "posthog_events": sorted({e["event"] for e in telemetry.posthog_events}),
                "sentry_envelopes": len(telemetry.sentry_envelopes),
                "score_trace_tasks": len(scored),
            }
        ),
    )
