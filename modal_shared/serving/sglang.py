"""SGLang dedicated launch command."""

from __future__ import annotations

import json
from pathlib import Path

from modal_shared.serving.args import CUDAGRAPH_CAPTURE_SIZES

# vLLM's parser names that SGLang spells differently. Other names match.
_TOOL_PARSER_NAMES = {
    "openai": "gpt-oss",
    "llama3_json": "llama3",
    "muse_glimmer": "muse",
}
# Harmony has no thinking-off switch. This parser keeps the analysis channel out of content.
_REASONING_PARSER_NAMES = {
    "openai_gptoss": "gpt-oss",
    "muse_glimmer": "muse",
    # vLLM calls this nemotron_v3. SGLang 0.5.22's choice is nemotron_3.
    "nemotron_v3": "nemotron_3",
}


def sglang_tool_parser(name: str) -> str:
    return _TOOL_PARSER_NAMES.get(name, name)


def sglang_reasoning_parser(name: str) -> str:
    return _REASONING_PARSER_NAMES.get(name, name)


def _position_limit(model_path: str) -> int | None:
    path = Path(model_path) / "config.json"
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    sources = [config]
    text = config.get("text_config")
    if isinstance(text, dict):
        sources.append(text)
    for source in sources:
        limit = source.get("max_position_embeddings")
        if type(limit) is int and limit > 0:
            return limit
    return None


def attention_backend(gpu_type: str, *, model: str = "") -> str:
    # fa3 is the Hopper kernel. On Ada and Ampere it dies during graph capture.
    lowered = model.lower()
    if "lfm3" in lowered:
        # The LFM3 server picks flashinfer itself. fa3 mismatches the conv blocks.
        return ""
    # Gemma 4 rejects flashinfer. The accepted backends are triton, trtllm_mha, fa4, aiter.
    if "gemma" in lowered:
        return "triton"
    hopper = gpu_type in {"H100", "H200"} or not gpu_type
    if "gpt-oss" in lowered:
        # This architecture rejects flashinfer. Triton runs on the other GPUs.
        return "fa3" if hopper else "triton"
    if hopper:
        return "fa3"
    return "flashinfer"


def build_sglang_args(
    *,
    model_path: str,
    served_name: str,
    port: int,
    draft_model: str = "",
    draft_tokens: int = 0,
    mtp: tuple[int, int, int] | None = None,
    tool_call_parser: str = "",
    reasoning_parser: str = "",
    gpu_type: str = "",
    model: str = "",
    enable_lora: bool = False,
    max_lora_rank: int = 16,
    lora_target_modules: str = "",
    mem_fraction: str = "0.75",
    max_model_len: int | None = None,
    max_running_requests: int | None = None,
) -> list[str]:
    cmd = [
        "python3",
        "-m",
        "sglang.launch_server",
        "--model-path",
        model_path,
        "--served-model-name",
        served_name,
        "--tp-size",
        "1",
    ]
    backend = attention_backend(gpu_type, model=model)
    if backend:
        cmd += ["--attention-backend", backend]
    cmd += [
        "--mem-fraction-static",
        mem_fraction,
        "--dtype",
        "bfloat16",
        "--trust-remote-code",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    # Runtime LoRA load rejects this format (the LFM3 image has no RUNAI_STREAMER
    # loader). A folded checkpoint has no adapter load, so it can stream.
    if not enable_lora:
        cmd += ["--load-format", "runai_streamer"]
    if max_model_len:
        limit = _position_limit(model_path)
        cmd += ["--context-length", str(min(max_model_len, limit) if limit else max_model_len)]
    # Triton dequantizes FP8 KV before attention. fa3 and flashinfer fuse it.
    if backend in {"fa3", "flashinfer"}:
        cmd += ["--kv-cache-dtype", "fp8_e4m3"]
    # 0.88 filled an L4 and the EAGLE draft capture was killed. 0.75 leaves
    # that headroom. Prefill graphs are the long boot; decode graphs stay.
    cmd.append("--disable-prefill-cuda-graph")
    # The short capture list drops speculative acceptance. Cap the width instead.
    if mtp or draft_model:
        cmd += ["--cuda-graph-max-bs-decode", "32"]
    else:
        sizes = [size for size in CUDAGRAPH_CAPTURE_SIZES if size <= (max_running_requests or size)]
        cmd += ["--cuda-graph-bs-decode", *(str(size) for size in sizes)]
        if max_running_requests:
            cmd += ["--max-running-requests", str(max_running_requests)]
    if mtp:
        steps, topk, tokens = mtp
        cmd += [
            "--speculative-algorithm",
            "EAGLE",
            "--speculative-num-steps",
            str(steps),
            "--speculative-eagle-topk",
            str(topk),
            "--speculative-num-draft-tokens",
            str(tokens),
        ]
    elif draft_model:
        cmd += [
            "--speculative-algorithm",
            "DFLASH",
            "--speculative-draft-model-path",
            draft_model,
            "--speculative-num-draft-tokens",
            str(draft_tokens),
        ]
    if enable_lora:
        # One comma-separated string is rejected unless it is all or all-linear.
        modules = [
            part.strip() for part in (lora_target_modules or "all").split(",") if part.strip()
        ]
        cmd += [
            "--enable-lora",
            "--max-lora-rank",
            str(max_lora_rank),
            "--lora-target-modules",
            *modules,
        ]
    if tool_call_parser:
        cmd += ["--tool-call-parser", sglang_tool_parser(tool_call_parser)]
    if reasoning_parser:
        cmd += ["--reasoning-parser", sglang_reasoning_parser(reasoning_parser)]
    return cmd


def select_sglang_adapter(body: bytes, name: str) -> bytes:
    """A loaded adapter is applied only when ``lora_path`` is that name.

    ``model`` with no colon is the base, and any model string still returns 200.
    """
    payload = json.loads(body)
    payload["lora_path"] = name
    return json.dumps(payload).encode()


def normalize_sglang_adapter(adapter_dir, *, keep_targets: str = "") -> None:
    """SGLang accepts ``all``, ``all-linear``, or a list of module names.

    Unsloth writes a regex. A family with an explicit target list replaces that
    regex with the list; every other family becomes ``all-linear``.
    """
    import json
    from pathlib import Path

    path = Path(adapter_dir) / "adapter_config.json"
    if not path.is_file():
        return
    cfg = json.loads(path.read_text())
    targets = cfg.get("target_modules")
    if keep_targets:
        wanted = [part.strip() for part in keep_targets.split(",") if part.strip()]
        cfg["target_modules"] = wanted
        path.write_text(json.dumps(cfg))
        _keep_lora_leaves(Path(adapter_dir), set(wanted))
        return
    if not isinstance(targets, str) or targets in {"all", "all-linear"}:
        return
    cfg["target_modules"] = "all-linear"
    path.write_text(json.dumps(cfg))


def _keep_lora_leaves(adapter_dir, leaves: set[str]) -> None:
    from pathlib import Path

    weight_path = Path(adapter_dir) / "adapter_model.safetensors"
    if not weight_path.is_file():
        return
    from safetensors import safe_open
    from safetensors.torch import save_file

    suffixes = (
        ".lora_A.weight",
        ".lora_B.weight",
        ".lora_A.default.weight",
        ".lora_B.default.weight",
    )
    kept = {}
    with safe_open(weight_path, framework="pt") as handle:
        for key in handle.keys():  # noqa: SIM118
            name = key
            for suffix in suffixes:
                if name.endswith(suffix):
                    name = name[: -len(suffix)]
                    break
            if name.split(".")[-1] in leaves:
                kept[key] = handle.get_tensor(key)
    if kept:
        save_file(kept, weight_path)
