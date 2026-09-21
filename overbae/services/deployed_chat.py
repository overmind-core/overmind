"""Non-stream chat against a READY deployed fine-tune.

Shared by the OpenAI-compatible completions gateway and MCP ``run_inference``
so cold-start detection and InferenceCall recording
stay on one path.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

from modal_shared.context_budget import INCOMPLETE_FINISH_REASONS
from overbae.models import BillingService, DeployedModel, InferenceCall, User
from overbae.services.billing_ledger import charge_credits
from overbae.services.inference_client import (
    InferenceClientError,
    get_inference_client,
)
from overbae.services.inference_pricing import estimate_call_cost

logger = logging.getLogger(__name__)

# A Modal container stays warm for its scaledown window (2 min) after the last
# request, so traffic inside this window means the GPU was almost certainly up.
_WARM_WINDOW_S = 120


def is_cold_start(deployed: DeployedModel) -> bool:
    """True when the model had no traffic inside the warm window. Never raises — a
    failed check conservatively counts the call as warm.

    Side effect: on a cold hit, stamps ``warming_started_at`` so the live-status
    endpoint can surface "Warming up" while the container boots.
    """
    from django.utils import timezone

    try:
        cutoff = timezone.now() - timezone.timedelta(seconds=_WARM_WINDOW_S)
        cold = not deployed.inference_calls.filter(created_at__gte=cutoff).exists()
        if cold:
            DeployedModel.objects.filter(pk=deployed.pk).update(warming_started_at=timezone.now())
        return cold
    except Exception:
        logger.exception("Cold-start check failed for %s", deployed.model_id)
        return False


def _f(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def record_inference_call(
    deployed: DeployedModel,
    usage: dict | None,
    latency_ms: float | None,
    is_cold: bool = False,
    user: User | None = None,
    metrics: dict | None = None,
) -> None:
    """Persist one InferenceCall. Never raises.

    ``metrics`` is vLLM's per-request timing block and wins when present: warm
    ``latency_ms`` becomes ``time_to_first_token_ms + generation_time_ms``, which
    excludes queue wait, network and container boot. COLD calls keep the gateway
    wall-clock so the boot overhead still feeds ``cold_start_ms``; wall-clock also
    drives the cost/GPU-time proxy and is the fallback when vLLM sends no metrics
    (``n > 1``/multi-prompt, or an older worker).
    """
    usage = usage or {}
    metrics = metrics or {}
    if not usage and latency_ms is None and not metrics:
        return
    completion_tokens = int(usage.get("completion_tokens") or 0)

    tps = _f(metrics.get("tokens_per_second"))
    if tps is None and latency_ms and completion_tokens:
        tps = round(completion_tokens / (latency_ms / 1000.0), 2)

    ttft = _f(metrics.get("time_to_first_token_ms"))
    gen = _f(metrics.get("generation_time_ms"))
    if not is_cold and ttft is not None and gen is not None:
        stored_latency_ms = round(ttft + gen, 1)
    else:
        stored_latency_ms = latency_ms

    try:
        cost = estimate_call_cost(deployed.gpu_type, latency_ms)
        call = InferenceCall.objects.create(
            deployed_model=deployed,
            project_id=deployed.project_id,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=completion_tokens,
            cost=cost,
            tokens_per_second=tps,
            latency_ms=stored_latency_ms,
            is_cold=is_cold,
        )
        DeployedModel.objects.filter(pk=deployed.pk).update(warming_started_at=None)
        if user is not None and cost:
            charge_credits(
                user,
                Decimal(str(cost)),
                BillingService.INFERENCE_FT_MODEL,
                project_id=deployed.project_id,
                idempotency_key=f"inference-ft:{call.id}",
                metadata={"inference_call_id": str(call.id), "model_id": deployed.model_id},
            )
    except Exception:
        logger.exception("Failed to record InferenceCall for %s", deployed.model_id)


def chat_with_deployed_model(
    *,
    deployed: DeployedModel,
    messages: list[dict],
    user: User | None = None,
    temperature: float = 1.0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Non-stream chat completion against a READY deployed model.

    Returns ``{content, usage, latency_ms, is_cold, model_id}`` on success,
    or ``{error: ...}`` when the model is not ready / the gateway fails.
    """
    if deployed.status != DeployedModel.Status.READY:
        return {
            "error": f"Model '{deployed.model_id}' is not ready (status: {deployed.status}).",
        }

    cold = is_cold_start(deployed)
    client = get_inference_client()
    t_start = time.monotonic()
    try:
        result = client.chat_completions(
            model_id=deployed.model_id,
            messages=messages,
            stream=False,
            temperature=temperature,
            max_tokens=max_tokens,
            deployed=deployed,
            include_metrics=True,
        )
    except InferenceClientError as exc:
        logger.error("Inference error for model %s: %s", deployed.model_id, exc)
        return {"error": f"Inference backend error: {exc}"}

    latency_ms = round((time.monotonic() - t_start) * 1000, 1)
    usage = result.get("usage") if isinstance(result, dict) else None
    call_metrics = result.pop("metrics", None) if isinstance(result, dict) else None
    record_inference_call(deployed, usage, latency_ms, cold, user=user, metrics=call_metrics)

    content = ""
    finish_reason = None
    if isinstance(result, dict):
        choices = result.get("choices") or []
        if choices:
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            finish_reason = choices[0].get("finish_reason")

    return {
        "model_id": deployed.model_id,
        "content": content,
        "usage": usage,
        "latency_ms": latency_ms,
        "is_cold": cold,
        "finish_reason": finish_reason,
        "truncated": finish_reason in INCOMPLETE_FINISH_REASONS,
    }
