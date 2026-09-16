"""Build ``vllm serve`` argv from FamilySpec + runtime context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from modal_shared.modelfam import (
    FamilySpec,
    has_training_generation_markers,
    resolve,
    strip_training_generation_markers,
)

# Decode batch sizes to capture graphs for. Measured against the full default list on
# L4/L40S/H100: this list costs 0.16-0.22 GiB and 2-22 s, the full list costs 1.0-1.43 GiB
# and 22-44 s for no extra decode win at our batch sizes. Widening it means re-checking
# ACTIVATION_OVERHEAD_GB in gpu_selector, which budgets the graph memory.
CUDAGRAPH_CAPTURE_SIZES: tuple[int, ...] = (1, 2, 4, 8, 16, 32)

# Adapters resident per in-flight batch. 8 measured ~50% more concurrent throughput than 4
# on an L4 at 8 adapters; past that the per-batch coordination cost grows faster than the
# batching win.
MAX_LORA_SLOTS = 8

# vLLM only accepts these values for --max-lora-rank; anything else is rejected at startup.
_LORA_RANK_STEPS: tuple[int, ...] = (1, 8, 16, 32, 64, 128, 256, 320, 512)


def snap_lora_rank(rank: int) -> int:
    """Round a trained rank up to the nearest rank vLLM accepts.

    Oversizing costs real memory and latency — a rank-16 adapter served at max_lora_rank=256
    measured 59% slower and 15.9x larger — so this rounds up to the next step, never to a
    blanket maximum.
    """
    for step in _LORA_RANK_STEPS:
        if rank <= step:
            return step
    return _LORA_RANK_STEPS[-1]


@dataclass(frozen=True)
class VllmServeContext:
    model_path: str
    model_name: str
    max_model_len: int
    port: int
    gpu_memory_utilization: str = "0.90"
    max_num_seqs: int = 32
    quantization: str | None = None
    base_model: str = ""
    trust_remote_code: bool = True
    # (served_name, adapter_dir) pairs. Non-empty switches the process to shared-base mode:
    # model_path is the stock base and each adapter answers to its own deployment name.
    lora_adapters: tuple[tuple[str, str], ...] = ()
    # Accept adapters that arrive after startup. A shared base pool is keyed by the base, so
    # it cannot name its adapters up front and loads every one of them over the LoRA API.
    enable_lora: bool = False
    max_lora_rank: int = 16
    # vLLM sleep/wake around Modal GPU snapshots. Full-FT workers leave this off.
    enable_sleep_mode: bool = False
    # Where to read chat_template.jinja from. An adapter carries the template it was trained
    # with, which is not the stock base's.
    chat_template_dir: str = ""


def _serve_chat_template_path(model_path: str) -> str | None:
    """Return a serve-safe chat template path, stripping training markers if needed."""
    raw = Path(model_path) / "chat_template.jinja"
    if not raw.is_file():
        return None
    text = raw.read_text(encoding="utf-8")
    if not has_training_generation_markers(text):
        return str(raw)
    cleaned = strip_training_generation_markers(text)
    out = Path(model_path) / "chat_template.serve.jinja"
    if not out.is_file() or out.read_text(encoding="utf-8") != cleaned:
        out.write_text(cleaned, encoding="utf-8")
    return str(out)


def build_vllm_args(ctx: VllmServeContext, spec: FamilySpec | None = None) -> list[str]:
    """Return the full ``vllm serve …`` command list (no shell)."""
    if spec is None:
        spec = resolve(ctx.base_model or ctx.model_name)

    # In shared-base mode the deployment name belongs to the adapter, so the base underneath
    # has to answer to something else or vLLM routes the request to the unadapted weights.
    served_name = f"{ctx.model_name}--base" if ctx.lora_adapters else ctx.model_name

    cmd = [
        "vllm",
        "serve",
        ctx.model_path,
        "--served-model-name",
        served_name,
        "--host",
        "0.0.0.0",
        "--port",
        str(ctx.port),
        "--max-model-len",
        str(ctx.max_model_len),
        "--gpu-memory-utilization",
        ctx.gpu_memory_utilization,
        "--max-num-seqs",
        str(ctx.max_num_seqs),
        "--enable-force-include-usage",
        "--enable-auto-tool-choice",
        "--tool-call-parser",
        spec.tool_call_parser,
    ]
    if spec.reasoning_parser:
        cmd += ["--reasoning-parser", spec.reasoning_parser]
    template_kwargs = {"enable_thinking": False, "thinking": False}
    template_kwargs.update(dict(spec.default_chat_template_kwargs))
    cmd += [
        "--enable-per-request-metrics",
        "--enable-prefix-caching",
        "--default-chat-template-kwargs",
        json.dumps(template_kwargs, separators=(",", ":")),
    ]
    if ctx.trust_remote_code:
        cmd.append("--trust-remote-code")

    quant = ctx.quantization
    force_bf16 = spec.dtype_override == "bfloat16" or (quant or "bf16") in (
        "bf16",
        "none",
        "",
        None,
    )
    if force_bf16:
        cmd += ["--dtype", spec.dtype_override or "bfloat16"]

    if ctx.lora_adapters or ctx.enable_lora:
        # A pool that loads adapters at runtime has none to count, so it takes the full
        # slot allowance.
        slots = min(len(ctx.lora_adapters), MAX_LORA_SLOTS) if ctx.lora_adapters else MAX_LORA_SLOTS
        cmd += [
            "--enable-lora",
            "--max-loras",
            str(slots),
            "--max-lora-rank",
            str(snap_lora_rank(ctx.max_lora_rank)),
            # Evictions fall back to host RAM rather than a Volume round trip.
            "--max-cpu-loras",
            str(max(len(ctx.lora_adapters), MAX_LORA_SLOTS * 4)),
        ]
        if ctx.lora_adapters:
            cmd += ["--lora-modules", *(f"{name}={path}" for name, path in ctx.lora_adapters)]

    # Concurrent tensor reads off the Volume into GPU. Default mmap is sequential and
    # random-IO on FUSE; streamer is the vLLM path for network filesystems. `distributed`
    # is CUDA-only and is what the docs quote for fileshares.
    cmd += [
        "--load-format",
        "runai_streamer",
        "--model-loader-extra-config",
        json.dumps({"distributed": True, "concurrency": 16}, separators=(",", ":")),
        "--cudagraph-capture-sizes",
        *(str(s) for s in CUDAGRAPH_CAPTURE_SIZES),
    ]
    if ctx.enable_sleep_mode:
        cmd.append("--enable-sleep-mode")

    chat_tmpl = _serve_chat_template_path(ctx.chat_template_dir or ctx.model_path)
    if chat_tmpl:
        cmd += ["--chat-template", chat_tmpl]

    return cmd


def multimodal_text_only_args() -> list[str]:
    return [
        "--language-model-only",
        "--limit-mm-per-prompt",
        '{"image":0,"video":0,"audio":0}',
    ]
