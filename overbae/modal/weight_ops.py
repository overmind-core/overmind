"""Merge LoRA adapters and FP8-quantize HF checkpoints for Modal serving.

Validated path:
  LoRA  → safetensors delta-merge into base → FP8_DYNAMIC → vLLM (no --enable-lora)
  Full  → FP8_DYNAMIC → vLLM

Do not quantize the base and attach LoRA at serve time — that path produced gibberish.
"""

from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import torch
from peft import LoraConfig
from safetensors import safe_open
from safetensors.torch import save_file

META_FILENAME = ".meta.json"

# Concurrent base shards during LoRA merge — each in-flight shard holds a full
# shard of tensors in RAM (~4-5 GB), and the work is Volume-read bound.
_MERGE_WORKERS = 4

# Sampling-only GenerationConfig fields that transformers>=5.x rejects when
# do_sample=False (see _sanitize_generation_config / _sanitize_generation_config_file).
_SAMPLING_ONLY_GENERATION_FIELDS = ("temperature", "top_p", "top_k")

# gpt-oss QLoRA trains Unsloth's BnB ModuleList experts (down_projs.N / gate_up_projs.N)
# but BF16 / vLLM serve fused experts.down_proj / gate_up_proj (no .weight suffix).
_GPT_OSS_FUSED_EXPERT = re.compile(
    r"^(?P<prefix>.+\.mlp\.experts)\.(?P<fused>down_proj|gate_up_proj)$"
)


def _nest_qwen3_5_text_config(cfg: dict, base_model_dir: Path | None) -> dict:
    """Saving a LoRA-merged Qwen3.5 writes a FLAT config.json (``model_type: "qwen3_5_text"``),
    because it loads as the text-only submodel. vLLM 0.25.x has no registry entry for that flat
    type, so ``AutoConfig`` resolves the nested ``Qwen3_5Config``, whose zero-arg
    ``Qwen3_5TextConfig()`` fallback silently substitutes class defaults for the real checkpoint
    dims — vLLM then builds embed_tokens/lm_head at the wrong width and fails a tensor-size assert.

    The fix reuses the base checkpoint's genuinely nested config.json as a template and swaps in
    the fine-tuned ``text_config``, hand-nesting the flat dict when the base is gone.
    """
    base_cfg_path = base_model_dir / "config.json" if base_model_dir else None
    if base_cfg_path and base_cfg_path.exists():
        base_cfg = json.loads(base_cfg_path.read_text())
        if isinstance(base_cfg.get("text_config"), dict):
            base_cfg["text_config"] = cfg
            print(
                "[weight_ops] Patched config.json: nested qwen3_5_text under base model's qwen3_5 wrapper"
            )
            return base_cfg
    # No base config available — hand-nest without a vision_config. Works fine
    # under --language-model-only, which never touches the vision sub-config.
    nested = {
        "model_type": "qwen3_5",
        "architectures": ["Qwen3_5ForConditionalGeneration"],
        "text_config": cfg,
    }
    print(
        "[weight_ops] Patched config.json: nested qwen3_5_text (no base config found — vision_config omitted)"
    )
    return nested


def fix_model_type(model_dir: Path, base_model_dir: Path | None = None) -> None:
    """Patch HF config quirks so vLLM can load the checkpoint."""
    from modal_shared.modelfam import fixups_for_model_type

    cfg_path = model_dir / "config.json"
    if not cfg_path.exists():
        return
    cfg = json.loads(cfg_path.read_text())
    changed = False
    mtype = str(cfg.get("model_type") or "").lower()
    fixups = fixups_for_model_type(mtype)

    if "nest_qwen3_5_text" in fixups and cfg.get("model_type") == "qwen3_5_text":
        cfg = _nest_qwen3_5_text_config(cfg, base_model_dir)
        changed = True

    layer_types = cfg.get("layer_types")
    if (
        "lfm_layer_types" in fixups
        and isinstance(layer_types, list)
        and any(t == "attention" for t in layer_types)
    ):
        # LFM2: Hub + vLLM 0.26 want "full_attention" | "conv".
        cfg["layer_types"] = ["full_attention" if t == "attention" else t for t in layer_types]
        changed = True
        print("[weight_ops] Patched config.json: LFM layer_types attention → full_attention")
    elif (
        "granite_layer_types" in fixups
        and isinstance(layer_types, list)
        and any(t == "full_attention" for t in layer_types)
    ):
        # GraniteMoeHybrid: vLLM only accepts "attention" | "mamba".
        cfg["layer_types"] = ["attention" if t == "full_attention" else t for t in layer_types]
        changed = True
        print("[weight_ops] Patched config.json: layer_types full_attention → attention")

    if "nemotron_h" in fixups:
        # Nano: hybrid_override_pattern is the spec; layers_block_type is a read-only
        # @property and a serialized copy crashes setattr on reload.
        # Lightning 3.5: layers_block_type IS the spec (no hybrid_override_pattern).
        if "layers_block_type" in cfg and "hybrid_override_pattern" in cfg:
            del cfg["layers_block_type"]
            changed = True
            print("[weight_ops] Patched config.json: dropped stale layers_block_type field")
        # Restore hybrid_override_pattern from base — PEFT merge drops it.
        if "hybrid_override_pattern" not in cfg and base_model_dir:
            base_cfg_path = base_model_dir / "config.json"
            if base_cfg_path.exists():
                base_cfg = json.loads(base_cfg_path.read_text())
                pattern = base_cfg.get("hybrid_override_pattern")
                if pattern:
                    cfg["hybrid_override_pattern"] = pattern
                    cfg.setdefault("num_hidden_layers", base_cfg.get("num_hidden_layers"))
                    changed = True
                    print(
                        f"[weight_ops] Patched config.json: restored hybrid_override_pattern "
                        f"from base model ({pattern})"
                    )

    if changed:
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")


def apply_fp8(source_dir: str, output_dir: str) -> None:
    """FP8_DYNAMIC (W8A8): per-channel weight scales, dynamic per-token activations.

    No calibration dataset. Recommended for Ada Lovelace / Hopper + vLLM.
    """
    from llmcompressor import oneshot
    from llmcompressor.modifiers.quantization import QuantizationModifier

    recipe = QuantizationModifier(
        targets="Linear",
        scheme="FP8_DYNAMIC",
        ignore=["lm_head"],
    )
    oneshot(
        model=source_dir,
        recipe=recipe,
        output_dir=output_dir,
        save_compressed=True,
    )


def _sanitize_generation_config_file(model_dir: Path) -> None:
    """Some base checkpoints ship a generation_config.json with ``top_p``/``top_k``/``temperature``
    set while ``do_sample`` stays False, and transformers 5.x makes ``save_pretrained`` raise on
    that combination — which aborts the re-save at the end of quantization. Clearing the fields
    beats forcing ``do_sample=True``, which would change the model's default inference behaviour."""
    path = model_dir / "generation_config.json"
    if not path.exists():
        return
    cfg = json.loads(path.read_text())
    if cfg.get("do_sample"):
        return
    changed = False
    for field in _SAMPLING_ONLY_GENERATION_FIELDS:
        if cfg.pop(field, None) is not None:
            changed = True
    if changed:
        path.write_text(json.dumps(cfg, indent=2))
        print(f"[weight_ops] Sanitized generation_config.json (do_sample=False) at {model_dir}")


def _copy_tree_contents(source_dir: Path, output_dir: Path) -> None:
    for item in source_dir.iterdir():
        dest = output_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)


def _has_tokenizer_files(model_dir: Path) -> bool:
    return (model_dir / "tokenizer.json").exists() or (model_dir / "tokenizer_config.json").exists()


def quantize_checkpoint(
    source_dir: Path,
    output_dir: Path,
    base_model_dir: Path | None = None,
    *,
    quantize: bool = True,
) -> None:
    """``quantize=False`` serves merged BF16 as-is, for catalog entries with
    ``fp8_supported: false``: llmcompressor's FP8_DYNAMIC recipe cannot handle some fused MoE
    expert layouts, and the per-expert-indexed keys it emits do not match vLLM's own loader
    (``KeyError: 'layers.N.mlp.experts.M.w2_bias'``)."""
    from transformers import AutoTokenizer

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _sanitize_generation_config_file(source_dir)

    if quantize:
        print(f"[weight_ops] FP8 quantizing {source_dir} → {output_dir}")
        apply_fp8(str(source_dir), str(output_dir))
    else:
        print(
            f"[weight_ops] fp8_supported=false — copying BF16 weights {source_dir} → {output_dir}"
        )
        _copy_tree_contents(source_dir, output_dir)

    # BF16 copy already brought tokenizer / processor files from the merged tree.
    # Reloading AutoTokenizer on Muse triggers a noisy empty-model_type warning and
    # can rewrite processor-linked tokenizer_config — skip when files are present.
    if quantize or not _has_tokenizer_files(output_dir):
        tokenizer = AutoTokenizer.from_pretrained(str(source_dir), trust_remote_code=True)
        tokenizer.save_pretrained(str(output_dir))
    fix_model_type(output_dir, base_model_dir=base_model_dir)


def _is_lora_key(key: str) -> bool:
    return key.endswith(".lora_A.weight") or key.endswith(".lora_B.weight")


def _lora_pair_prefix(tensor_name: str, lora_weights: dict[str, torch.Tensor]) -> str | None:
    """Also handles the Gemma 4 ClippableLinear layout, where the base weight lives at
    ``…q_proj.linear.weight`` but the adapter saved ``…q_proj.lora_A.weight``: Unsloth attaches to
    the outer name while safetensors store the inner Linear."""
    if not tensor_name.endswith(".weight"):
        return None
    prefix = tensor_name.rsplit(".", 1)[0]
    if f"{prefix}.lora_A.weight" in lora_weights and f"{prefix}.lora_B.weight" in lora_weights:
        return prefix
    if prefix.endswith(".linear"):
        outer = prefix[: -len(".linear")]
        if f"{outer}.lora_A.weight" in lora_weights and f"{outer}.lora_B.weight" in lora_weights:
            return outer
    return None


def _warn_unexpected_target_modules(
    lora_weights: dict[str, torch.Tensor],
    lora_config: LoraConfig,
) -> None:
    target_modules = getattr(lora_config, "target_modules", None)
    if not target_modules:
        return
    # Unsloth often saves a regex string (e.g. all-linear); set(str) would warn on
    # every character of the pattern.
    if isinstance(target_modules, str):
        return
    targets = set(target_modules)
    unexpected = [
        key for key in lora_weights if key.rsplit(".lora_", 1)[0].rsplit(".", 1)[-1] not in targets
    ]
    if unexpected:
        warnings.warn(
            f"{len(unexpected)} adapter key(s) not in target_modules {sorted(targets)}; "
            f"first: {unexpected[0]!r}",
            stacklevel=2,
        )


def _apply_gpt_oss_expert_loras(
    tensor_name: str,
    tensor: torch.Tensor,
    lora_weights: dict[str, torch.Tensor],
    lora_config: LoraConfig,
) -> tuple[torch.Tensor, set[str]]:
    """Fold ModuleList expert LoRAs into a fused ``experts.down_proj`` / ``gate_up_proj``.

    Returns ``(possibly_updated_tensor, consumed_adapter_keys)``. No-op when this
    tensor is not a fused gpt-oss expert weight or no matching LoRA pairs exist.
    """
    m = _GPT_OSS_FUSED_EXPERT.match(tensor_name)
    if m is None:
        return tensor, set()
    prefix = m.group("prefix")
    kind = "down_projs" if m.group("fused") == "down_proj" else "gate_up_projs"
    if tensor.ndim < 1:
        return tensor, set()

    scale = lora_config.lora_alpha / lora_config.r
    out = tensor
    consumed: set[str] = set()
    n_experts = int(tensor.shape[0])
    for i in range(n_experts):
        a_key = f"{prefix}.{kind}.{i}.lora_A.weight"
        b_key = f"{prefix}.{kind}.{i}.lora_B.weight"
        if a_key not in lora_weights or b_key not in lora_weights:
            continue
        delta = scale * (lora_weights[b_key] @ lora_weights[a_key])
        # BnB ModuleList experts are nn.Linear (out, in). Fused BF16 gpt-oss stores
        # gate_up_proj as (hidden, 2*inter) = Linear.weight.T; down_proj may match
        # either layout depending on transformers/Unsloth version — accept both.
        if delta.shape == tensor[i].shape:
            apply = delta
        elif delta.T.shape == tensor[i].shape:
            apply = delta.T
        else:
            raise ValueError(
                f"gpt-oss LoRA shape mismatch for {a_key}: "
                f"delta={tuple(delta.shape)} vs fused[{i}]={tuple(tensor[i].shape)}"
            )
        if out is tensor:
            out = tensor.clone()
        out[i] = out[i] + apply.to(dtype=out.dtype, device=out.device)
        consumed |= {a_key, b_key}
    return out, consumed


def _merge_lora_weight(
    tensor_name: str,
    tensor: torch.Tensor,
    lora_weights: dict[str, torch.Tensor],
    lora_config: LoraConfig,
) -> torch.Tensor:
    scale = lora_config.lora_alpha / lora_config.r
    lora_prefix = _lora_pair_prefix(tensor_name, lora_weights)
    if lora_prefix is None:
        raise KeyError(f"no LoRA pair for base weight {tensor_name!r}")
    delta_w = (
        lora_weights[f"{lora_prefix}.lora_B.weight"] @ lora_weights[f"{lora_prefix}.lora_A.weight"]
    )
    return tensor + scale * delta_w


def _process_single_file(
    safetensors_path: Path,
    lora_config: LoraConfig,
    lora_weights: dict[str, torch.Tensor],
    output_ckpt_path: Path,
) -> set[str]:
    """Returns the adapter keys this shard consumed. ``lora_weights`` is read-only here, and
    consumption is reported rather than deleted in place, so shards run concurrently without a
    check-then-delete race on the shared dict."""
    tensors: dict[str, torch.Tensor] = {}
    consumed: set[str] = set()
    with safe_open(safetensors_path, framework="torch") as handle:
        for tensor_name in handle.keys():  # noqa: SIM118  # safetensors handle is not a Mapping
            tensor = handle.get_tensor(tensor_name)
            expert_tensor, expert_consumed = _apply_gpt_oss_expert_loras(
                tensor_name, tensor, lora_weights, lora_config
            )
            if expert_consumed:
                tensor = expert_tensor
                consumed |= expert_consumed
            else:
                lora_prefix = _lora_pair_prefix(tensor_name, lora_weights)
                if lora_prefix is not None:
                    tensor = _merge_lora_weight(tensor_name, tensor, lora_weights, lora_config)
                    consumed |= {f"{lora_prefix}.lora_A.weight", f"{lora_prefix}.lora_B.weight"}
            tensors[tensor_name] = tensor
    save_file(tensors, filename=output_ckpt_path / safetensors_path.name)
    print(
        f"[weight_ops] wrote {output_ckpt_path / safetensors_path.name} ({len(consumed) // 2} merged)"
    )
    return consumed


def _load_lora_config(adapter: Path) -> LoraConfig:
    """Load adapter_config, dropping keys newer PEFT/Unsloth wrote that this image's PEFT rejects.

    Unsloth saves ``monteclora_config`` / ``velora_config``; older ``peft`` warns and ignores them.
    """
    raw = json.loads((adapter / "adapter_config.json").read_text())
    known = set(getattr(LoraConfig, "__dataclass_fields__", {}))
    known.add("peft_type")
    if not known:
        return LoraConfig.from_pretrained(str(adapter))
    dropped = sorted(k for k in raw if k not in known)
    if not dropped:
        return LoraConfig.from_pretrained(str(adapter))
    cleaned = {k: v for k, v in raw.items() if k in known}
    print(f"[weight_ops] Dropped unknown adapter_config keys for this peft: {dropped}")
    with tempfile.TemporaryDirectory() as td:
        Path(td).joinpath("adapter_config.json").write_text(json.dumps(cleaned, indent=2) + "\n")
        return LoraConfig.from_pretrained(td)


def merge_lora_weights(
    base_path: Path | str,
    adapter_path: Path | str,
    output_dir: Path | str,
) -> Path:
    """Shard-by-shard, not PEFT merge_and_unload: merges every matching lora_A/lora_B pair so MLP
    deltas are not dropped. Falls back to ``_merge_lora_weights_peft`` for hybrid architectures,
    whose linear-attention layers expose virtual sub-modules with no 1:1 base safetensors key."""
    base = Path(base_path)
    adapter = Path(adapter_path)
    output = Path(output_dir)

    safetensor_paths = sorted(base.glob("*.safetensors"))
    adapter_weights = adapter / "adapter_model.safetensors"
    if not safetensor_paths:
        raise FileNotFoundError(f"No *.safetensors in base checkpoint: {base}")
    if not adapter_weights.exists():
        raise FileNotFoundError(f"LoRA adapter weights not found: {adapter_weights}")
    if not (adapter / "adapter_config.json").exists():
        raise FileNotFoundError(f"LoRA adapter config not found: {adapter / 'adapter_config.json'}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    lora_config = _load_lora_config(adapter)
    lora_weights: dict[str, torch.Tensor] = {}
    with safe_open(adapter_weights, framework="torch") as handle:
        for tensor_name in handle.keys():  # noqa: SIM118  # safetensors handle is not a Mapping
            # PEFT saves adapter weights with a "base_model.model." prefix — strip it so
            # keys match the base model weight names (e.g. model.layers.0.mlp.down_proj.weight).
            norm_name = tensor_name
            if norm_name.startswith("base_model.model."):
                norm_name = norm_name[len("base_model.model.") :]
            if not _is_lora_key(norm_name):
                raise ValueError(
                    f"Unexpected adapter key (expected lora_A/lora_B): {tensor_name!r}"
                )
            lora_weights[norm_name] = handle.get_tensor(tensor_name)

    _warn_unexpected_target_modules(lora_weights, lora_config)

    # Shards hold disjoint keys and each is dominated by reading ~4-5 GB off the
    # network-backed Volume, so overlap them.
    print(
        f"[weight_ops] merge base={base} adapter={adapter} → {output} "
        f"({len(safetensor_paths)} shards, {_MERGE_WORKERS} workers)"
    )
    with ThreadPoolExecutor(max_workers=_MERGE_WORKERS) as pool:
        futures = [
            pool.submit(_process_single_file, path, lora_config, lora_weights, output)
            for path in safetensor_paths
        ]
        for future in futures:
            for key in future.result():
                lora_weights.pop(key, None)

    if lora_weights:
        # A hybrid architecture's linear-attention layers hold a fused in_proj_qkv weight
        # while PEFT exposes separate q/k/v virtual sub-modules, so the shard-level key
        # match fails. PEFT's merge_and_unload resolves that mapping via forward logic.
        unmerged = sorted(lora_weights)
        print(
            f"[weight_ops] {len(unmerged)} LoRA key(s) unmatched "
            f"(first: {unmerged[0]!r}) — falling back to PEFT merge_and_unload"
        )
        shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        _merge_lora_weights_peft(base, adapter, output)
        return output

    for path in base.iterdir():
        if path.suffix == ".safetensors":
            continue
        dest = output / path.name
        if path.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(path, dest)
        else:
            shutil.copy2(path, dest)

    # Prefer adapter chat template / tokenizer patches over base copies.
    for name in ("chat_template.jinja", "tokenizer_config.json", "tokenizer.json"):
        src = adapter / name
        if src.exists():
            shutil.copy2(src, output / name)

    print(f"[weight_ops] merge done — {output}")
    return output


def _sanitize_generation_config(model) -> None:
    """In-memory counterpart of :func:`_sanitize_generation_config_file`: without it,
    ``save_pretrained`` raises under transformers 5.x and aborts the whole merge."""
    gen_cfg = getattr(model, "generation_config", None)
    if gen_cfg is None or getattr(gen_cfg, "do_sample", False):
        return
    for field in _SAMPLING_ONLY_GENERATION_FIELDS:
        if getattr(gen_cfg, field, None) is not None:
            setattr(gen_cfg, field, None)


@contextlib.contextmanager
def _gemma4_clippable_peft_patch():
    """Stock PEFT rejects ``Gemma4ClippableLinear`` (an nn.Module wrapping nn.Linear), so LoRA
    injection is redirected to the inner ``.linear``. Unsloth patches this during training; the
    register merge path must do the same or ``PeftModel.from_pretrained`` dies on multimodal
    towers. See unsloth#4807 / peft#3129."""
    try:
        from transformers.models.gemma4.modeling_gemma4 import (  # noqa: PLC0415
            Gemma4ClippableLinear,
        )
    except ImportError:
        yield
        return

    from peft.tuners.lora.model import LoraModel  # noqa: PLC0415

    original = LoraModel._create_and_replace

    def _patched(
        self,
        peft_config,
        adapter_name,
        target,
        target_name,
        parent,
        current_key=None,
        **kwargs,
    ):
        if isinstance(target, Gemma4ClippableLinear):
            return original(
                self,
                peft_config,
                adapter_name,
                target.linear,
                "linear",
                target,
                current_key=current_key,
                **kwargs,
            )
        return original(
            self,
            peft_config,
            adapter_name,
            target,
            target_name,
            parent,
            current_key=current_key,
            **kwargs,
        )

    LoraModel._create_and_replace = _patched
    try:
        yield
    finally:
        LoraModel._create_and_replace = original


def _merge_lora_weights_peft(
    base: Path,
    adapter: Path,
    output: Path,
) -> None:
    """Fallback for hybrid/VLM architectures, used when the shard-by-shard merge leaves LoRA keys
    unmatched. Loads the whole model in CPU bfloat16 and merges; it runs on the same GPU worker as
    FP8 quantization, so OOM is unlikely up to ~70B."""
    from peft import PeftModel  # noqa: PLC0415
    from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

    print(f"[weight_ops] PEFT merge_and_unload: {base} + {adapter} → {output}")
    model = AutoModelForCausalLM.from_pretrained(
        str(base),
        trust_remote_code=True,
        torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True,
    )
    with _gemma4_clippable_peft_patch():
        model = PeftModel.from_pretrained(model, str(adapter))
    model = model.merge_and_unload()
    _sanitize_generation_config(model)
    model.save_pretrained(str(output), safe_serialization=True)

    # Copy tokenizer — prefer adapter overrides over base copies.
    tokenizer = AutoTokenizer.from_pretrained(str(base), trust_remote_code=True)
    tokenizer.save_pretrained(str(output))
    for name in ("chat_template.jinja", "tokenizer_config.json", "tokenizer.json"):
        src = adapter / name
        if src.exists():
            shutil.copy2(src, output / name)

    print(f"[weight_ops] PEFT merge done — {output}")


def _load_safetensor_dict(model_dir: Path) -> dict[str, torch.Tensor]:
    paths = sorted(model_dir.glob("*.safetensors"))
    if not paths:
        raise FileNotFoundError(f"No *.safetensors in {model_dir}")
    out: dict[str, torch.Tensor] = {}
    for path in paths:
        with safe_open(path, framework="torch") as handle:
            for name in handle.keys():  # noqa: SIM118
                out[name] = handle.get_tensor(name)
    return out


def overlay_full_weights(
    base_path: Path | str,
    full_path: Path | str,
    output_dir: Path | str,
) -> Path:
    """Unsloth ``FastModel`` full-FT saves can omit frozen multimodal params, and serving that
    artifact alone makes vLLM raise ``Following weights were not initialized``. Starts from the
    base shard layout and overwrites every key the FT checkpoint holds — trained weights win, the
    base fills the rest."""
    base = Path(base_path)
    full = Path(full_path)
    output = Path(output_dir)

    base_shards = sorted(base.glob("*.safetensors"))
    if not base_shards:
        raise FileNotFoundError(f"No *.safetensors in base checkpoint: {base}")

    ft_weights = _load_safetensor_dict(full)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    overwritten = 0
    for shard in base_shards:
        tensors: dict[str, torch.Tensor] = {}
        with safe_open(shard, framework="torch") as handle:
            for name in handle.keys():  # noqa: SIM118
                if name in ft_weights:
                    tensors[name] = ft_weights.pop(name)
                    overwritten += 1
                else:
                    tensors[name] = handle.get_tensor(name)
        save_file(tensors, filename=output / shard.name)

    leftover = sorted(ft_weights)
    if leftover:
        # Rare: FT introduced keys not in base (new params). Append as an extra shard.
        save_file(ft_weights, filename=output / "model-ft-extra.safetensors")
        print(
            f"[weight_ops] full overlay wrote {len(leftover)} FT-only key(s) → "
            f"model-ft-extra.safetensors (first={leftover[0]!r})"
        )

    # Prefer FT tokenizer/config overrides; fall back to base for anything missing.
    for path in base.iterdir():
        if path.name.endswith(".safetensors") or path.name == META_FILENAME:
            continue
        dest = output / path.name
        if path.is_dir():
            shutil.copytree(path, dest)
        else:
            shutil.copy2(path, dest)
    for path in full.iterdir():
        if path.name.endswith(".safetensors") or path.name == META_FILENAME:
            continue
        dest = output / path.name
        if path.is_dir():
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(path, dest)
        else:
            shutil.copy2(path, dest)

    # Rebuild index if base had one — single-shard bases often don't.
    index_src = base / "model.safetensors.index.json"
    if index_src.exists() and len(base_shards) > 1:
        # Keep base shard membership; FT-only keys land in model-ft-extra if any.
        shutil.copy2(index_src, output / index_src.name)
        if leftover:
            idx = json.loads((output / index_src.name).read_text())
            weight_map = idx.setdefault("weight_map", {})
            for name in leftover:
                weight_map[name] = "model-ft-extra.safetensors"
            (output / index_src.name).write_text(json.dumps(idx, indent=2) + "\n")

    print(
        f"[weight_ops] full overlay base={base} ft={full} → {output} "
        f"(overwrote {overwritten} keys, leftover={len(leftover)})"
    )
    return output


def prepare_fp8_weights(
    *,
    source_dir: Path,
    output_dir: Path,
    is_lora: bool,
    base_model_path: Path | None = None,
    quantize: bool = True,
) -> dict:
    work = Path(tempfile.mkdtemp(prefix="weight_ops_"))
    try:
        if is_lora:
            if base_model_path is None or not base_model_path.exists():
                raise FileNotFoundError(f"LoRA merge requires base model at {base_model_path}")
            merged_dir = work / "merged"
            merge_lora_weights(base_model_path, source_dir, merged_dir)
            quant_source = merged_dir
            merge_method: str | None = "safetensors"
        elif base_model_path is not None and base_model_path.exists():
            # Full SFT of multimodal bases often omits frozen tower weights —
            # overlay onto base before quantize/serve.
            merged_dir = work / "merged"
            overlay_full_weights(base_model_path, source_dir, merged_dir)
            quant_source = merged_dir
            merge_method = "full_overlay"
        else:
            quant_source = source_dir
            merge_method = None

        # Quantize STRAIGHT into output_dir (the Volume): staging into work/ and
        # copying after writes every output byte twice and needs both copies on local
        # disk at once (~216 GB for a 72B). Safe because nothing reads output_dir
        # until prepare_fp8 writes .meta.json with ready=true, and quantize_checkpoint
        # clears a stale dir first — a crash mid-write costs only a retry.
        quantize_checkpoint(
            quant_source, output_dir, base_model_dir=base_model_path, quantize=quantize
        )

        return {
            "quantization": "fp8" if quantize else "bf16",
            "is_lora": False,  # merged (+quantized, if applicable) — serve as a normal model
            "merge_method": merge_method,
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)
