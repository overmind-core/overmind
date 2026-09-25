from types import SimpleNamespace

import pytest

from overbae.services.eval import context_suggestions as suggestions


@pytest.fixture
def catalog(monkeypatch):
    entries = [
        {
            "id": f"vendor/{name}",
            "name": name,
            "context_length": 32000,
            "max_completion_tokens": 10000,
            "supported_parameters": ["tools", "response_format"],
            "prompt_price": price,
            "completion_price": price * 2,
        }
        for name, price in [("current", 2), ("preferred", 4), ("cheaper", 1)]
    ]
    monkeypatch.setattr(suggestions, "fetch_model_catalog", lambda: (entries, True))
    monkeypatch.setattr(suggestions, "openrouter_configured", lambda: True)
    monkeypatch.setattr(suggestions, "judge_picker_models", lambda: ["preferred", "cheaper"])
    monkeypatch.setattr(
        suggestions, "inference_models", lambda: [SimpleNamespace(name="preferred")]
    )
    monkeypatch.setattr(suggestions, "pricing_slug", lambda name: f"vendor/{name}")
    monkeypatch.setattr(suggestions, "effective_max_tokens", lambda _: 5000)
    return entries


def check(**kwargs):
    return {
        "status": "warning",
        "role": "judge",
        "model": "current",
        "reserved_output_tokens": 5000,
        **kwargs,
    }


def test_cost_projection_uses_all_inputs_and_reserved_output_without_price_ranking(catalog):
    result = suggestions.suggest_models(check(), inputs=[100, 300], uses_tools=False)
    assert result["estimated_cost_usd"] == pytest.approx(0.0408)
    assert [row["model"] for row in result["suggestions"]] == ["preferred", "cheaper"]
    assert result["suggestions"][0]["estimated_cost_usd"] == pytest.approx(0.0816)
    assert result["suggestions"][0]["cost_delta_usd"] == pytest.approx(0.0408)
    assert result["suggestions"][1]["cost_delta_usd"] == pytest.approx(-0.0204)
    assert "same judge" in result["suggestion_note"]


@pytest.mark.parametrize("field", ["context_length", "max_completion_tokens"])
def test_requires_verified_input_and_output_capacity(catalog, field):
    catalog[1][field] = None
    catalog[2][field] = 4000
    result = suggestions.suggest_models(check(), inputs=[500], uses_tools=False)
    assert result["suggestions"] == []
    assert "No compatible alternative" in result["suggestion_note"]


def test_does_not_shrink_output_to_make_an_alternative_fit(catalog, monkeypatch):
    monkeypatch.setattr(suggestions, "effective_max_tokens", lambda _: 17000)
    result = suggestions.suggest_models(check(), inputs=[500], uses_tools=False)
    assert result["suggestions"] == []


def test_requires_judge_schema_and_generation_tool_support(catalog):
    catalog[1]["supported_parameters"] = ["tools"]
    catalog[2]["supported_parameters"] = []
    assert suggestions.suggest_models(check(), inputs=[500], uses_tools=False)["suggestions"] == []
    catalog[1]["supported_parameters"] = ["response_format"]
    assert (
        suggestions.suggest_models(check(role="generation"), inputs=[500], uses_tools=True)[
            "suggestions"
        ]
        == []
    )


@pytest.mark.parametrize("price", [None, float("nan"), float("inf"), -1])
def test_unknown_or_invalid_rates_are_not_zero_cost(catalog, price):
    catalog[0]["completion_price"] = price
    result = suggestions.suggest_models(check(), inputs=[500], uses_tools=False)
    assert result["estimated_cost_usd"] is None
    assert result["suggestions"][0]["cost_delta_usd"] is None


def test_zero_price_is_known_and_custom_endpoint_has_no_borrowed_catalog_price(catalog):
    catalog[1]["prompt_price"] = catalog[1]["completion_price"] = 0
    result = suggestions.suggest_models(check(), inputs=[500], uses_tools=False, custom=True)
    assert result["estimated_cost_usd"] is None
    assert result["suggestions"][0]["estimated_cost_usd"] == 0


def test_excludes_family_under_test(catalog):
    result = suggestions.suggest_models(
        check(), inputs=[500], uses_tools=False, variant_models=["candidate"]
    )
    assert result["suggestions"] == []


def test_no_suggestions_without_rows_or_configured_provider(catalog, monkeypatch):
    assert suggestions.suggest_models(check(), inputs=[], uses_tools=False)["suggestions"] == []
    monkeypatch.setattr(suggestions, "openrouter_configured", lambda: False)
    assert suggestions.suggest_models(check(), inputs=[100], uses_tools=False)["suggestions"] == []


def test_fitting_models_have_prices_but_do_not_suggest_or_change_selection(catalog):
    original = check(status="fits")
    result = suggestions.suggest_models(original, inputs=[100], uses_tools=False)
    assert result["suggestions"] == []
    assert result["estimated_cost_usd"] is not None
    assert original == check(status="fits")


def test_dropdown_checks_every_judge_and_keeps_unsafe_options(catalog):
    catalog[1]["context_length"] = 5500
    checks = [
        {
            **check(),
            "estimated_input_tokens": size,
            "total_input_tokens": size * 2,
            "checked_rows": 2,
            "estimated_cost_usd": 0.1,
        }
        for size in (100, 1000)
    ]
    options = suggestions.judge_model_options(checks)
    assert [option["status"] for option in options] == ["warning", "fits"]
    assert options[1]["estimated_cost_usd"] == pytest.approx(0.0422)
    assert options[1]["cost_delta_usd"] == pytest.approx(-0.1578)


def test_dropdown_missing_schema_or_limits_is_unverified(catalog):
    catalog[1]["supported_parameters"] = []
    catalog[2]["max_completion_tokens"] = None
    options = suggestions.judge_model_options(
        [{**check(), "estimated_input_tokens": 100, "total_input_tokens": 100, "checked_rows": 1}]
    )
    assert all(option["status"] == "unknown" for option in options)


def test_dropdown_same_judge_has_no_rounding_price_change(catalog):
    catalog[1]["prompt_price"] = 0.01
    checks = [
        {
            **check(model="preferred"),
            "estimated_input_tokens": 123,
            "total_input_tokens": 123,
            "checked_rows": 1,
            "estimated_cost_usd": suggestions.token_cost(catalog[1], 123, 5000),
        }
        for _ in range(11)
    ]
    assert suggestions.judge_model_options(checks)[0]["cost_delta_usd"] == 0
