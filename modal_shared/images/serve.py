"""vLLM serving + gateway images."""

from __future__ import annotations

import modal

from modal_shared.images import attach_modelfam, attach_serving_args
from modal_shared.stacks import SERVE_MUSE_GLIMMER, SERVE_VLLM

_SERVE_ENV = {
    # Container-local. Serving resolves every model by path — a merged checkpoint, or an adapter
    # plus its .base_models/ base — so nothing here should reach the hub at all.
    "HF_HOME": "/tmp/hf_cache",
    "HF_XET_HIGH_PERFORMANCE": "1",
    "VLLM_LOG_STATS_INTERVAL": "30",
    # Default off; worker sets "1" for non-prod at runtime.
    "VLLM_SERVER_DEV_MODE": "0",
}


def _from_vllm_openai(tag: str, *extra_pip: str) -> modal.Image:
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
                "runai-model-streamer>=0.15.7",
                *extra_pip,
            )
            .env(_SERVE_ENV)
        )
    )


# amd64 digest sha256:c2f3b1b964e47809b722b5e75b61b1e7b39a50f70388cf2bf2418f16a9f31da2
# transformers<5.15: 5.15+ treats Gemma 4 head_dim as per-layer and vLLM 0.27.1
# dies in ModelConfig (AmbiguousGlobalPerLayerAttributeError). Upstream fix is
# vLLM #49797, not in 0.27.1 — new tag = new SERVE_IMAGES key, never retag vllm.
vllm_image = _from_vllm_openai("v0.27.1", "transformers>=5.10.2,<5.15")
# amd64 digest sha256:8151766297ea77f37d7378ba182aa16870b3f8eb5740a740846dd16d1dc4fa05
# Nightly pinned to a commit, not the "v0.28.0" release tag: v0.28.0 merges
# official Muse Glimmer support (vLLM #51655) but its muse_glimmer.py predates
# the LoRA multimodal-module-mapping fix (vLLM #53513, merged 2026-08-24) —
# --enable-lora still crashes at startup profiling (AssertionError in
# lora_shrink_op, vLLM #53254) on that tag despite reporting version "0.28.0".
# This nightly (built 2026-08-26 off main commit 46638857) has get_mm_mapping()
# present — verified by importing vllm.model_executor.models.muse_glimmer in
# the image. Retag to a numbered release once one ships with the fix.
vllm_muse_glimmer_image = _from_vllm_openai(
    "cu129-nightly-46638857fdbb30e0c232c9e8f9cb1ff6d6f545c3"
)

SERVE_IMAGES: dict[str, modal.Image] = {
    SERVE_VLLM: vllm_image,
    SERVE_MUSE_GLIMMER: vllm_muse_glimmer_image,
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
