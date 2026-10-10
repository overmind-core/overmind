from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import modal
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.utils import timezone

from modal_shared.modelfam import serve_image_key
from modal_shared.shared import (
    INFERENCE_APP_NAME,
    base_pool_name,
    rel_weights_path,
    worker_cls_name,
)
from overbae.models import DeployedModel
from overbae.services.deployed_chat import has_recent_inference
from overbae.services.deployment import modal_environment, serving_gpu

logger = logging.getLogger(__name__)

WORKER_STATS_TTL = 5
WORKER_STATS_TIMEOUT = 10


def worker_status(deployed: DeployedModel) -> dict:
    stats = live_worker_stats(deployed)
    state = "unknown"
    if deployed.status == "ready":
        if stats["recently_active"]:
            state = "warm"
        elif stats["warming"]:
            state = "warming"
        elif stats["available"] and stats["num_total_runners"] is not None:
            state = "warm" if stats["num_total_runners"] > 0 else "asleep"
    return {"state": state, **stats}


async def _read_worker_stats(environment, cls_name, parameters):
    parameters = dict(parameters)
    if parameters["enable_lora"]:
        path = parameters["model_path"]
        identity_key = f"serving-base-identity:{environment}:{path}"
        identity = cache.get(identity_key)
        if identity is None:
            volume = modal.Volume.from_name("overmind-weights", environment_name=environment)
            chunks = [chunk async for chunk in volume.read_file.aio(f"{path}/.base-manifest.json")]
            identity = json.loads(b"".join(chunks))["identity"]
            if not isinstance(identity, str) or not identity:
                raise ValueError("Serving base manifest has no identity")
            cache.set(identity_key, identity, timeout=86400)
        parameters["base_identity"] = identity

    worker = modal.Cls.from_name(INFERENCE_APP_NAME, cls_name, environment_name=environment)(
        **parameters
    )
    # Binding the method selects the parameter-specific pool without invoking it.
    stats = await worker.infer.get_current_stats.aio()
    return {
        "backlog": stats.backlog,
        "num_running_inputs": stats.num_running_inputs,
        "num_total_runners": stats.num_total_runners,
        "available": True,
    }


async def _bounded_worker_stats(environment, cls_name, parameters):
    return await asyncio.wait_for(
        _read_worker_stats(environment, cls_name, parameters), timeout=WORKER_STATS_TIMEOUT
    )


def live_worker_stats(deployed: DeployedModel) -> dict:
    signals = {
        "recently_active": has_recent_inference(deployed, 90),
        "warming": bool(
            deployed.warming_started_at
            and timezone.now() - deployed.warming_started_at < timezone.timedelta(seconds=600)
        ),
    }
    unavailable = {
        "backlog": None,
        "num_running_inputs": None,
        "num_total_runners": None,
        "available": False,
    }
    if not deployed.gpu_type or not deployed.weights_path or deployed.status != "ready":
        return {**unavailable, **signals}

    try:
        environment = modal_environment()
        lora = bool(deployed.adapter_path)
        path = rel_weights_path(deployed.weights_path)
        cls_name = worker_cls_name(
            serving_gpu(deployed),
            serve_image_key(deployed.base_model_id, deployed.model_id),
            enable_lora=lora,
        )
        parameters = {
            "model_path": path,
            "model_name": base_pool_name(path) if lora else deployed.model_id,
            "max_model_len": deployed.max_model_len,
            "enable_lora": lora,
            "max_lora_rank": (deployed.lora_rank or 16) if lora else 16,
        }
        pool = json.dumps([environment, cls_name, parameters], sort_keys=True)
        key = f"serving-worker-stats:{hashlib.sha256(pool.encode()).hexdigest()}"
        measurements = cache.get(key)
        if measurements is None:
            try:
                measurements = async_to_sync(_bounded_worker_stats)(
                    environment, cls_name, parameters
                )
            except Exception:
                logger.warning("Worker stats unavailable for %s", deployed.model_id, exc_info=True)
                measurements = unavailable
            cache.set(key, measurements, timeout=WORKER_STATS_TTL)
        return {**measurements, **signals}
    except Exception:
        logger.warning("Worker stats unavailable for %s", deployed.model_id, exc_info=True)
        return {**unavailable, **signals}
