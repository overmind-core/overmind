from __future__ import annotations

import json
from datetime import timedelta

import pytest
import requests
from django.db import OperationalError, connection
from django.utils import timezone
from factories import make_project, make_span, make_user

from overbae.models import OverwatchFunnelDelivery, ProjectMembership, Span
from overbae.tasks.overwatch import dispatch_funnel_notifications

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _sandbox(settings):
    settings.SANDBOX_URL = "http://sandbox.test"
    settings.SANDBOX_TOKEN = "analytics-test"


@pytest.fixture(autouse=True)
def delivery_config(settings):
    settings.OVERWATCH_FUNNEL_START_AT = (timezone.now() - timedelta(days=1)).isoformat()
    settings.OVERWATCH_FUNNEL_CHANNEL_IDS = json.dumps({"signed_up": "C01234567"})
    settings.SLACK_BOT_TOKEN = "overwatch-bot-test"
    settings.POSTHOG_PROJECT_TOKEN = "overwatch-ingest-test"
    settings.POSTHOG_HOST = "https://eu.i.posthog.com"


def test_posthog_failure_retries_same_uuid_and_slack_sends_once(scripted):
    customer = make_user(email="private-customer@example.com")
    posthog = scripted("https://eu.i.posthog.com").reply(503).reply(json_body={"status": 1})
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    dispatch_funnel_notifications()
    delivery = OverwatchFunnelDelivery.objects.get(user=customer, step="signed_up")
    assert delivery.posthog_delivered_at is None
    assert delivery.posthog_last_error == "posthog_delivery_failed"
    assert delivery.status == "sent"
    dispatch_funnel_notifications()
    delivery.refresh_from_db()
    assert delivery.posthog_delivered_at is not None
    assert delivery.posthog_attempts == 2
    assert posthog.calls[0].json["uuid"] == posthog.calls[1].json["uuid"] == str(delivery.id)
    assert len(slack.calls) == 1
    assert customer.email not in json.dumps(slack.calls[0].json)
    assert customer.email not in json.dumps(posthog.calls[0].json)
    assert posthog.calls[0].json["distinct_id"] == customer.clerk_user_id
    assert posthog.calls[0].json["properties"]["$process_person_profile"] is False


def test_existing_ledger_retries_without_rediscovering_source(scripted):
    customer = make_user()
    old = timezone.now() - timedelta(days=10)
    type(customer).objects.filter(pk=customer.pk).update(date_joined=old)
    delivery = OverwatchFunnelDelivery.objects.create(
        user=customer,
        step="signed_up",
        source_at=timezone.now(),
        channel_id="C01234567",
    )
    posthog = scripted("https://eu.i.posthog.com").reply(json_body={"status": 1})
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    dispatch_funnel_notifications()
    delivery.refresh_from_db()
    assert delivery.posthog_delivered_at is not None
    assert delivery.status == "sent"
    assert len(posthog.calls) == len(slack.calls) == 1


def test_network_exception_text_is_never_saved_or_logged(scripted, caplog):
    make_user()
    scripted("https://eu.i.posthog.com").fail(
        requests.RequestException("private-query-token=test-secret-value")
    )
    scripted("https://slack.com").fail(
        requests.RequestException("private-query-token=test-secret-value")
    )
    dispatch_funnel_notifications()
    delivery = OverwatchFunnelDelivery.objects.get()
    assert delivery.last_error == "slack_delivery_failed"
    assert delivery.posthog_last_error == "posthog_delivery_failed"
    assert "test-secret-value" not in caplog.text


def test_ingestion_redirect_does_not_receive_token_or_customer_id(scripted):
    make_user()
    posthog = scripted("https://eu.i.posthog.com").reply(
        302, headers={"Location": "https://attacker.test/"}
    )
    scripted("https://slack.com").reply(json_body={"ok": True})
    dispatch_funnel_notifications()
    assert len(posthog.calls) == 1
    assert OverwatchFunnelDelivery.objects.get().posthog_delivered_at is None


def test_every_destination_is_bounded_per_tick(scripted):
    for _ in range(25):
        make_user()
    posthog = scripted("https://eu.i.posthog.com").reply(json_body={"status": 1})
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    first = dispatch_funnel_notifications()
    assert first["sent"] == len(posthog.calls) == len(slack.calls) == 20
    second = dispatch_funnel_notifications()
    assert second["sent"] == 5
    assert len(posthog.calls) == len(slack.calls) == 25


def test_recorded_member_does_not_hide_other_project_members(settings, scripted):
    settings.POSTHOG_PROJECT_TOKEN = ""
    settings.OVERWATCH_FUNNEL_CHANNEL_IDS = json.dumps({"sent_first_span": "C01234567"})
    first, second = make_user(), make_user()
    project = make_project(member=first)
    ProjectMembership.objects.create(project=project, user=second)
    span = make_span(project, trace_id="a" * 32)
    OverwatchFunnelDelivery.objects.create(
        user=first,
        step="sent_first_span",
        source_at=span.received_at,
        channel_id="C01234567",
        status="sent",
    )
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    dispatch_funnel_notifications()
    assert OverwatchFunnelDelivery.objects.get(user=second).status == "sent"
    assert len(slack.calls) == 1
    assert f"#{second.pk}" in slack.calls[0].json["text"]


def test_disabled_rollout_and_guest_accounts_never_send(settings, scripted):
    make_user(is_guest=True)
    assert dispatch_funnel_notifications() == {"created": 0, "sent": 0, "failed": 0}
    make_user()
    settings.OVERWATCH_FUNNEL_START_AT = ""
    assert dispatch_funnel_notifications() == {"created": 0, "sent": 0, "failed": 0}


def test_discovery_keeps_first_ever_timestamp_per_member(settings, scripted):
    settings.POSTHOG_PROJECT_TOKEN = ""
    settings.OVERWATCH_FUNNEL_CHANNEL_IDS = json.dumps({"sent_first_span": "C01234567"})
    first, invited = make_user(), make_user()
    project = make_project(member=first)
    old = timezone.now() - timedelta(days=3)
    ProjectMembership.objects.filter(project=project, user=first).update(created_at=old)
    first_span = make_span(project, trace_id="a" * 32)
    Span.objects.filter(pk=first_span.pk).update(received_at=timezone.now() - timedelta(days=2))
    ProjectMembership.objects.create(project=project, user=invited)
    recent_span = make_span(project, trace_id="b" * 32)
    slack = scripted("https://slack.com").reply(json_body={"ok": True})
    dispatch_funnel_notifications()
    assert not OverwatchFunnelDelivery.objects.filter(user=first).exists()
    delivery = OverwatchFunnelDelivery.objects.get(user=invited, step="sent_first_span")
    assert delivery.source_at == recent_span.received_at
    assert len(slack.calls) == 1


def test_slow_step_does_not_discard_signup_or_block_later_retry(settings, scripted, caplog):
    settings.POSTHOG_PROJECT_TOKEN = ""
    settings.OVERWATCH_FUNNEL_CHANNEL_IDS = json.dumps(
        {"signed_up": "C01234567", "sent_first_span": "C01234567"}
    )
    user = make_user()
    project = make_project(member=user)
    make_span(project, trace_id="c" * 32)
    slack = scripted("https://slack.com").reply(json_body={"ok": True})

    def unavailable_source(execute, sql, params, many, context):
        if sql.startswith("SELECT") and '"overbae_span"' in sql:
            raise OperationalError("synthetic_source_unavailable")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(unavailable_source):
        assert dispatch_funnel_notifications()["sent"] == 1
    assert OverwatchFunnelDelivery.objects.get(user=user).step == "signed_up"
    assert "synthetic_source_unavailable" not in caplog.text
    assert dispatch_funnel_notifications()["sent"] == 1
    assert OverwatchFunnelDelivery.objects.get(user=user, step="sent_first_span").status == "sent"
    assert len(slack.calls) == 2
