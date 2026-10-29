"""Collect bounded, deployment-scoped startup receipts without waking a worker."""

import asyncio
import logging
import re
from datetime import timedelta

import modal
from asgiref.sync import async_to_sync
from django.db import transaction
from django.utils import timezone

from modal_shared.operational_events import pool_key
from modal_shared.shared import rel_weights_path
from overbae.models import OperationalRun
from overbae.services import dedicated_runtime, operational_progress
from overbae.services.provider_progress import bounded_page

logger = logging.getLogger(__name__)
LABELS = {
    "starting_engine": "Starting inference engine",
    "refreshing_volumes": "Reading model storage",
    "validating_weights": "Checking model weights",
    "starting_engine_process": "Starting inference engine",
    "waiting_for_engine": "Waiting for engine",
    "preparing_snapshot_artifact": "Preparing startup snapshot",
    "snapshot_artifact_ready": "Startup snapshot ready",
    "allocating_weight_memory": "Allocating model memory",
    "restoring_weights": "Restoring model weights",
    "restored_weights": "Model weights restored",
    "allocating_kv_cache": "Allocating KV cache",
    "checking_engine_health": "Checking engine health",
    "engine_ready": "Verifying endpoint",
    "startup_failed": "Engine startup failed",
}

TERMINAL = {
    "online": ("online", "Endpoint ready", "ready"),
    "failed": ("failed", "Deployment failed", "failed"),
    "offline": ("offline", "Offline", "cancelled"),
    "shutting_down": ("shutting_down", "Stopping deployment", "running"),
}


def parse_log(message):
    # Only recognized startup messages cross the provider boundary; never copy arbitrary output.
    text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", message)
    if "Loading safetensors" in text or "Capturing CUDA graphs" in text:
        match = re.search(r"(\d+)\s*/\s*(\d+)", text)
        if not match:
            return None
        completed, total = map(int, match.groups())
        if not 0 <= completed <= total or total == 0:
            return None
        if "Loading safetensors" in text:
            return "loading_weights", "Loading model weights", completed, total, "tensors"
        kind = re.search(r"Capturing CUDA graphs \((PIECEWISE|FULL)\)", text)
        label = "Capturing CUDA graphs" + (f" ({kind[1]})" if kind else "")
        return "capturing_graphs", label, completed, total, "graphs"
    match = re.search(r"torch.compile took ([\d.]+) s", text)
    if match:
        return "compiling_model", f"Model compilation finished in {match[1]}s", None, None, None
    for fragment, stage, label in (
        ("Starting vLLM:", "starting_engine", "Starting inference engine"),
        ("Loading model from scratch", "loading_weights", "Loading model weights"),
        ("torch.compile", "compiling_model", "Compiling model"),
        ("Loaded new LoRA adapter:", "adapter_ready", "Model adapter loaded"),
        (
            "Triton kernel JIT compilation during inference",
            "warming_kernels",
            "Warming inference kernels",
        ),
        ("Available KV cache memory:", "allocating_kv_cache", "Allocating KV cache"),
        ("Application startup complete.", "checking_engine_health", "Checking engine health"),
    ):
        if fragment in text:
            return stage, label, None, None, None
    return None


def _receipt(stage, label, at, worker, completed=None, total=None, unit=None):
    return dict(
        stage=stage,
        message=label,
        at=at.isoformat(),
        worker=worker,
        completed=completed,
        total=total,
        unit=unit,
    )


async def _observe(row, state, since):
    env = dedicated_runtime.environment()
    app = await modal.App.lookup.aio(dedicated_runtime.APP_NAME, environment_name=env)
    workers = set(state.get("workers", []))
    marker = f"dedicated-base-{row.id}" if row.deployed_model.is_lora else row.served_model_id
    until = timezone.now()
    discovered = 0
    async for entry in app.logs.fetch.aio(since=since, until=until, search_text=marker):
        discovered += 1
        if f"--served-model-name {marker}" in entry.message:
            workers.update(value for value in entry.context_ids if value.startswith("ta-"))
        if discovered >= 200 or len(workers) >= 8:
            break
    if not workers:
        return {**state, "available": False, "observed_at": until.isoformat()}
    receipts = []
    after = operational_progress.timestamp(state.get("log_cursor")) or since
    count = 0
    cursor = after
    async for entry in app.logs.fetch.aio(since=after, until=until):
        count += 1
        cursor = entry.timestamp
        worker = next((value for value in entry.context_ids if value in workers), None)
        if worker:
            parsed = parse_log(entry.message)
            if parsed:
                stage, label, completed, total, unit = parsed
                receipts.append(
                    _receipt(stage, label, entry.timestamp, worker, completed, total, unit)
                )
        if count >= 2000:
            break
    state = {
        **state,
        "workers": sorted(workers),
        "log_cursor": cursor.isoformat() if count >= 2000 else until.isoformat(),
    }
    deployed = row.deployed_model
    stream = pool_key(
        dedicated_runtime.worker_class_name(deployed, row.gpu_type),
        rel_weights_path(deployed.weights_path),
        deployed.max_model_len,
        bool(deployed.is_lora),
        deployed.lora_rank or 16,
    )
    try:
        head, events = await bounded_page(env, stream, state.get("journal_cursor", {}))
    except Exception:
        logger.warning("Dedicated startup journal unavailable for %s", row.pk)
        head, events = None, []
    if head:
        state["journal_cursor"] = head
    for event in events:
        if not event:
            continue
        worker = event.get("facts", {}).get("worker_id")
        at = operational_progress.timestamp(event.get("source_at"))
        stage = event.get("stage")
        if worker not in workers or not at or at < since or stage not in LABELS:
            continue
        receipts.append(
            _receipt(
                stage,
                LABELS[stage],
                at,
                worker,
                event.get("completed"),
                event.get("total"),
                event.get("unit"),
            )
        )
    existing = state.get("logs", [])
    unique = {
        (e["at"], e["worker"], e["message"], e.get("completed")): e for e in existing + receipts
    }
    logs = sorted(unique.values(), key=lambda e: e["at"])
    state["logs"] = logs[-200:]
    state["truncated"] = state.get("truncated", False) or len(logs) > 200
    state["available"] = True
    state["observed_at"] = until.isoformat()
    # Heartbeats prove liveness, not forward progress through engine initialization.
    stages = [e for e in logs if e["stage"] != "waiting_for_engine"]
    if stages:
        state["current"] = stages[-1]
    return state


def collect(row):
    if not row.probe_call_id or row.deployed_model.inference_contract != "chat":
        return
    run, _ = OperationalRun.objects.get_or_create(
        project_id=row.project_id,
        kind="dedicated_deployment",
        reference=str(row.pk),
        attempt=row.probe_call_id,
        defaults={"snapshot": {"stage": "starting_worker", "status": "running"}},
    )
    since = run.created_at - timedelta(seconds=30)
    try:

        async def bounded():
            return await asyncio.wait_for(
                _observe(row, dict(run.provider_state), since), timeout=12
            )

        state = async_to_sync(bounded)()
    except Exception:
        logger.warning("Dedicated startup telemetry unavailable for %s", row.pk, exc_info=False)
        state = {**run.provider_state, "available": False}
    current = state.get("current") or dict(
        stage="starting_worker", message="Starting worker", completed=None, total=None, unit=None
    )
    status = "running"
    if row.status in TERMINAL:
        stage, label, status = TERMINAL[row.status]
        current = dict(stage=stage, message=label, completed=None, total=None, unit=None)
    state["current"] = current
    run = operational_progress.record(
        row.project_id,
        "dedicated_deployment",
        row.pk,
        row.probe_call_id,
        stage=current["stage"],
        status=status,
        completed=current.get("completed"),
        total=current.get("total"),
        unit=current.get("unit"),
    )
    with transaction.atomic():
        stored = OperationalRun.objects.select_for_update().get(pk=run.pk)
        stored.provider_state = state
        stored.save(update_fields=["provider_state"])


def summary(row):
    if not row.probe_call_id:
        return None
    run = (
        getattr(row, "_startup_operation", None)
        if hasattr(row, "_startup_operation")
        else OperationalRun.objects.filter(
            project_id=row.project_id,
            kind="dedicated_deployment",
            reference=str(row.pk),
            attempt=row.probe_call_id,
        ).first()
    )
    if not run:
        return None
    state = run.provider_state
    current = state.get("current") or {}
    snapshot = run.snapshot
    if row.status in TERMINAL:
        stage, label, _ = TERMINAL[row.status]
        current = {"message": label}
        snapshot = {"stage": stage}
    return dict(
        operation_id=str(run.pk),
        stage=snapshot.get("stage", "starting_worker"),
        label=current.get("message", "Starting worker"),
        completed=snapshot.get("completed"),
        total=snapshot.get("total"),
        unit=snapshot.get("unit"),
        available=state.get("available", False),
        observed_at=state.get("observed_at"),
        logs=state.get("logs", []),
        truncated=state.get("truncated", False),
    )
