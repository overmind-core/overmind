"""Single-completion generation and a per-variant gate against the recorded baseline."""

from __future__ import annotations

from types import SimpleNamespace

from overbae.services.eval.comparison import compare_variant_to_baseline
from overbae.tasks import eval as eval_tasks


def _quality(mean: float) -> dict:
    return {"mean": mean, "pass_rate": mean, "n": 4}


def _summary():
    return {
        "baseline_variant_id": "base",
        "metrics": ["quality", "format"],
        "gate_metrics": ["format"],
        "variants": {
            "base": {
                "label": "recorded",
                "metrics": {"quality": _quality(0.8), "format": _quality(1.0)},
            },
            "lower": {
                "label": "openai/gpt-5-mini",
                "metrics": {"quality": _quality(0.4), "format": _quality(0.0)},
            },
            "same": {
                "label": "anthropic/claude-sonnet-4.5",
                "metrics": {"quality": _quality(0.8), "format": _quality(0.0)},
            },
        },
    }


def test_each_variant_is_gated_on_its_own_and_gate_metrics_are_excluded():
    rows = {
        row["label"]: row["overall"]["status"]
        for row in compare_variant_to_baseline(_summary())["variants"]
    }
    assert rows["openai/gpt-5-mini"] == "regressed"
    assert rows["anthropic/claude-sonnet-4.5"] == "unchanged"


def test_normalize_datapoint_keeps_a_recorded_tool_call():
    datapoint = SimpleNamespace(
        input={
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        },
        expected_output={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": "{}"},
                }
            ],
        },
        extra={},
    )
    normalized = eval_tasks.normalize_datapoint(datapoint)
    assistant = normalized["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["name"] == "get_weather"
    assert assistant.get("content") in (None, "")
    assert normalized["metadata"]["output_synthesized_from_reference"] is True
