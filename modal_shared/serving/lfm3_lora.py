"""Hidden sizes the LFM3 SGLang image does not know.

The conv and MLP leaves are Linear modules with names SGLang's loader has no
case for. Shapes are the LFM3 modeling file: in_proj is hidden to 3*hidden,
out_proj is square, w1 and w3 expand, w2 contracts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# This image sizes q/k/v/o, the conv projections, and out_proj.
# w1, w2, and w3 have no LoRA buffer here.
SGLANG_TARGETS = "q_proj,k_proj,v_proj,o_proj,in_proj,out_proj"


def stacked_multiply(module_name: str) -> int | None:
    # SGLang's default map packs every in_proj into two slices. This conv is three.
    if module_name == "in_proj":
        return 3
    return None


def hidden_dim(module_name: str, hidden: int, intermediate: int) -> tuple[int, int] | None:
    if module_name == "in_proj":
        return hidden, 3 * hidden
    if module_name == "out_proj":
        return hidden, hidden
    if module_name in {"w1", "w3"}:
        return hidden, intermediate
    if module_name == "w2":
        return intermediate, hidden
    return None


def install() -> None:
    try:
        from sglang.srt.lora import utils as lora_utils
    except ImportError:
        return
    original = lora_utils.get_default_hidden_dim
    original_stack = lora_utils.get_stacked_multiply

    def get_default_hidden_dim(module_name, config, layer_idx=0, lora_added_vocab_size=0):
        known = hidden_dim(
            module_name,
            int(config.hidden_size),
            int(getattr(config, "intermediate_size", 0) or 0),
        )
        if known is not None:
            return known
        return original(module_name, config, layer_idx, lora_added_vocab_size)

    def get_stacked_multiply(module_name, base_model=None):
        # The default map treats every in_proj as Mamba's 2-way pack.
        # LFM3's conv projection is B, C, and x.
        known = stacked_multiply(module_name)
        if known is not None:
            return known
        return original_stack(module_name, base_model)

    lora_utils.get_default_hidden_dim = get_default_hidden_dim
    lora_utils.get_stacked_multiply = get_stacked_multiply
    for module in list(sys.modules.values()):
        if getattr(module, "get_default_hidden_dim", None) is original:
            module.get_default_hidden_dim = get_default_hidden_dim
        if getattr(module, "get_stacked_multiply", None) is original_stack:
            module.get_stacked_multiply = get_stacked_multiply


def process_env() -> dict[str, str]:
    site = Path("/tmp/overmind-lfm3")
    site.mkdir(parents=True, exist_ok=True)
    (site / "sitecustomize.py").write_text(
        "import modal_shared.serving.lfm3_lora as lfm3\nlfm3.install()\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site) + os.pathsep + env.get("PYTHONPATH", "")
    return env
