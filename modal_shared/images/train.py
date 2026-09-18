"""SFT training images keyed by frozen train-stack ids (modal_shared.stacks).

Do not edit pip pins on an existing TRAIN_IMAGES key. Clone the builder, new key.
"""

from __future__ import annotations

import modal

from modal_shared.images import attach_modelfam, attach_sft_assets
from modal_shared.stacks import (
    TRAIN_U2026_7,
    TRAIN_U2026_8_18,
    TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515,
    TRAIN_U2026_9_2,
)

_SFT_ENV = {
    # Container-local on purpose. Base weights come from the weights Volume's .base_models/
    # snapshot after fetch_base_model; this cache is not a second copy of the catalog.
    "HF_HOME": "/tmp/hf_cache",
    "HF_XET_HIGH_PERFORMANCE": "1",
    "PYTHONUNBUFFERED": "1",
}

_base_cuda = modal.Image.from_registry(
    "nvidia/cuda:12.8.0-devel-ubuntu22.04",
    add_python="3.11",
).apt_install("git")


def _mamba_kernels(image: modal.Image, torch_pin: str) -> modal.Image:
    # Nemotron-H needs fused Mamba kernels. Re-pin torch in the same call —
    # mamba-ssm's install_requires otherwise silently upgrades it.
    return image.pip_install(
        torch_pin,
        "mamba-ssm>=2.2.0",
        "causal-conv1d>=1.4.0",
        extra_options="--no-build-isolation",
    )


def _finish(image: modal.Image, env: dict[str, str]) -> modal.Image:
    return attach_sft_assets(attach_modelfam(image.env(env)))


def _clone_trl(image: modal.Image, *, branch: str | None = "v1.10.0") -> modal.Image:
    cmd = "git clone --depth 1"
    if branch:
        cmd += f" --branch {branch}"
    cmd += " https://github.com/huggingface/trl.git /opt/trl_src"
    return image.run_commands(cmd)


def _assert_versions(label: str, *, torch_prefix: str, tf_min: tuple[int, int] | None) -> str:
    tf_check = ""
    if tf_min:
        maj, minor = tf_min
        tf_check = (
            f"assert tuple(int(x) for x in transformers.__version__.split('.')[:2]) "
            f">= ({maj}, {minor}), transformers.__version__; "
        )
    return (
        'python -c "import torch, transformers; from torch.nn.functional import ScalingType; '
        f"assert torch.__version__.startswith({torch_prefix!r}), torch.__version__; "
        f"{tf_check}"
        f'print({label!r}, torch.__version__, transformers.__version__)"'
    )


def _unsloth_torch27() -> modal.Image:
    return _mamba_kernels(
        _base_cuda.pip_install(
            "torch==2.7.0",
            index_url="https://download.pytorch.org/whl/cu128",
        ).pip_install(
            "unsloth[cu128-torch270]==2026.7.5",
            "unsloth_zoo==2026.7.6",
            "transformers>=5.2.0",
            "datasets>=3.0.0",
            "huggingface_hub>=0.27.0",
            "sentencepiece",
            "protobuf",
        ),
        "torch==2.7.0",
    )


def _unsloth_torch210() -> modal.Image:
    # Frozen 2026.8 / torch 2.10 layer. Do not edit — copy into a new stack instead.
    return _mamba_kernels(
        _base_cuda.pip_install(
            "torch==2.10.0",
            index_url="https://download.pytorch.org/whl/cu128",
        ).pip_install(
            "unsloth[cu128-torch2100]==2026.8.5",
            "unsloth_zoo==2026.8.4",
            "datasets>=3.0.0",
            "huggingface_hub>=0.27.0",
            "sentencepiece",
            "protobuf",
        ),
        "torch==2.10.0",
    )


_u2026_7 = _finish(
    _clone_trl(_unsloth_torch27()),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_7,
    },
)

_u2026_8_tf510 = _finish(
    _clone_trl(_unsloth_torch210().pip_install("transformers>=5.10.2")).run_commands(
        _assert_versions("u2026_8_tf510-ok", torch_prefix="2.10", tf_min=(5, 10)),
        "python -c \"import mamba_ssm; print('mamba-ok')\"",
    ),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_8_TF510,
    },
)

_u2026_8_tf515 = _finish(
    _clone_trl(_unsloth_torch210().pip_install("transformers>=5.15.0")).run_commands(
        _assert_versions("u2026_8_tf515-ok", torch_prefix="2.10", tf_min=(5, 15)),
        'python -c "from transformers import MuseGlimmerForConditionalGeneration"',
    ),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_8_TF515,
    },
)


def _unsloth_torch210_818() -> modal.Image:
    # New stack: Unsloth 2026.8.18 (improved GC offload). Do not retag older keys.
    return _mamba_kernels(
        _base_cuda.pip_install(
            "torch==2.10.0",
            index_url="https://download.pytorch.org/whl/cu128",
        ).pip_install(
            "unsloth[cu128-torch2100]==2026.8.18",
            "unsloth_zoo==2026.8.12",
            "datasets>=3.0.0",
            "huggingface_hub>=0.27.0",
            "sentencepiece",
            "protobuf",
        ),
        "torch==2.10.0",
    )


_u2026_8_18 = _finish(
    _clone_trl(_unsloth_torch210_818().pip_install("transformers>=5.10.2")).run_commands(
        _assert_versions("u2026_8_18-ok", torch_prefix="2.10", tf_min=(5, 10)),
        "python -c \"import mamba_ssm; print('mamba-ok')\"",
    ),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_8_18,
    },
)


def _unsloth_torch210_92() -> modal.Image:
    # Qwen3.8: Unsloth 2026.8.18's get_model_name rejects unsloth/Qwen3.8-27B.
    return _mamba_kernels(
        _base_cuda.pip_install(
            "torch==2.10.0",
            index_url="https://download.pytorch.org/whl/cu128",
        ).pip_install(
            "unsloth[cu128-torch2100]==2026.9.2",
            "unsloth_zoo==2026.9.1",
            "datasets>=3.0.0",
            "huggingface_hub>=0.27.0",
            "sentencepiece",
            "protobuf",
        ),
        "torch==2.10.0",
    )


_u2026_9_2 = _finish(
    _clone_trl(_unsloth_torch210_92().pip_install("transformers>=5.10.2")).run_commands(
        _assert_versions("u2026_9_2-ok", torch_prefix="2.10", tf_min=(5, 10)),
        "python -c \"import mamba_ssm; print('mamba-ok')\"",
    ),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_9_2,
    },
)

# Keep Unsloth's transformers pin (≤5.5): upgrading to 5.10+ remaps MoE experts
# to fused gate_up_proj and drops the bnb-4bit per-expert weights (random init →
# loss ~17 / token_acc 0). kernels keeps MXFP4 from silently dequantizing to bf16.
# Cap kernels<0.15: 0.15+ requires version= on LayerRepository and crashes
# transformers≤5.5 import (hub_kernels.py) before training starts.
_u2026_8_gptoss = _finish(
    _clone_trl(_unsloth_torch210().pip_install("kernels>=0.12.0,<0.15")).run_commands(
        _assert_versions("u2026_8_gptoss-ok", torch_prefix="2.10", tf_min=None),
        "python -c \"import kernels; print('kernels-ok')\"",
    ),
    {
        **_SFT_ENV,
        "TRL_SRC": "/opt/trl_src",
        "UNSLOTH_IMAGE": TRAIN_U2026_8_GPOS,
        # Fused forward binds load_balancing_loss_func by value and crashes on
        # gate_logits=(). Disable compile so stock forward + empty-tuple guard apply.
        "UNSLOTH_COMPILE_DISABLE": "1",
    },
)

# Every image in this module needs modal_shared attached, even ones whose
# functions never call resolve(): Modal re-imports the whole entrypoint file
# to reconstruct any function in it, and that file top-level-imports this
# module, regardless of which specific Function/image is being invoked.
light_image = attach_modelfam(modal.Image.debian_slim(python_version="3.11"))

TRAIN_IMAGES: dict[str, modal.Image] = {
    TRAIN_U2026_7: _u2026_7,
    TRAIN_U2026_8_TF510: _u2026_8_tf510,
    TRAIN_U2026_8_TF515: _u2026_8_tf515,
    TRAIN_U2026_8_GPOS: _u2026_8_gptoss,
    TRAIN_U2026_8_18: _u2026_8_18,
    TRAIN_U2026_9_2: _u2026_9_2,
}
