"""Shared cold-start detection and inference usage recording."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.db.models import Q
from django.utils import timezone

from overbae.models import BillingService, Capability, DeployedModel, InferenceCall, User
from overbae.services.billing_ledger import charge_credits
from overbae.services.inference_pricing import estimate_call_cost

logger = logging.getLogger(__name__)

# A Modal container stays warm for its scaledown window (2 min) after the last
# request, so traffic inside this window means the GPU was almost certainly up.
_WARM_WINDOW_S = 120


def has_recent_inference(deployed: DeployedModel, seconds: int) -> bool:
    cutoff = timezone.now() - timezone.timedelta(seconds=seconds)
    return (
        bool(deployed.deployed_at and deployed.deployed_at >= cutoff)
        or deployed.inference_calls.filter(created_at__gte=cutoff, outcome="succeeded").exists()
        or deployed.activations.filter(stage="complete", completed_at__gte=cutoff).exists()
    )


def is_cold_start(deployed: DeployedModel) -> bool:
    """True when the model had no traffic inside the warm window. Never raises — a
    failed check conservatively counts the call as warm.

    Side effect: on a cold hit, stamps ``warming_started_at`` so the live-status
    endpoint can surface "Warming up" while the container boots.
    """
    try:
        cold = not has_recent_inference(deployed, _WARM_WINDOW_S)
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
    *,
    request_id: uuid.UUID | None = None,
    outcome: str = "succeeded",
    error_code: str = "",
    source: str = "internal",
    requested_model: str = "",
    started_at: datetime | None = None,
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
        cost = (
            estimate_call_cost(deployed.gpu_type, latency_ms)
            if outcome == "succeeded" or usage
            else None
        )
        call = InferenceCall.objects.create(
            deployed_model=deployed,
            project_id=deployed.project_id,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=completion_tokens,
            cost=cost,
            tokens_per_second=tps,
            latency_ms=stored_latency_ms,
            is_cold=is_cold,
            id=request_id or uuid.uuid4(),
            end_to_end_ms=latency_ms,
            outcome=outcome,
            error_code=error_code,
            source=source,
            requested_model=requested_model,
        )
        DeployedModel.objects.filter(pk=deployed.pk).update(warming_started_at=None)
        if (
            source == "application"
            and outcome == "succeeded"
            and requested_model.startswith("overmind/")
        ):
            capability_id = uuid.UUID(requested_model.removeprefix("overmind/"))
            active = Capability.objects.filter(
                pk=capability_id,
                project_id=deployed.project_id,
                active_model=deployed,
            ).filter(
                Q(active_model_activated_at__isnull=True)
                | Q(active_model_activated_at__lte=started_at or call.created_at)
            )
            active.filter(first_application_request_at__isnull=True).update(
                first_application_request_at=call.created_at
            )
            active.update(last_application_request_at=call.created_at)
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
