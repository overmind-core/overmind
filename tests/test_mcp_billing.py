from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from starlette.testclient import TestClient
from test_mcp_account_access import account, rpc

from overbae.models import APIToken, BillingService, BillingTelemetry
from overbae.services.billing_ledger import append_entry
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


def report(client, key, **arguments):
    return rpc(
        client,
        key,
        "tools/call",
        {
            "name": "get_credit_balance",
            "arguments": arguments,
        },
    )["result"]


def test_credit_report_scopes_balance_usage_and_window(settings):
    settings.STRIPE_SECRET_KEY = "sk_test_local"
    user, projects, raw = account()
    other, _, _ = account()
    since = timezone.now() - timedelta(hours=1)
    for owner, project, amount, service in [
        (user, None, "10", BillingService.STRIPE_TOPUP),
        (user, projects[0], "-0.1234567", BillingService.EVALUATION),
        (user, projects[1], "-2", BillingService.FINETUNING_JOB),
        (other, projects[0], "-99", BillingService.EVALUATION),
    ]:
        append_entry(
            user=owner,
            project_id=project.pk if project else None,
            amount=Decimal(amount),
            service=service,
            metadata={"secret": "never-return-this"},
        )
    old = append_entry(
        user=user,
        project_id=projects[0].pk,
        amount=Decimal("-1"),
        service=BillingService.EVALUATION,
    )
    BillingTelemetry.objects.filter(pk=old.pk).update(timestamp=since - timedelta(seconds=1))
    count = BillingTelemetry.objects.count()
    with TestClient(create_mcp_application()) as client:
        result = report(client, raw, since=since.isoformat())
        assert not result.get("isError"), result
        data = result["structuredContent"]
        assert Decimal(data["balance_credits"]) == Decimal("5687.65433")
        assert Decimal(data["granted_credits"]) == Decimal("6000")
        assert Decimal(data["spent_credits"]) == Decimal("312.34567")
        assert Decimal(data["usage"]["spent_credits"]) == Decimal("212.34567")
        assert {
            row["service"]: Decimal(row["spent_credits"]) for row in data["usage"]["by_service"]
        } == {
            "evaluation": Decimal("12.34567"),
            "finetuning-job": Decimal("200"),
        }
        assert data["billing_enabled"] is True
        assert "never-return-this" not in str(result)
        project_raw, _ = APIToken.create_for_user(user, project=projects[0], permission=["read"])
        scoped = report(client, project_raw, since=since.isoformat())["structuredContent"]
        assert {k: v for k, v in scoped.items() if k != "observed_at"} == {
            k: v for k, v in data.items() if k != "observed_at"
        }
    assert BillingTelemetry.objects.count() == count


def test_credit_report_empty_uncapped_negative_and_invalid_windows(settings):
    settings.STRIPE_SECRET_KEY = ""
    user, projects, raw = account()
    with TestClient(create_mcp_application()) as client:
        data = report(client, raw)["structuredContent"]
        assert data["billing_enabled"] is False
        assert Decimal(data["balance_credits"]) == 0
        assert data["usage"]["by_service"] == []
        append_entry(user=user, amount=Decimal("-0.5"), service=BillingService.INFERENCE)
        charged = report(client, raw)["structuredContent"]
        assert Decimal(charged["balance_credits"]) == -50
        assert Decimal(charged["usage"]["spent_credits"]) == 50
        for arguments in [
            {"since": "2026-01-01T00:00:00"},
            {"since": (timezone.now() + timedelta(days=1)).isoformat()},
            {"user_id": str(user.pk)},
            {"project_id": str(projects[0].pk)},
        ]:
            result = report(client, raw, **arguments)
            assert result["isError"]
            assert result["structuredContent"]["error"]["code"] == "invalid_input"
