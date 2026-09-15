from __future__ import annotations

from unittest import mock

from overbae.api import overmind_attrs as oc_attrs
from overbae.api.span_usage import usage_span_attributes
from overbae.services import model_catalog


class _Usage:
    """OpenAI/OpenRouter-style usage object (attribute access, no ``cost``)."""

    def __init__(self, prompt_tokens, completion_tokens, total_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


def test_dict_usage_with_cost_sets_all_genai_keys():
    attrs = usage_span_attributes(
        {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42, "cost": 0.0123}
    )
    assert attrs[oc_attrs.LLM_PROMPT_TOKENS] == 30
    assert attrs[oc_attrs.LLM_COMPLETION_TOKENS] == 12
    assert attrs[oc_attrs.LLM_TOTAL_TOKENS] == 42
    assert attrs[oc_attrs.LLM_COST] == 0.0123


def test_object_usage_derives_total_when_absent():
    attrs = usage_span_attributes(_Usage(30, 12, None))
    assert attrs[oc_attrs.LLM_TOTAL_TOKENS] == 42  # 30 + 12
    # No provider cost and no model → cost is an honest absence, not a zero.
    assert oc_attrs.LLM_COST not in attrs


def test_cost_derived_from_model_pricing_when_provider_omits_it():
    catalog = [{"id": "openai/gpt-5-mini", "prompt_price": 0.25, "completion_price": 2.0}]
    with mock.patch.object(model_catalog, "fetch_model_catalog", return_value=(catalog, True)):
        attrs = usage_span_attributes(
            {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, model="gpt-5-mini"
        )
    # 1M * 0.25/1M + 1M * 2.0/1M = 2.25
    assert attrs[oc_attrs.LLM_COST] == 2.25
    assert attrs[oc_attrs.LLM_MODEL] == "gpt-5-mini"


def test_absent_usage_returns_empty():
    assert usage_span_attributes(None) == {}
    assert oc_attrs.LLM_COMPLETION_TOKENS not in usage_span_attributes({"prompt_tokens": 5})


def test_estimate_cost_none_when_catalog_unavailable():
    with mock.patch.object(model_catalog, "fetch_model_catalog", return_value=([], False)):
        assert model_catalog.estimate_cost("gpt-5-mini", 100, 100) is None
