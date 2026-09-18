import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import validate_shared_base_serving as benchmark


def test_http_ttft_ignores_idle_pings_and_role_chunks(monkeypatch):
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_lines.return_value = iter(
        [
            b": ping",
            b"",
            b'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            b'data: {"choices":[{"delta":{"content":"391"}}]}',
            b"data: [DONE]",
        ]
    )
    monkeypatch.setattr(benchmark.requests, "post", lambda *args, **kwargs: response)
    ticks = iter([10.0, 12.0, 13.0])
    monkeypatch.setattr(benchmark.time, "monotonic", lambda: next(ticks))
    wall_ticks = iter([100.0, 103.0])
    monkeypatch.setattr(benchmark.time, "time", lambda: next(wall_ticks))
    assert benchmark.completion("http://test", "private-key", "model") == {
        "request_started_at": 100.0,
        "ttft_s": 2.0,
        "latency_s": 3.0,
        "content": "391",
    }


@pytest.mark.parametrize("lines", [[b"data: [DONE]"], [b'data: {"error":{"message":"failed"}}']])
def test_missing_content_or_error_is_not_a_latency_sample(monkeypatch, lines):
    response = MagicMock()
    response.__enter__.return_value = response
    response.iter_lines.return_value = iter(lines)
    monkeypatch.setattr(benchmark.requests, "post", lambda *args, **kwargs: response)
    with pytest.raises(RuntimeError):
        benchmark.completion("http://test", "private-key", "model")


def test_readiness_estimate_accounts_for_clock_offset_and_round_trip():
    result = benchmark.cold_readiness(
        {"request_started_at": 100.0}, {"ready_at": 250.0, "observed_at": 300.0}, 201.0, 203.0
    )
    assert result == {
        "request_to_ready_estimate_s": 52.0,
        "clock_uncertainty_s": 1.0,
        "ready_before_request": False,
    }
    assert benchmark.cold_readiness(
        {"request_started_at": 200.0}, {"ready_at": 250.0, "observed_at": 300.0}, 201.0, 203.0
    )["ready_before_request"]


@pytest.mark.parametrize("failure", [None, "identical", "recovery", "control"])
def test_adapter_diagnostic_uses_worker_rpc_without_expanding_public_api(failure):
    worker = MagicMock()
    values = [
        -5.0,
        -6.0,
        -1.0,
        -1.0,
        -1.0 if failure == "identical" else -2.0,
        -1.0 if failure == "identical" else -2.0,
        -1.0,
    ]
    if failure == "recovery":
        values[-1] = -3.0
    if failure == "control":
        values[3] = -3.0
    worker.infer.remote.side_effect = [
        {
            "status": 200,
            "body": json.dumps(
                {
                    "choices": [
                        {
                            "logprobs": {
                                "content": [
                                    {
                                        "top_logprobs": [{"token": "Paris", "logprob": value}],
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode(),
        }
        for value in values
    ]
    models = [
        SimpleNamespace(model_id=name, adapter_path=f"/weights/.adapters/{name}")
        for name in ("a", "b")
    ]
    if failure:
        with pytest.raises(
            RuntimeError, match="identical" if failure == "identical" else "recover"
        ):
            benchmark.verify_adapter_effect(worker, models)
    else:
        assert benchmark.verify_adapter_effect(worker, models)["adapter_effect_verified"]
    assert [call.kwargs["adapter"] for call in worker.infer.remote.call_args_list] == [
        ("a", ".adapters/a"),
        ("b", ".adapters/b"),
        ("a", ".adapters/a"),
        ("a", ".adapters/a"),
        ("b", ".adapters/b"),
        ("b", ".adapters/b"),
        ("a", ".adapters/a"),
    ]


def test_probability_distance_does_not_overweight_tiny_tail_logprob_changes():
    first = [("The", -0.00281236), ("Paris", -6.127812), ("**", -7.627812)]
    repeated = [("The", -0.00287857), ("Paris", -6.127879), ("**", -7.502879)]
    assert benchmark.distribution_distance(first, repeated) < 0.0001
    assert benchmark.distribution_distance(first, [("other", 0)]) > 0.99
