"""Shared Modal constants used by workers, Django views, and smoke scripts.

Single source for GPU-class map and weights-path helpers so the four previous
copies cannot drift (views.py used to key A100 as ``"A100"`` while
gpu_selector emits ``"A100-80GB"``).
"""

from __future__ import annotations

import os

from modal_shared.stacks import (  # noqa: F401
    GPU_CLASS_MAP,
    SERVE_CLASS_MAP,
    worker_cls_name,
)

WEIGHTS_MOUNT = "/weights"
INFERENCE_APP_NAME = "overmind-inference"

# Django → InferenceAPIServer routing. Postgres is SoT; the gateway must not
# look these up from a Volume JSON (concurrent Volume commits last-writer-win).
GPU_TYPE_HEADER = "X-Overmind-Gpu-Type"
WEIGHTS_PATH_HEADER = "X-Overmind-Weights-Path"
MAX_MODEL_LEN_HEADER = "X-Overmind-Max-Model-Len"
SERVE_IMAGE_HEADER = "X-Overmind-Serve-Image"
# Set only for shared-base LoRA serving: WEIGHTS_PATH_HEADER then carries the shared base and
# this carries the adapter riding on it.
ADAPTER_PATH_HEADER = "X-Overmind-Adapter-Path"
LORA_RANK_HEADER = "X-Overmind-Lora-Rank"


def rel_weights_path(weights_path: str, mount: str = WEIGHTS_MOUNT) -> str:
    """Strip the volume mount prefix from an absolute weights path."""
    return weights_path.removeprefix(mount).lstrip("/")


def base_pool_name(base_path: str) -> str:
    """Served-model name for a shared base container pool.

    Every adapter on a base must resolve to the same name, because the Modal parameter set is
    what decides whether two deployments share a warm pool or each boot their own container.
    """
    return "base--" + rel_weights_path(base_path).replace("/", "-").lower()


def routing_headers(
    *,
    gpu_type: str,
    weights_path: str,
    max_model_len: int,
    serve_image: str = "vllm",
    adapter_path: str = "",
    lora_rank: int = 0,
) -> dict[str, str]:
    headers = {
        GPU_TYPE_HEADER: gpu_type,
        WEIGHTS_PATH_HEADER: weights_path,
        MAX_MODEL_LEN_HEADER: str(max_model_len),
    }
    if serve_image and serve_image != "vllm":
        headers[SERVE_IMAGE_HEADER] = serve_image
    if adapter_path:
        headers[ADAPTER_PATH_HEADER] = adapter_path
        headers[LORA_RANK_HEADER] = str(lora_rank or 16)
    return headers


def parse_routing_headers(headers: dict) -> dict | None:
    """Read routing from a case-insensitive header map. None if any field missing."""
    lower = {str(k).lower(): str(v) for k, v in headers.items() if v is not None}
    gpu_type = lower.get(GPU_TYPE_HEADER.lower(), "").strip()
    weights_path = lower.get(WEIGHTS_PATH_HEADER.lower(), "").strip()
    raw_len = lower.get(MAX_MODEL_LEN_HEADER.lower(), "").strip()
    if not gpu_type or not weights_path or not raw_len:
        return None
    try:
        max_model_len = int(raw_len)
    except ValueError:
        return None
    serve_image = lower.get(SERVE_IMAGE_HEADER.lower(), "").strip() or "vllm"
    adapter_path = lower.get(ADAPTER_PATH_HEADER.lower(), "").strip()
    try:
        lora_rank = int(lower.get(LORA_RANK_HEADER.lower(), "").strip() or 16)
    except ValueError:
        lora_rank = 16
    return {
        "gpu_type": gpu_type,
        "weights_path": weights_path,
        "max_model_len": max_model_len,
        "serve_image": serve_image,
        "adapter_path": adapter_path,
        "lora_rank": lora_rank,
    }


# The checkpoint archive bucket per Modal environment. Not secret; both register_model.py's
# CloudBucketMount and Django's presigned downloads resolve it from here so they can't drift.
_FINETUNING_BUCKETS = {
    "overmind-dev": "overmind-finetuning-dev-62hdauj",
    "overmind-staging": "overmind-finetuning-staging-xmpmbnsw",
    "overmind-prod": "overmind-finetuning-prod-cmuziwbk",
}


def finetuning_bucket(environment: str | None = None) -> str:
    env = environment or os.environ.get("MODAL_ENVIRONMENT", "overmind-dev")
    return _FINETUNING_BUCKETS[env]


def resolve_inference_url(
    *,
    gpu_type: str,
    model_path: str,
    model_name: str,
    max_model_len: int,
    environment: str | None = None,
    serve_image: str = "vllm",
) -> str:
    """Parametrized worker URL. Query params select the container pool; a slash in
    ``model_path`` breaks that routing, so callers pass a relative path."""
    import urllib.parse

    env = environment or os.environ.get("MODAL_ENVIRONMENT", "overmind-dev")
    cls_slug = worker_cls_name(gpu_type, serve_image).lower().replace("_", "-")
    base = f"https://overmind-{env}--{INFERENCE_APP_NAME}-{cls_slug}-api.modal.run"
    params = {
        "model_path": rel_weights_path(model_path),
        "model_name": model_name,
        "max_model_len": max_model_len,
    }
    return f"{base}?{urllib.parse.urlencode(params)}"
