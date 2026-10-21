import asyncio
import hashlib
import json
import logging
import uuid
from datetime import timedelta

import modal
from asgiref.sync import async_to_sync
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from modal_shared.context_budget import (
    CONTEXT_BUDGET_MESSAGE,
    DEFAULT_OUTPUT_TOKENS,
    INCOMPLETE_FINISH_REASONS,
)
from modal_shared.operational_events import STORE
from overbae.core.errors import InputValidationError
from overbae.models import InferenceCall, InferenceRequest, Project
from overbae.services import operational_progress
from overbae.services.decision_serving import validate_requests
from overbae.services.deployed_chat import is_cold_start, record_inference_call
from overbae.services.deployment import modal_environment, poll_operation
from overbae.services.provider_progress import collect

logger = logging.getLogger(__name__)
ACTIVE = ("queued", "submitting", "running", "submission_unknown", "unresolved")
FINISHED = ("succeeded", "failed")


def recover_existing(deployment, request_key, payload):
    request = InferenceRequest.objects.filter(
        project_id=deployment.project_id, request_key=request_key
    ).first()
    if request is None:
        return None
    inputs = request_inputs(deployment, payload)
    inputs["deployment"] = str(deployment.pk)
    if any(request.payload.get(key) != value for key, value in inputs.items()):
        raise InputValidationError("Request key is already bound to different inference inputs.")
    return request


def request_inputs(deployment, payload):
    if deployment.inference_contract == "decision":
        if (
            payload.get("messages")
            or payload.get("max_tokens") is not None
            or payload.get("temperature") is not None
        ):
            raise InputValidationError(
                "Decision models accept decisions, not chat generation parameters."
            )
        return {"decisions": validate_requests(payload.get("decisions"))}
    if payload.get("decisions") is not None or not payload.get("messages"):
        raise InputValidationError("Chat models require messages, not decisions.")
    return {
        "messages": payload["messages"],
        "temperature": payload.get("temperature")
        if payload.get("temperature") is not None
        else 1.0,
        "max_tokens": payload.get("max_tokens") or DEFAULT_OUTPUT_TOKENS,
    }


def submit(deployment, user, request_key, payload, *, source="mcp"):
    existing = recover_existing(deployment, request_key, payload)
    if existing:
        return existing
    inputs = request_inputs(deployment, payload)
    if deployment.status != "ready":
        raise InputValidationError("Inference requires a ready deployment.")
    if inputs.get("max_tokens", 0) >= deployment.max_model_len:
        raise InputValidationError(CONTEXT_BUDGET_MESSAGE)
    payload = {
        **inputs,
        "deployment": str(deployment.pk),
        "environment": modal_environment(),
        "model_id": deployment.model_id,
        "weights_path": deployment.weights_path,
        "gpu_type": deployment.gpu_type,
        "max_model_len": deployment.max_model_len,
        "lora_rank": deployment.lora_rank,
        "adapter_path": deployment.adapter_path,
        "inference_contract": deployment.inference_contract,
        "source": source,
    }
    if deployment.inference_contract == "decision":
        payload.update(
            environment=deployment.serving_runtime["environment"],
            serving_app=deployment.serving_runtime["app"],
            artifact_identity=deployment.checkpoint_hash,
        )
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=deployment.project_id)
        old = recover_existing(deployment, request_key, payload)
        if old:
            return old
        now = timezone.now()
        request = InferenceRequest.objects.create(
            project_id=deployment.project_id,
            deployment=deployment,
            user=user,
            request_key=request_key,
            fingerprint=fingerprint,
            payload=payload,
            deadline=now + timedelta(minutes=50),
            next_poll_at=now,
        )
        observe(request)
        transaction.on_commit(lambda: _follow(request))
        return request


async def bounded_call(method, *args, **kwargs):
    return await asyncio.wait_for(method(*args, **kwargs), timeout=10)


def spawn(request):
    function = modal.Function.from_name(
        request.payload.get("serving_app", "overmind-inference"),
        "inference_request",
        environment_name=request.payload["environment"],
    )
    return async_to_sync(bounded_call)(
        function.spawn.aio, str(request.pk), request.payload
    ).object_id


def recover(request):
    store = modal.Dict.from_name(STORE, environment_name=request.payload["environment"])
    return async_to_sync(bounded_call)(store.get.aio, "request:" + str(request.pk), None)


def poll(call_id):
    return poll_operation(call_id)


def observe(request):
    return operational_progress.record(
        request.project_id,
        "inference_request",
        request.pk,
        request.pk,
        stage=request.state,
        status=request.state,
        facts={
            "provider": "modal",
            "provider_call_id": request.remote_id or None,
            "model_id": request.payload["model_id"],
            "deadline": request.deadline.isoformat(),
            "retry_safe": False,
            "error_code": request.error_code or None,
        },
    )


def _follow(request):
    # Beat stays the safety net. Waiting for it leaves a warm call unfinished for ~45s.
    delay = 0 if request.state == "queued" else 1 if request.state == "running" else None
    if delay is None:
        return
    try:
        from overbae.tasks.inference_requests import inspect_inference_request

        inspect_inference_request.apply_async(args=[str(request.pk)], countdown=delay)
    except Exception:
        logger.warning("Inference follow-up not queued for %s", request.pk, exc_info=False)


def advance(request_id):
    now, claim = timezone.now(), uuid.uuid4()
    eligible = InferenceRequest.objects.filter(pk=request_id, state__in=ACTIVE).filter(
        Q(claim_until__isnull=True) | Q(claim_until__lte=now)
    )
    if not eligible.update(claim=claim, claim_until=now + timedelta(seconds=45)):
        finalize(request_id)
        return
    request = InferenceRequest.objects.select_related("deployment", "user").get(pk=request_id)
    owned = InferenceRequest.objects.filter(pk=request.pk, claim=claim)
    changes = {}
    try:
        if request.deadline <= now and request.state != "unresolved":
            changes = {
                "state": "unresolved",
                "error_code": "observation_deadline",
            }
        elif request.state == "queued":
            owned.update(state="submitting", updated_at=now)
            request.is_cold = is_cold_start(request.deployment)
            owned.update(is_cold=request.is_cold)
            try:
                handle = spawn(request)
                changes = {"remote_id": handle, "state": "running"}
            except Exception:
                changes = {"state": "submission_unknown", "error_code": "acknowledgement_unknown"}
        elif not request.remote_id:
            handle = recover(request)
            changes = (
                {"remote_id": handle, "state": "running", "error_code": ""}
                if handle
                else {"state": "submission_unknown"}
            )
        else:
            state, result = poll(request.remote_id)
            if state == "complete":
                if not isinstance(result, dict):
                    changes = {
                        "state": "failed",
                        "error_code": "invalid_provider_result",
                        "completed_at": now,
                    }
                elif result.get("error_code"):
                    code = (
                        result["error_code"]
                        if result["error_code"] in {"context_length_exceeded", "inference_rejected"}
                        else "provider_failed"
                    )
                    changes = {"state": "failed", "error_code": code, "completed_at": now}
                else:
                    result = {
                        key: result.get(key)
                        for key in (
                            "content",
                            "usage",
                            "finish_reason",
                            "latency_ms",
                            "predictions",
                            "model_identity",
                        )
                    }
                    result["truncated"] = result["finish_reason"] in INCOMPLETE_FINISH_REASONS
                    changes = {
                        "state": "succeeded",
                        "result": result,
                        "completed_at": now,
                        "error_code": "",
                    }
            elif state == "failed":
                changes = {"state": "failed", "error_code": "provider_failed", "completed_at": now}
    except Exception:
        logger.warning(
            "Inference observation unavailable for %s; retaining identity",
            request.pk,
            exc_info=False,
        )
    finally:
        persisted = owned.update(
            **changes,
            claim=None,
            claim_until=None,
            next_poll_at=now + timedelta(seconds=300 if request.deadline <= now else 15),
            updated_at=timezone.now(),
        )
        if persisted:
            request.refresh_from_db()
            _follow(request)
            operation = observe(request)
            if request.remote_id:
                collect(
                    operation,
                    environment=request.payload["environment"],
                    call_id=request.remote_id,
                    deployed=request.deployment,
                )
            finalize(request.pk)


def finalize(request_id):
    with transaction.atomic():
        request = (
            InferenceRequest.objects.select_related("deployment", "user")
            .select_for_update(of=("self",))
            .filter(pk=request_id, state__in=FINISHED)
            .first()
        )
        if request and not InferenceCall.objects.filter(pk=request.pk).exists():
            record_inference_call(
                request.deployment,
                request.result.get("usage"),
                request.result.get("latency_ms")
                or ((request.completed_at or timezone.now()) - request.created_at).total_seconds()
                * 1000,
                request.is_cold,
                user=request.user,
                request_id=request.pk,
                outcome=request.state,
                error_code=request.error_code,
                source=request.payload.get("source", "mcp"),
            )


def describe(request):
    result = dict(request.result)
    content = str(result.get("content") or "")
    if result:
        result.update(
            content=content[:32000],
            content_clipped=len(content) > 32000,
            content_characters=len(content),
        )
    return {
        "id": str(request.pk),
        "status": request.state,
        "request_key": request.request_key,
        "model_id": request.payload["model_id"],
        "provider_call_id": request.remote_id or None,
        "result": result,
        "error_code": request.error_code or None,
        "deadline": request.deadline.isoformat(),
        "retry_safe": False,
        "is_cold": request.is_cold,
    }
