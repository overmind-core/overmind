import hashlib
import hmac
import json
import time

import pytest
import requests

from overbae.models import BillingTelemetry

WEBHOOK_SECRET = "whsec_journey"
MODEL = "openai/gpt-4.1-mini"


@pytest.fixture
def billed(settings, cli, sample_agent, fake_llm, worker, request):
    settings.STRIPE_WEBHOOK_SECRET = WEBHOOK_SECRET
    if request.param == "self_host":
        settings.STRIPE_SECRET_KEY = ""
    fake_llm.on(lambda r: r.model == MODEL, {"content": "ok", "usage": {"cost": 30.0}})
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    return request.param


def complete(live_api, key) -> int:
    return requests.post(
        f"{live_api.url}/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
        timeout=30,
    ).status_code


def top_up(live_api, user, usd) -> int:
    event = {
        "id": "evt_journey",
        "object": "event",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_journey",
                "object": "checkout.session",
                "metadata": {"purpose": "credit-topup", "user_id": str(user.pk)},
                "payment_status": "paid",
                "amount_total": usd * 100,
                "customer": "cus_journey",
            }
        },
    }
    payload = json.dumps(event)
    stamp = int(time.time())
    signature = hmac.new(
        WEBHOOK_SECRET.encode(), f"{stamp}.{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return requests.post(
        f"{live_api.url}/api/billing/webhook/",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": f"t={stamp},v1={signature}",
        },
        timeout=30,
    ).status_code


def ledger(rest) -> list[float]:
    return sorted(
        float(row["amount"]) for row in rest.request("GET", "/api/billing/ledger/")["results"]
    )


def test_retired_service_receipts_remain_readable(account, rest_for):
    user, account_key = account
    receipt = BillingTelemetry.objects.create(
        user=user,
        service="cursor-agent",
        amount="-0.55",
        idempotency_key="retired-agent-charge",
        metadata={"llm_usage": {"served_model": "composer-2.5"}},
    )
    before = list(BillingTelemetry.objects.filter(user=user).values())
    rows = rest_for(account_key).request("GET", "/api/billing/ledger/")["results"]
    retained = next(row for row in rows if row["id"] == str(receipt.pk))
    assert retained["service"] == "cursor-agent"
    assert retained["service_label"] == "Cursor agent"
    assert float(retained["amount"]) == -0.55
    assert list(BillingTelemetry.objects.filter(user=user).values()) == before


@pytest.mark.parametrize("billed", ["hosted"], indirect=True)
def test_hosted_credits_meter_spend_stop_at_zero_and_top_up_once(
    billed, live_api, cli, sample_agent, account, rest_for
):
    user, account_key = account
    key = cli.project_key(sample_agent)

    assert complete(live_api, key) == 200
    assert complete(live_api, key) == 200
    assert ledger(rest_for(account_key)) == [-30.0, -30.0, 50.0]
    assert complete(live_api, key) == 402

    assert top_up(live_api, user, 20) == 204
    assert top_up(live_api, user, 20) == 204
    assert ledger(rest_for(account_key)) == [-30.0, -30.0, 20.0, 50.0]
    assert complete(live_api, key) == 200


@pytest.mark.parametrize("billed", ["self_host"], indirect=True)
def test_self_hosted_usage_is_metered_without_a_credit_cap(
    billed, live_api, cli, sample_agent, account, rest_for
):
    user, account_key = account
    key = cli.project_key(sample_agent)

    for _ in range(3):
        assert complete(live_api, key) == 200
    assert ledger(rest_for(account_key)) == [-30.0, -30.0, -30.0, 50.0]
    assert top_up(live_api, user, 20) == 404
