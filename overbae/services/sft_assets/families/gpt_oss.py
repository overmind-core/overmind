"""gpt-oss Unsloth hooks — LoRA loaded directly from the catalog BF16 repo.

Training starts from ``models.json``'s ``hf_model_id`` (``unsloth/gpt-oss-*-BF16``)
only — no separate pre-linearized ``*-unsloth-bnb-4bit`` repo, by requirement.

That constrains the experts to full-precision (BF16) LoRA, not QLoRA: gpt-oss's
MoE experts ship as one fused 3D ``nn.Parameter`` (``mlp.experts.gate_up_proj`` /
``down_proj``, shape ``(num_experts, hidden, ...)``), and bitsandbytes' on-the-fly
quantizer only replaces ``nn.Linear`` submodules it finds via
``model.named_modules()`` — it can't see a bare 3D ``Parameter``. Unsloth's LoRA
path for this class (``patch_gpt_oss_moe_for_lora``) attaches LoRA straight to
that parameter and never touches bnb, so the experts stay BF16 regardless of
``load_in_4bit``. The only Unsloth class that gets bnb 4-bit onto the experts is
``GptOssExpertsBnb4bit`` (per-expert ``nn.ModuleList``), auto-installed whenever
``UNSLOTH_MODEL_NAME`` carries ``gpt_oss`` + ``_load_in_4bit_`` — but its
``_load_from_state_dict`` expects the checkpoint's tensors *already* split into
per-expert ``gate_up_projs.{i}.weight`` keys, the shape only the
``*-unsloth-bnb-4bit`` repos ship. Loading the plain BF16 (fused-tensor)
checkpoint into it leaves every expert randomly initialized (first loss ~16,
token_accuracy 0) — so ``UNSLOTH_GPT_OSS_BNB4BIT_DISABLE=1`` keeps Unsloth off
that class and on the stock BF16-native one instead. Non-expert linear layers
(attention, router) still quantize normally.

The gpt-oss image/worker default ``UNSLOTH_COMPILE_DISABLE=1`` (fused MoE
forward captures ``load_balancing_loss_func`` and crashes on ``gate_logits=()``).
That also skips Unsloth's ``patch_GptOssAttention``, which is the only path
that trains attention-sinks via Flex Attention instead of materializing
``[B,H,S,S]`` eager weights (~100 GiB at S=32k). LoRA ``env_overrides``
re-enables compile before ``import unsloth`` so that patch installs; the
empty-tuple guard below still covers the fused aux-loss crash.

Requires huggingface ``kernels>=0.12.0,<0.15`` on the image (MXFP4 tooling);
cap <0.15 because kernels 0.15+ breaks ``import transformers`` on Unsloth's
transformers≤5.5.
"""

from __future__ import annotations

import contextlib
import os
import types
from typing import Any

from families import DefaultHooks


def _require_kernels() -> None:
    try:
        import kernels  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "gpt-oss Unsloth training requires kernels>=0.12.0,<0.15 "
            "(without it transformers dequantizes MXFP4→bf16, which is broken). "
            "Install: pip install 'kernels>=0.12.0,<0.15'"
        ) from exc


def _patch_empty_router_aux_loss(model: Any | None = None) -> None:
    """Guard load_balancing_loss_func against gate_logits=()."""

    def _wrap(fn: Any) -> Any:
        if not isinstance(fn, types.FunctionType):
            return fn
        if getattr(fn, "_overmind_empty_guard", False):
            return fn
        orig = fn

        def _safe(gate_logits, *args, **kwargs):  # noqa: ANN001
            if not gate_logits:
                import torch

                # Fused forward does aux_loss.to(...); plain int 0 blows up.
                return torch.tensor(0.0)
            return orig(gate_logits, *args, **kwargs)

        _safe._overmind_empty_guard = True  # type: ignore[attr-defined]
        return _safe

    try:
        import transformers.models.gpt_oss.modeling_gpt_oss as _gpt_oss_mod

        _gpt_oss_mod.load_balancing_loss_func = _wrap(_gpt_oss_mod.load_balancing_loss_func)
    except ImportError as exc:
        print(f"gpt-oss: modeling_gpt_oss not importable yet: {exc!r}", flush=True)

    try:
        # trl's SFTTrainer._chunked_ce_forward does a *local* import —
        # `from transformers.models.mixtral.modeling_mixtral import load_balancing_loss_func`
        # inside the function body — so it re-reads this module attribute on
        # every call rather than binding it once at import time. Patching the
        # gpt_oss copy above never reaches this call; mixtral's is the one
        # actually invoked (transformers reuses the mixtral aux-loss impl for
        # every MoE family), so the empty-tuple guard has to land here too.
        import transformers.models.mixtral.modeling_mixtral as _mixtral_mod

        _mixtral_mod.load_balancing_loss_func = _wrap(_mixtral_mod.load_balancing_loss_func)
    except ImportError as exc:
        print(f"gpt-oss: modeling_mixtral not importable yet: {exc!r}", flush=True)

    patched_fwd = 0
    if model is not None:
        seen: set[int] = set()
        candidates: list[Any] = [model]
        base = getattr(model, "get_base_model", None)
        if callable(base):
            with contextlib.suppress(Exception):
                candidates.append(base())
        for attr in ("base_model", "model"):
            obj = getattr(model, attr, None)
            if obj is not None:
                candidates.append(obj)
                nested = getattr(obj, "model", None)
                if nested is not None:
                    candidates.append(nested)
        for obj in candidates:
            fwd = getattr(obj, "forward", None)
            for _ in range(6):
                if not callable(fwd):
                    break
                if hasattr(fwd, "__func__"):
                    fwd = fwd.__func__
                    continue
                if hasattr(fwd, "__wrapped__"):
                    fwd = fwd.__wrapped__
                    continue
                break
            g = getattr(fwd, "__globals__", None) if callable(fwd) else None
            if not isinstance(g, dict) or id(g) in seen:
                continue
            seen.add(id(g))
            lb = g.get("load_balancing_loss_func")
            if isinstance(lb, types.FunctionType):
                g["load_balancing_loss_func"] = _wrap(lb)
                patched_fwd += 1

        print(
            f"gpt-oss: empty-gate_logits guard fused_fwd={patched_fwd} "
            f"COMPILE_DISABLE={os.environ.get('UNSLOTH_COMPILE_DISABLE')!r} "
            f"FLEX={os.environ.get('UNSLOTH_ENABLE_FLEX_ATTENTION')!r} "
            f"MODEL_NAME={os.environ.get('UNSLOTH_MODEL_NAME')!r}",
            flush=True,
        )


def _prepare_gpt_oss_lora_load() -> None:
    """Pre-load setup: aux-loss guard + drop any compiled-module cache built
    for the other GptOssExperts flavor (ModuleList vs reshaped-2D)."""
    _patch_empty_router_aux_loss()
    try:
        from unsloth_zoo.temporary_patches.gpt_oss import _invalidate_gpt_oss_compiled_module
    except ImportError as exc:
        raise RuntimeError(
            "gpt-oss Unsloth training needs unsloth_zoo.temporary_patches.gpt_oss "
            f"(import failed: {exc})"
        ) from exc
    try:
        _invalidate_gpt_oss_compiled_module()
    except Exception as exc:  # noqa: BLE001
        print(f"gpt-oss: compile-cache invalidate skipped: {exc!r}", flush=True)


class GptOssHooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        _require_kernels()
        out: dict[str, str] = {}
        if use_lora:
            out["LOAD_IN_4BIT"] = "1"
            # Keep Unsloth's default reshaped-2D GptOssExperts (matches the
            # BF16 repo's fused tensors) instead of the ModuleList class that
            # expects a pre-split checkpoint — see module docstring.
            out["UNSLOTH_GPT_OSS_BNB4BIT_DISABLE"] = "1"
            # Must land before `import unsloth`. Image/worker force "1"; without
            # this flip Unsloth never patches GptOssAttention onto Flex+sinks.
            out["UNSLOTH_COMPILE_DISABLE"] = "0"
            out["UNSLOTH_ENABLE_FLEX_ATTENTION"] = "1"
            # patch_GptOssAttention no-ops unless this contains "gpt_oss" at
            # import time; Unsloth only writes the env later, in from_pretrained.
            mid = os.environ.get("UNSLOTH_MODEL_NAME") or os.environ.get("MODEL_ID") or ""
            out["UNSLOTH_MODEL_NAME"] = mid.replace("-", "_") or "gpt_oss"
        return out

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        if use_lora:
            _prepare_gpt_oss_lora_load()
        return {}

    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None:
        _patch_empty_router_aux_loss(model)

        cfg = getattr(model, "config", None)
        if cfg is not None:
            if hasattr(cfg, "output_router_logits"):
                cfg.output_router_logits = False
            if hasattr(cfg, "router_aux_loss_coef"):
                cfg.router_aux_loss_coef = 0.0

        if not use_lora:
            return

        # Informational only: the experts are expected to stay BF16 here (see
        # module docstring) — bnb can't quantize their fused 3D Parameter, and
        # that's the accepted tradeoff for loading straight off the BF16 repo.
        quantized = 0
        bf16 = 0
        for m in model.modules():
            if type(m).__name__ != "GptOssExperts":
                continue
            for proj_name in ("gate_up_proj", "down_proj"):
                proj = getattr(m, proj_name, None)
                base = getattr(proj, "base_layer", proj)
                if type(base).__name__ == "Linear4bit":
                    quantized += 1
                else:
                    bf16 += 1
        attn_fwd = ""
        for m in model.modules():
            if type(m).__name__ == "GptOssAttention":
                attn_fwd = getattr(m.forward, "__name__", type(m.forward).__name__)
                break
        print(
            f"gpt-oss: experts loaded from {os.environ.get('MODEL_ID')!r} — "
            f"{quantized} Linear4bit / {bf16} full-precision projection(s); "
            f"attn_impl={getattr(cfg, '_attn_implementation', None)!r} "
            f"attn_fwd={attn_fwd!r}",
            flush=True,
        )


hooks = GptOssHooks()
