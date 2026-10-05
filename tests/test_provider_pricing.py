from decimal import Decimal

from overbae.services import provider_pricing
from overbae.services.compute_costs import estimate_usage
from overbae.services.inference_pricing import gpu_usd_per_second


def test_current_workspace_rates_drive_costs_without_historical_charges(monkeypatch):
    monkeypatch.setattr(
        provider_pricing,
        "fetch_rates",
        lambda: {
            "gpu_hour_cost_h100": Decimal("7.20"),
            "cpu_hour_cost": Decimal("0.072"),
            "mem_gib_hour_cost": Decimal("0.036"),
        },
    )
    card = provider_pricing.refresh_rates()
    assert card["status"] == "current"
    assert card["fetched_at"] and card["source"] == "modal.Workspace.billing.rates"
    monkeypatch.setattr(provider_pricing, "current_rates", lambda: card)
    assert gpu_usd_per_second("H100") == 0.002
    result = estimate_usage(
        [
            {
                "usage_id": "one",
                "gpu_type": "H100",
                "gpu_count": 1,
                "elapsed_seconds": 100,
                "cpu_core_seconds": 100,
                "memory_gib_seconds": 100,
            }
        ]
    )
    assert result["estimated_usd"] == 0.203
    assert result["rate_card"] == card


def test_unavailable_or_invalid_rates_do_not_fall_back_to_static_prices(monkeypatch):
    def unavailable():
        raise TimeoutError("private provider details")

    monkeypatch.setattr(provider_pricing, "fetch_rates", unavailable)
    failed = provider_pricing.refresh_rates()
    assert failed["status"] == "unavailable" and failed["rates"] == {}
    assert "private" not in str(failed)
    monkeypatch.setattr(provider_pricing, "current_rates", lambda: failed)
    assert gpu_usd_per_second("H100") is None
    monkeypatch.setattr(
        provider_pricing,
        "fetch_rates",
        lambda: {"gpu_hour_cost_h100": Decimal("NaN"), "cpu_hour_cost": Decimal("-1")},
    )
    assert provider_pricing.refresh_rates()["rates"] == {}


def test_rate_cache_outage_does_not_fail_workflow(monkeypatch):
    def unavailable(*args, **kwargs):
        raise ConnectionError("cache offline")

    monkeypatch.setattr(provider_pricing.cache, "get", unavailable)
    monkeypatch.setattr(provider_pricing.cache, "set", unavailable)
    monkeypatch.setattr(
        provider_pricing, "fetch_rates", lambda: {"gpu_hour_cost_h100": Decimal("3.95")}
    )
    assert provider_pricing.refresh_rates()["rates"]["gpu_hour_cost_h100"] == 3.95
