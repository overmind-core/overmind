"""vLLM 0.25 (`--enable-per-request-metrics` + `include_metrics`) returns a `metrics`
block. The gateway prefers it over wall-clock, except on cold calls where boot time
must still land in cold_start_ms.
"""

from __future__ import annotations

import uuid

import pytest

from overbae.api.completions import _process_stream_chunk
from overbae.models import Project
from overbae.models.inference import DeployedModel, InferenceCall
from overbae.services.deployed_chat import record_inference_call

pytestmark = pytest.mark.django_db


def _model() -> DeployedModel:
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    return DeployedModel.objects.create(
        project=project,
        model_id=f"ft-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
    )


def test_warm_call_uses_vllm_metrics_not_wall_clock() -> None:
    m = _model()
    record_inference_call(
        m,
        usage={"prompt_tokens": 10, "completion_tokens": 100},
        latency_ms=5000.0,  # inflated wall-clock (network + queue) — must be ignored
        is_cold=False,
        metrics={
            "tokens_per_second": 103.2,
            "time_to_first_token_ms": 85.2,
            "generation_time_ms": 1240.5,
        },
    )
    call = InferenceCall.objects.get(deployed_model=m)
    assert call.tokens_per_second == pytest.approx(103.2)
    assert call.latency_ms == pytest.approx(85.2 + 1240.5)


def test_cold_call_keeps_wall_clock_for_boot_overhead() -> None:
    m = _model()
    record_inference_call(
        m,
        usage={"completion_tokens": 100},
        latency_ms=42000.0,  # includes container boot
        is_cold=True,
        metrics={
            "tokens_per_second": 100.0,
            "time_to_first_token_ms": 80,
            "generation_time_ms": 900,
        },
    )
    call = InferenceCall.objects.get(deployed_model=m)
    assert call.tokens_per_second == pytest.approx(100.0)
    assert call.latency_ms == pytest.approx(42000.0)


def test_falls_back_to_wall_clock_without_metrics() -> None:
    m = _model()
    record_inference_call(
        m,
        usage={"completion_tokens": 200},
        latency_ms=1000.0,
        is_cold=False,
        metrics=None,
    )
    call = InferenceCall.objects.get(deployed_model=m)
    assert call.latency_ms == pytest.approx(1000.0)
    assert call.tokens_per_second == pytest.approx(200.0 / 1.0)


def test_record_clears_warming_stamp() -> None:
    from django.utils import timezone

    m = _model()
    DeployedModel.objects.filter(pk=m.pk).update(warming_started_at=timezone.now())
    record_inference_call(m, usage={"completion_tokens": 1}, latency_ms=10.0)
    m.refresh_from_db()
    assert m.warming_started_at is None


def test_stream_chunk_captures_and_strips_metrics() -> None:
    captured: dict = {"usage": None, "metrics": None}
    final = (
        'data: {"choices": [], "usage": {"completion_tokens": 5}, '
        '"metrics": {"tokens_per_second": 50.0}}\n\n'
    )
    forwarded = _process_stream_chunk(final, captured, include_usage=True)
    assert captured["metrics"] == {"tokens_per_second": 50.0}
    assert captured["usage"] == {"completion_tokens": 5}
    assert forwarded is not None
    assert "metrics" not in forwarded
    assert "usage" in forwarded


def test_stream_chunk_dropped_when_client_did_not_opt_in() -> None:
    captured: dict = {"usage": None, "metrics": None}
    final = 'data: {"choices": [], "usage": {"completion_tokens": 5}, "metrics": {"tokens_per_second": 50.0}}\n\n'
    forwarded = _process_stream_chunk(final, captured, include_usage=False)
    assert captured["metrics"] == {"tokens_per_second": 50.0}
    assert forwarded is None
