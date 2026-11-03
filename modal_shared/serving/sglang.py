"""SGLang dedicated launch command. The flags are the ones that matched Modal's DFlash rate."""

from __future__ import annotations

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
}


def sglang_tool_parser(name: str) -> str:
    return _TOOL_PARSER_NAMES.get(name, name)


def sglang_reasoning_parser(name: str) -> str:
    return _REASONING_PARSER_NAMES.get(name, name)


def attention_backend(gpu_type: str, *, model: str = "") -> str:
    # fa3 is the Hopper kernel. On Ada and Ampere it dies during graph capture.
    hopper = gpu_type in {"H100", "H200"} or not gpu_type
    if "gpt-oss" in model:
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
    tool_call_parser: str = "",
    reasoning_parser: str = "",
    gpu_type: str = "",
    model: str = "",
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
        "--attention-backend",
        attention_backend(gpu_type, model=model),
        "--mem-fraction-static",
        "0.75",
        "--dtype",
        "bfloat16",
        "--trust-remote-code",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
    ]
    if draft_model:
        cmd += [
            "--speculative-algorithm",
            "DFLASH",
            "--speculative-draft-model-path",
            draft_model,
            "--speculative-num-draft-tokens",
            str(draft_tokens),
        ]
    if tool_call_parser:
        cmd += ["--tool-call-parser", sglang_tool_parser(tool_call_parser)]
    if reasoning_parser:
        cmd += ["--reasoning-parser", sglang_reasoning_parser(reasoning_parser)]
    return cmd
