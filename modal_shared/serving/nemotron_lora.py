"""Nemotron-H expert LoRA is the stacked 3D layout.

vLLM 0.33 leaves ``is_3d_moe_weight`` unset, so the 2D MoE wrapper receives one
tensor and asserts. Attention-only adapters never reach that wrapper.
"""

from __future__ import annotations

import os
from pathlib import Path


def mark(cls: type) -> None:
    cls.is_3d_moe_weight = True


def install() -> None:
    try:
        from vllm.model_executor.models.nemotron_h import NemotronHForCausalLM
    except ImportError:
        return
    mark(NemotronHForCausalLM)


def process_env() -> dict[str, str]:
    site = Path("/tmp/overmind-nemotron")
    site.mkdir(parents=True, exist_ok=True)
    (site / "sitecustomize.py").write_text(
        "import modal_shared.serving.nemotron_lora as nemotron\nnemotron.install()\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site) + os.pathsep + env.get("PYTHONPATH", "")
    return env
