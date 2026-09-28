"""vLLM serving + gateway images."""

from __future__ import annotations

import modal

from modal_shared.images import attach_modelfam, attach_serving_args
from modal_shared.stacks import SERVE_VLLM

_SERVE_ENV = {
    # Container-local. Serving resolves every model by path — a merged checkpoint, or an adapter
    # plus its .base_models/ base — so nothing here should reach the hub at all.
    "HF_HOME": "/tmp/hf_cache",
    "HF_XET_HIGH_PERFORMANCE": "1",
    "VLLM_LOG_STATS_INTERVAL": "30",
    # Default off; worker sets "1" for non-prod at runtime.
    "VLLM_SERVER_DEV_MODE": "0",
}


def _from_vllm_openai(tag: str, *extra_pip: str, snapshot: bool = False) -> modal.Image:
    """Official ``vllm/vllm-openai`` image. ENTRYPOINT is ``vllm serve`` — must be
    cleared or Modal never runs the worker. Image has python3 only."""
    return attach_serving_args(
        attach_modelfam(
            modal.Image.from_registry(
                f"vllm/vllm-openai:{tag}",
                setup_dockerfile_commands=[
                    "RUN ln -sf $(command -v python3) /usr/local/bin/python",
                ],
            )
            .entrypoint([])
            .pip_install(
                "httpx>=0.28.0",
                # Concurrent safetensors reads into GPU; vLLM extra `runai`.
                "runai-model-streamer==0.16.1" if snapshot else "runai-model-streamer>=0.15.7",
                *extra_pip,
            )
            .env(
                {
                    **_SERVE_ENV,
                    **(
                        dict.fromkeys(
                            ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"), "8"
                        )
                        if snapshot
                        else {}
                    ),
                }
            )
        )
    )


# CUDA 13.0. Modal's host driver is 580 / CUDA 13.0. Do not pip-downgrade
# transformers: 0.30.0 ships 5.16.1, and Gemma 4 head_dim (#49797) plus Muse
# LoRA get_mm_mapping (#53513) both need this release.
_VLLM_TAG = "v0.30.0"
vllm_image = _from_vllm_openai(_VLLM_TAG)

SERVE_IMAGES: dict[str, modal.Image] = {
    SERVE_VLLM: vllm_image,
}

# The snapshot reader uses a version-specific pinned-buffer allocation site.
# Keep these images separate from the unchanged full-checkpoint stacks.
LORA_SERVE_IMAGES = {
    SERVE_VLLM: _from_vllm_openai(_VLLM_TAG, snapshot=True),
}

# Needs modal_shared too: modal_vllm_worker.py top-level-imports it regardless
# of which class/function (and thus which image) Modal is reconstructing.
api_server_image = attach_modelfam(
    modal.Image.debian_slim(python_version="3.11").pip_install(
        "fastapi[standard]==0.115.12",
        "uvicorn==0.34.0",
        "httpx==0.28.1",
        "modal>=0.73",
    )
)
