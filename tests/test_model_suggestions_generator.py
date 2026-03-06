"""Tests for overmind.tasks.model_suggestions_generator."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from overmind.tasks.model_suggestions_generator import (
    _ModelRecommendation,
    _ModelSuggestionsResponse,
    _format_available_models,
    _format_span_stats,
    _fetch_span_usage_stats,
)


# ---------------------------------------------------------------------------
# _format_span_stats
# ---------------------------------------------------------------------------


def test_format_span_stats_no_data():
    assert (
        _format_span_stats({"has_data": False})
        == "No production usage data available yet."
    )


def test_format_span_stats_missing_has_data():
    assert _format_span_stats({}) == "No production usage data available yet."


def test_format_span_stats_with_data():
    stats = {
        "has_data": True,
        "current_model": "gpt-5-mini",
        "sample_size": 42,
        "avg_input_tokens": 500.0,
        "avg_output_tokens": 200.0,
        "avg_latency_ms": 320.5,
        "avg_cost_usd": 0.000123,
    }
    text = _format_span_stats(stats)
    assert "gpt-5-mini" in text
    assert "42" in text
    assert "500" in text
    assert "200" in text
    assert "320" in text
    assert "$0.000123" in text


def test_format_span_stats_zero_cost():
    stats = {
        "has_data": True,
        "current_model": "gpt-5-mini",
        "sample_size": 5,
        "avg_input_tokens": 100.0,
        "avg_output_tokens": 50.0,
        "avg_latency_ms": 100.0,
        "avg_cost_usd": 0.0,
    }
    text = _format_span_stats(stats)
    assert "N/A" in text


# ---------------------------------------------------------------------------
# _format_available_models
# ---------------------------------------------------------------------------


def test_format_available_models_no_providers():
    with patch(
        "overmind.tasks.model_suggestions_generator.get_available_providers",
        return_value=set(),
    ):
        result = _format_available_models(None)
    assert result == "No models available."


def test_format_available_models_openai_only_no_span_stats():
    with patch(
        "overmind.tasks.model_suggestions_generator.get_available_providers",
        return_value={"openai"},
    ):
        result = _format_available_models(None)

    assert result != "No models available."
    # All lines should be openai models
    for line in result.splitlines():
        assert "(openai)" in line
    assert "reasoning" in result


def test_format_available_models_with_span_stats_shows_cost():
    span_stats = {
        "has_data": True,
        "avg_input_tokens": 500,
        "avg_output_tokens": 200,
    }
    with (
        patch(
            "overmind.tasks.model_suggestions_generator.get_available_providers",
            return_value={"openai"},
        ),
        patch(
            "overmind.tasks.model_suggestions_generator.calculate_llm_usage_cost",
            return_value=0.001234,
        ),
    ):
        result = _format_available_models(span_stats)

    assert "projected cost/call" in result
    assert "0.001234" in result


def test_format_available_models_span_stats_zero_tokens_omits_cost():
    # has_data=True but both avg_input_tokens and avg_output_tokens are 0 → no cost column
    span_stats = {
        "has_data": True,
        "avg_input_tokens": 0,
        "avg_output_tokens": 0,
    }
    with patch(
        "overmind.tasks.model_suggestions_generator.get_available_providers",
        return_value={"openai"},
    ):
        result = _format_available_models(span_stats)

    assert "projected cost/call" not in result


def test_format_available_models_unknown_cost_shows_na():
    span_stats = {
        "has_data": True,
        "avg_input_tokens": 500,
        "avg_output_tokens": 200,
    }
    with (
        patch(
            "overmind.tasks.model_suggestions_generator.get_available_providers",
            return_value={"openai"},
        ),
        patch(
            "overmind.tasks.model_suggestions_generator.calculate_llm_usage_cost",
            return_value=0.0,
        ),
    ):
        result = _format_available_models(span_stats)

    assert "projected cost/call: N/A" in result


def test_format_available_models_reasoning_labels():
    """Spot-check that reasoning metadata is rendered for each model type."""
    with patch(
        "overmind.tasks.model_suggestions_generator.get_available_providers",
        return_value={"openai", "anthropic", "gemini"},
    ):
        result = _format_available_models(None)

    assert (
        "reasoning: not supported" in result
        or "reasoning: optional" in result
        or "reasoning: always on" in result
    )


# ---------------------------------------------------------------------------
# _fetch_span_usage_stats
# ---------------------------------------------------------------------------


def _make_session(rows: list) -> AsyncMock:
    """Build a session mock where execute() is async but .all() is sync."""
    execute_result = MagicMock()
    execute_result.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    return session


@pytest.mark.asyncio
async def test_fetch_span_usage_stats_no_rows():
    result = await _fetch_span_usage_stats(_make_session([]), "proj_1_slug")
    assert result == {"has_data": False}


@pytest.mark.asyncio
async def test_fetch_span_usage_stats_no_model_in_metadata():
    """Rows with no recognizable model field → has_data=False (model_counts empty)."""
    rows = [
        (
            {"gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 50},
            1000,
            2000,
        ),
    ]
    result = await _fetch_span_usage_stats(_make_session(rows), "proj_1_slug")
    assert result == {"has_data": False}


@pytest.mark.asyncio
async def test_fetch_span_usage_stats_with_data():
    rows = [
        (
            {
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 400,
                "gen_ai.usage.output_tokens": 100,
            },
            1_000_000_000,
            1_500_000_000,
        ),
        (
            {
                "gen_ai.request.model": "gpt-4o",
                "gen_ai.usage.input_tokens": 600,
                "gen_ai.usage.output_tokens": 200,
            },
            2_000_000_000,
            2_800_000_000,
        ),
    ]

    with patch(
        "overmind.tasks.model_suggestions_generator.calculate_llm_usage_cost",
        return_value=0.001,
    ):
        result = await _fetch_span_usage_stats(_make_session(rows), "proj_1_slug")

    assert result["has_data"] is True
    assert result["sample_size"] == 2
    assert result["avg_input_tokens"] == pytest.approx(500.0)
    assert result["avg_output_tokens"] == pytest.approx(150.0)
    assert result["avg_latency_ms"] > 0
    assert result["avg_cost_usd"] == pytest.approx(0.001)


@pytest.mark.asyncio
async def test_fetch_span_usage_stats_most_common_model_wins():
    rows = [
        (
            {
                "gen_ai.request.model": "gpt-5-mini",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
            1000,
            2000,
        ),
        (
            {
                "gen_ai.request.model": "gpt-5-mini",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
            1000,
            2000,
        ),
        (
            {
                "gen_ai.request.model": "claude-haiku-4-5",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 50,
            },
            1000,
            2000,
        ),
    ]

    with patch(
        "overmind.tasks.model_suggestions_generator.calculate_llm_usage_cost",
        return_value=0.0,
    ):
        result = await _fetch_span_usage_stats(_make_session(rows), "proj_1_slug")

    assert result["has_data"] is True
    assert "gpt-5-mini" in result["current_model"]


@pytest.mark.asyncio
async def test_fetch_span_usage_stats_none_metadata_rows_skipped():
    rows = [
        (None, 1000, 2000),
        (
            {
                "gen_ai.request.model": "gpt-5-mini",
                "gen_ai.usage.input_tokens": 200,
                "gen_ai.usage.output_tokens": 80,
            },
            1000,
            2000,
        ),
    ]

    with patch(
        "overmind.tasks.model_suggestions_generator.calculate_llm_usage_cost",
        return_value=0.0,
    ):
        result = await _fetch_span_usage_stats(_make_session(rows), "proj_1_slug")

    assert result["has_data"] is True
    assert result["sample_size"] == 2  # both rows counted, None row just skipped


# ---------------------------------------------------------------------------
# _ModelRecommendation / _ModelSuggestionsResponse pydantic parsing
# ---------------------------------------------------------------------------


def test_model_recommendation_valid():
    rec = _ModelRecommendation(
        model="gpt-5-mini",
        provider="openai",
        category="fastest",
        reasoning_effort=None,
        reason="Very fast and cheap.",
    )
    assert rec.category == "fastest"
    assert rec.reasoning_effort is None


def test_model_recommendation_invalid_category_raises():
    with pytest.raises(ValidationError):
        _ModelRecommendation(
            model="gpt-5-mini",
            provider="openai",
            category="most_affordable",  # not a valid Literal value
            reason="Cheap.",
        )


def test_model_recommendation_all_valid_categories():
    for cat in ("best_overall", "most_capable", "fastest", "cheapest"):
        rec = _ModelRecommendation(
            model="gpt-5-mini",
            provider="openai",
            category=cat,
            reason="ok",
        )
        assert rec.category == cat


def test_model_suggestions_response_parses_list():
    payload = {
        "recommendations": [
            {
                "model": "gpt-5-mini",
                "provider": "openai",
                "category": "cheapest",
                "reasoning_effort": None,
                "reason": "Lowest cost.",
            },
            {
                "model": "claude-sonnet-4-6",
                "provider": "anthropic",
                "category": "most_capable",
                "reasoning_effort": "medium",
                "reason": "Best quality.",
            },
        ]
    }
    resp = _ModelSuggestionsResponse.model_validate(payload)
    assert len(resp.recommendations) == 2
    assert resp.recommendations[0].category == "cheapest"
    assert resp.recommendations[1].reasoning_effort == "medium"


def test_model_suggestions_response_invalid_category_in_list_raises():
    payload = {
        "recommendations": [
            {
                "model": "gpt-5-mini",
                "provider": "openai",
                "category": "best_value",  # invalid
                "reason": "ok",
            }
        ]
    }
    with pytest.raises(ValidationError):
        _ModelSuggestionsResponse.model_validate(payload)
