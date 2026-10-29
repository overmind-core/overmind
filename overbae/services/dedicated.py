"""Start, scale, meter, and stop a private dedicated deployment."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from overbae.core.errors import InputValidationError
from overbae.models import BillingService, DedicatedDeployment, DeployedModel
from overbae.services import dedicated_runtime
from overbae.services.billing_ledger import balance_usd, charge_credits
from overbae.services.billing_provider import commercial_billing_enabled
from overbae.services.dedicated_offer import credits_per_hour, hourly_usd, offers, serving_shape
from overbae.services.dedicated_offer import gpu_names_for as offered_gpus
from overbae.services.provider_pricing import current_rates

logger = logging.getLogger(__name__)

MAX_REPLICAS = 8
MIN_IDLE_SECONDS = 5 * 60
MAX_IDLE_SECONDS = 24 * 60 * 60
DEFAULT_IDLE_SECONDS = 15 * 60
PRICE_UNAVAILABLE = "Dedicated GPU price is unavailable."
NOT_READY = "Dedicated deployment requires a ready model."
ALREADY_RUNNING = "This model already has a dedicated deployment."
GPU_UNAVAILABLE = "This GPU cannot run this model."
BALANCE_TOO_LOW = "Not enough credits for one minute at this replica floor."
NOT_ONLINE = "Dedicated deployment is not online."


def gpu_offers(deployed):
    return offers(deployed, current_rates())


def latest_for(deployed):
    return (
        DedicatedDeployment.objects.select_related("deployed_model")
        .filter(deployed_model=deployed)
        .order_by("-created_at")
        .first()
    )


def latest_rows(project_ids):
    latest = (
        DedicatedDeployment.objects.filter(project_id__in=project_ids)
        .order_by("deployed_model_id", "-created_at")
        .distinct("deployed_model_id")
        .values("id")
    )
    return (
        DedicatedDeployment.objects.filter(id__in=latest)
        .select_related("deployed_model", "deployed_model__finetuning_job")
        .order_by("-updated_at")
    )


def live_for(deployed):
    return (
        DedicatedDeployment.objects.select_related("deployed_model")
        .filter(deployed_model=deployed, status__in=DedicatedDeployment.LIVE)
        .order_by("-created_at")
        .first()
    )


def lookup(model_id: str, project_ids):
    if not str(model_id).startswith("dedicated/"):
        return None
    token = str(model_id).removeprefix("dedicated/")
    rows = DedicatedDeployment.objects.select_related("deployed_model").filter(
        project_id__in=project_ids
    )
    try:
        pk = uuid.UUID(token)
    except ValueError:
        pk = None
    if pk is not None:
        return rows.filter(pk=pk).first()
    return (
        rows.filter(deployed_model__model_id=token, status__in=DedicatedDeployment.LIVE)
        .order_by("-created_at")
        .first()
    )


def _bounds(min_replicas: int, max_replicas: int, scale_to_zero: bool, idle_seconds: int):
    if scale_to_zero:
        min_replicas = 0
    elif min_replicas < 1:
        raise InputValidationError("Scale to zero is off, so the minimum is at least 1 replica.")
    if not 0 <= min_replicas <= max_replicas <= MAX_REPLICAS:
        raise InputValidationError("Replicas run from the minimum up to at most 8.")
    if not MIN_IDLE_SECONDS <= idle_seconds <= MAX_IDLE_SECONDS:
        raise InputValidationError("Idle timeout is between 5 minutes and 24 hours.")
    return min_replicas, max_replicas, idle_seconds


def _quote(gpu_type: str):
    card = current_rates()
    price = hourly_usd(card, gpu_type)
    credits = credits_per_hour(price)
    if price is None or credits is None:
        raise InputValidationError(PRICE_UNAVAILABLE)
    fetched = card.get("fetched_at")
    parsed = parse_datetime(fetched) if isinstance(fetched, str) else None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, UTC)
    return price, credits, parsed


def _require_floor(user, price: Decimal, minimum: int) -> None:
    if not commercial_billing_enabled() or minimum < 1 or user is None:
        return
    if balance_usd(user) < dedicated_runtime.minute_usd(price, minimum):
        raise InputValidationError(BALANCE_TOO_LOW)


def start(
    deployed,
    user,
    *,
    gpu_type: str,
    min_replicas: int = 1,
    max_replicas: int = 1,
    scale_to_zero: bool = False,
    idle_shutdown_seconds: int = DEFAULT_IDLE_SECONDS,
):
    if deployed.status != DeployedModel.Status.READY:
        raise InputValidationError(NOT_READY)
    if gpu_type not in offered_gpus(deployed):
        raise InputValidationError(GPU_UNAVAILABLE)
    minimum, maximum, idle = _bounds(
        min_replicas, max_replicas, scale_to_zero, idle_shutdown_seconds
    )
    price, credits, fetched = _quote(gpu_type)
    _require_floor(user, price, minimum)
    try:
        return DedicatedDeployment.objects.create(
            project_id=deployed.project_id,
            deployed_model=deployed,
            started_by=user,
            gpu_type=gpu_type,
            serving_shape=serving_shape(deployed),
            min_replicas=minimum,
            max_replicas=maximum,
            scale_to_zero=minimum == 0,
            idle_shutdown_seconds=idle,
            modal_app=dedicated_runtime.APP_NAME,
            rate_usd_per_gpu_hour=price,
            credits_per_gpu_hour=credits,
            rates_fetched_at=fetched,
            next_poll_at=timezone.now(),
        )
    except IntegrityError as exc:
        raise InputValidationError(ALREADY_RUNNING) from exc


def update(
    row: DedicatedDeployment,
    *,
    gpu_type: str | None = None,
    min_replicas: int | None = None,
    max_replicas: int | None = None,
    scale_to_zero: bool | None = None,
    idle_shutdown_seconds: int | None = None,
):
    if row.status not in DedicatedDeployment.LIVE:
        raise InputValidationError(ALREADY_RUNNING)
    gpu = gpu_type or row.gpu_type
    if gpu not in offered_gpus(row.deployed_model):
        raise InputValidationError(GPU_UNAVAILABLE)
    minimum, maximum, idle = _bounds(
        row.min_replicas if min_replicas is None else min_replicas,
        row.max_replicas if max_replicas is None else max_replicas,
        row.scale_to_zero if scale_to_zero is None else scale_to_zero,
        row.idle_shutdown_seconds if idle_shutdown_seconds is None else idle_shutdown_seconds,
    )
    price, credits, fetched = (
        row.rate_usd_per_gpu_hour,
        row.credits_per_gpu_hour,
        row.rates_fetched_at,
    )
    if gpu != row.gpu_type:
        dedicated_runtime.release(row, row.gpu_type)
        price, credits, fetched = _quote(gpu)
        _require_floor(row.started_by, price, minimum)
    DedicatedDeployment.objects.filter(pk=row.pk).update(
        gpu_type=gpu,
        min_replicas=minimum,
        max_replicas=maximum,
        scale_to_zero=minimum == 0,
        idle_shutdown_seconds=idle,
        rate_usd_per_gpu_hour=price,
        credits_per_gpu_hour=credits,
        rates_fetched_at=fetched,
        status=DedicatedDeployment.Status.DEPLOYING if gpu != row.gpu_type else row.status,
        probe_call_id="" if gpu != row.gpu_type else row.probe_call_id,
        online_at=None if gpu != row.gpu_type else row.online_at,
        next_poll_at=timezone.now(),
    )
    row.refresh_from_db()
    return row


def stop(row: DedicatedDeployment):
    if row.status in (DedicatedDeployment.Status.OFFLINE, DedicatedDeployment.Status.FAILED):
        return row
    DedicatedDeployment.objects.filter(pk=row.pk).update(
        status=DedicatedDeployment.Status.SHUTTING_DOWN,
        min_replicas=0,
        next_poll_at=timezone.now(),
    )
    row.refresh_from_db()
    return row


def due():
    now = timezone.now()
    return DedicatedDeployment.objects.filter(status__in=DedicatedDeployment.LIVE).filter(
        Q(next_poll_at__isnull=True) | Q(next_poll_at__lte=now)
    )


def _meter(row: DedicatedDeployment, now) -> None:
    if row.last_billed_at is None or row.started_by_id is None:
        return
    minutes = int((now - row.last_billed_at).total_seconds() // 60)
    if minutes <= 0:
        return
    replicas = row.running_replicas
    if replicas:
        usd = dedicated_runtime.minute_usd(row.rate_usd_per_gpu_hour, replicas) * minutes
        charged = charge_credits(
            row.started_by,
            usd,
            BillingService.DEDICATED_INFERENCE,
            project_id=row.project_id,
            idempotency_key=f"dedicated:{row.id}:{row.last_billed_at.isoformat()}",
            metadata={"replicas": replicas, "minutes": minutes, "gpu_type": row.gpu_type},
        )
        if charged is not None:
            row.accrued_credits += (usd * 100).quantize(Decimal("0.01"))
    row.last_billed_at = row.last_billed_at + timedelta(minutes=minutes)


def _broke(row: DedicatedDeployment) -> bool:
    if not commercial_billing_enabled() or row.running_replicas < 1 or row.started_by_id is None:
        return False
    return balance_usd(row.started_by) < dedicated_runtime.minute_usd(
        row.rate_usd_per_gpu_hour, row.running_replicas
    )


def _fail(row: DedicatedDeployment, message: str, now) -> None:
    _meter(row, now)
    row.status = DedicatedDeployment.Status.FAILED
    row.error_message = message
    row.stopped_at = now
    row.running_replicas = 0


def advance(deployment_id: str) -> None:
    now = timezone.now()
    with transaction.atomic():
        row = (
            DedicatedDeployment.objects.select_for_update(of=("self",))
            .select_related("deployed_model", "started_by")
            .filter(pk=deployment_id)
            .first()
        )
        if row is None or row.status not in DedicatedDeployment.LIVE:
            return
        decision = row.deployed_model.inference_contract == "decision"
        stopping = row.status == DedicatedDeployment.Status.SHUTTING_DOWN
        try:
            instance = dedicated_runtime.bind(
                row,
                minimum=0 if stopping else row.min_replicas,
                maximum=row.max_replicas,
                idle_seconds=2 if stopping else row.idle_shutdown_seconds,
            )
            if not stopping and not decision:
                dedicated_runtime.retire_previous_options(row)
            if row.status == DedicatedDeployment.Status.DEPLOYING:
                row.probe_call_id = dedicated_runtime.spawn_probe(instance, row)
                row.status = DedicatedDeployment.Status.WARMING
                row.running_replicas = max(row.min_replicas, 1)
                row.last_billed_at = row.last_billed_at or now
            elif row.status == DedicatedDeployment.Status.WARMING:
                state = dedicated_runtime.probe_state(row.probe_call_id)
                if state == "failed":
                    _fail(row, dedicated_runtime.WORKER_UNAVAILABLE, now)
                elif state == "ok":
                    count = dedicated_runtime.runner_count(instance, decision=decision)
                    row.running_replicas = row.min_replicas if count is None else count
                    if row.running_replicas < 1 and row.min_replicas:
                        row.running_replicas = row.min_replicas
                    row.status = DedicatedDeployment.Status.ONLINE
                    row.online_at = row.online_at or now
                    _meter(row, now)
                else:
                    _meter(row, now)
            else:
                count = dedicated_runtime.runner_count(instance, decision=decision)
                if count is not None:
                    row.running_replicas = count
                _meter(row, now)
                if stopping and count == 0:
                    row.status = DedicatedDeployment.Status.OFFLINE
                    row.stopped_at = now
                elif not stopping and _broke(row):
                    row.status = DedicatedDeployment.Status.SHUTTING_DOWN
                    row.min_replicas = 0
        except InputValidationError as exc:
            _fail(row, exc.detail, now)
        except Exception as exc:
            logger.exception("Dedicated deployment %s failed", row.pk)
            _fail(row, str(exc).strip() or dedicated_runtime.WORKER_UNAVAILABLE, now)
        row.next_poll_at = now + timedelta(seconds=15)
        row.save()


def serve_chat(row: DedicatedDeployment, body: bytes):
    if row.deployed_model.inference_contract == "decision":
        raise InputValidationError(
            "This model returns decision probabilities. Call POST /api/v1/systemone."
        )
    if row.status != DedicatedDeployment.Status.ONLINE:
        raise InputValidationError(NOT_ONLINE)
    instance = dedicated_runtime.bind(
        row,
        minimum=row.min_replicas,
        maximum=row.max_replicas,
        idle_seconds=row.idle_shutdown_seconds,
    )
    return dedicated_runtime.chat(instance, row, body)


def serve_decision(row: DedicatedDeployment, decisions: list) -> dict:
    if row.deployed_model.inference_contract != "decision":
        raise InputValidationError("This dedicated deployment is a chat model.")
    if row.status != DedicatedDeployment.Status.ONLINE:
        raise InputValidationError(NOT_ONLINE)
    instance = dedicated_runtime.bind(
        row,
        minimum=row.min_replicas,
        maximum=row.max_replicas,
        idle_seconds=row.idle_shutdown_seconds,
    )
    return dedicated_runtime.predict(instance, decisions)


def complete_request(payload: dict) -> dict:
    row = DedicatedDeployment.objects.select_related("deployed_model").get(
        pk=payload["dedicated_id"]
    )
    started = timezone.now()
    if row.deployed_model.inference_contract == "decision":
        result = serve_decision(row, payload["decisions"])
        result["latency_ms"] = result.get("latency_ms") or 0
        return result
    body = {
        "model": row.public_model_id,
        "messages": payload["messages"],
        "temperature": payload.get("temperature", 1),
    }
    if payload.get("max_tokens"):
        body["max_tokens"] = payload["max_tokens"]
    status, raw, _content_type = serve_chat(row, json.dumps(body).encode())
    if status >= 400:
        raise InputValidationError(dedicated_runtime.WORKER_UNAVAILABLE)
    value = json.loads(raw)
    choice = (value.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {
        "content": message.get("content") or "",
        "usage": value.get("usage"),
        "finish_reason": choice.get("finish_reason"),
        "latency_ms": (timezone.now() - started).total_seconds() * 1000,
    }
