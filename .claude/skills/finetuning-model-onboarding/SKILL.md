---
name: finetuning-model-onboarding
description: Rules for adding a new model or model family to the finetuning pipeline, or changing finetuning behavior for an existing one — engine-agnostic customization via family hooks instead of if/else in the shared script, TRL-compatible chat templates ({% generation %} / pretok_trl), H100-before-H200 GPU probing, populating real_max_context_length/validated_context_length in models.json, and setting finetuning cost/pricing for a new model. Use when onboarding a model to Modal+Unsloth finetuning, adding a ModelFamily, pretok_trl falls back with a training-compatible chat template error, a model fails on its assigned GPU/context length, or pricing a new model's training cost.
---

# Finetuning model onboarding

All finetuning runs on Modal with Unsloth (`overbae/services/sft_assets/engine_unsloth.py`). Every model and family in this pipeline goes through the same engine — the rules below exist to keep it that way.

## 1. Never branch on model/family in the shared script

`engine_unsloth.py` and `overbae/services/finetuning_runner.py`/`finetuning_policy.py` are shared by every model. If a new model needs different behavior:

- Do **not** add `if model_id == "..."` or `if family == "..."` branches to the engine, runner, or policy.
- Do add/extend a family hooks module under `overbae/services/sft_assets/families/<family>.py`, subclassing `DefaultHooks` (`families/__init__.py`) and overriding only the hook methods you need (`env_overrides`, `load_kwargs`, `post_load`, `device_map`, `sft_config_overrides`, `peft_lora_dropout`, `peft_gradient_checkpointing`, `fast_model_cls`). Export a single `hooks = XxxHooks()`.
- Register the family in `modal_shared/modelfam/families.py` as a `FamilySpec` (patterns, images, `hooks_module="families.<family>"`), then add it to the ordered `_FAMILIES` tuple in `modal_shared/modelfam/registry.py` — **specific patterns before general ones** (e.g. `qwen35`/`qwen_coder` before `qwen_mm` before `qwen`).
- The engine calls hooks uniformly (`load_hooks(_spec.hooks_module)` then `_hooks.<method>(...)`) — it never knows which family it's running. That's the contract to preserve.

Reference existing hooks files for scale: `qwen35.py` (two-line SDPA override) up to `gpt_oss.py` (monkeypatches + adapter base-model swap) — match the size of the override to the size of the actual quirk, don't build more than the model needs.

## 2. Chat template must be training-compatible (pretok Path A)

Unsloth labels come from `overbae/services/sft_assets/pretok.py`. Path A is TRL `get_training_chat_template` + `return_assistant_tokens_mask`. That only works if the live tokenizer template already has `{% generation %}` / `{% endgeneration %}` markers, or TRL can exact-match it to a bundled original, or we swap in a hand-patched twin.

If the log shows:

```
diag: pretok_trl unsupported (ValueError('The chat template is not training-compatible (missing prefix-preservation or `{% generation %}` markers) and patching is not supported for this template. ...')); using multi_header fallback
```

the job may still train, but assistant-only masks are the weaker Path B. **Onboarding is not done until Path A succeeds** (`pretok paths:` contains `trl_training_template` or `native_generation_markers`, not only `multi_header_fallback`).

How to check (tokenizer issue — LoRA is enough; skip a second Full run):

1. Probe the catalog **`hf_model_id`**, not the catalog `id`. Unsloth repos often ship a different `chat_template.jinja` than the upstream id; TRL and our patches match the live string after `from_pretrained`.
1. Tiny Modal LoRA (`MAX_STEPS=2`, short `MAX_LENGTH`) is enough. Read `runs/<run_id>/train_stdout.log` for `pretok paths:` / `pretok_trl unsupported`.
1. One size is not enough when Unsloth templates differ inside a family (Gemma 4 E2B vs 12B, Qwen3-0.6B vs 8B, Qwen3.5-0.8B vs 4B). Hash the Hub template for each `hf_model_id` you enable.

How to fix — do **not** branch in `engine_unsloth.py` / `pretok.py`:

1. Save the **live** Hub template as a base jinja under `overbae/services/sft_assets/` (existing dirs: `llama_templates/`, `qwen_templates/`, `gemma_templates/`, …).
1. Copy it to `*_training.jinja` and wrap only assistant-generated spans in `{% generation %}` / `{% endgeneration %}`. `{% generation %}` is a real Jinja block: it cannot open inside `{% if %}` and close after `{% endif %}`, and `{% if %}`/`{% else %}`/`{% endif %}` must sit wholly inside or wholly outside it. The training file must render **byte-identical** text to the base; markers are invisible in the rendered string. Compile every twin (see `test_every_training_template_compiles`).
1. Register `(base, training)` in `KNOWN_TEMPLATE_PATCHES` in `overbae/services/sft_assets/training_chat_template.py`. `pretok.py` and `engine_stock.py` both call `patch_known_training_template` — that is the only dispatch table.
1. Add a pair assertion in `tests/test_sft_training_chat_template.py` (or rely on `test_every_patch_pair_exists_and_training_has_markers`).
1. `sft_assets` is baked into the train image (`add_local_dir`). A pretok/jinja change does nothing until `modal deploy` of `overbae/modal/modal_sft_worker.py`. Re-run the LoRA probe after deploy.

## 3. If a model fails on its assigned environment, re-derive the GPU, don't patch around it

GPU selection for training lives in `finetuning_runner.py`'s `_GPU_TABLE` / `_GPU_TABLE_FULL` (params_b → gpu_type/count) plus the `_LONG_CONTEXT_H200` bump and any family-specific clamp (e.g. `clamp_gemma4_training_gpu` — Gemma4 forced onto 1×H200 because Unsloth's `device_map="balanced"` multi-GPU split is broken for it).

If a new model OOMs or errors on its assigned GPU:

1. Check whether the failure is architectural (needs a real device_map/env override → family hook, §1) or capacity (needs a different GPU/context ceiling → §4).
1. If it's a one-off model/family quirk, add a clamp or override scoped to that family only (see the Gemma4 precedent), not a new general rule in the shared table.
1. Re-run the context-length probe (§4) rather than guessing a new ceiling.

## 4. Deriving `real_max_context_length` and `validated_context_length`

For every new model, before it's usable for finetuning:

1. **Find the real max context length.** This is the HF `max_position_embeddings` (no RoPE/YaRN scaling) — it goes in `finetuning.context_length` in `models.json`, distinct from the top-level `context_length` (published inference window, which may be YaRN-extended).
1. **Probe each enabled training type (`full`, `lora`) independently.** Use `scripts/calibrate_activation_budget.py` (`--experiment e3` context sweep, spawns real Modal `sft_unsloth` jobs and reads peak VRAM) to find the largest context that actually trains without OOM.
1. **Try H100 first, then H200.** `TRAINING_GPU_VRAM_GB = {"H100": 80.0, "H200": 141.0}` (`finetuning_policy.py`) — H100 has less VRAM, so it's the cheaper GPU and must be tried first. Only fall back to H200 if the model can't reach its real max context length on H100.
1. **Record the result per training type** in `models.json` under `finetuning.training_type.<full|lora>`:
   - `context_length`: the largest context that trained successfully (equal to `finetuning.context_length` if the full ceiling was reached, lower otherwise).
   - `validated_context_length: true` once probed — this flag means "this number came from an actual training run on H100/H200," not an assumption.
1. Set `finetuning.real_max_context_length` to the max across validated training types, and keep every `training_type.*.context_length <= real_max_context_length`.

`tests/test_modelfam.py::test_baseten_real_max_context_length` enforces this shape for every `backend: "baseten"` entry — run it before considering onboarding done.

## 5. Everything model-specific goes in `models.json`

Model configuration — context lengths, batch sizes, training type enablement, GPU/VRAM-relevant architecture fields (`hidden_size`, `num_attn_layers`, `num_kv_heads`, `head_dim`, `fp8_supported`), pricing, disabled state — belongs in `overbae/modal/models.json`, not scattered across Python as constants or conditionals. The file's own `"comment"` field documents each field; read it before adding a new one. If a field doesn't exist yet and is genuinely per-model data (not behavior), add it to the schema there rather than hardcoding it in a script.

The training job's baseline eval scores the untouched base through OpenRouter when OpenRouter lists it (`model_catalog.resolve_served_slug` matches the lower-cased catalog `id` against the live model list), so no field is needed for `Qwen/Qwen3-8B` → `qwen/qwen3-8b`. Set `openrouter_id` only when the served slug is not the lower-cased id (`Qwen/Qwen2.5-7B-Instruct` → `qwen/qwen-2.5-7b-instruct`, `LiquidAI/LFM2.5-2.6B` → `liquid/lfm-2.5-2.6b:free`). A base OpenRouter does not list is served for the baseline from a Modal base deployment (`deploy_base_model_for_eval`) — that is the only case that spends GPU on a baseline.

## 6. Cost: usually nothing to add, one marketing floor to set

Actual training cost is **computed, not stored per model** — `overbae/services/finetuning_pricing.py` derives it from fields already in `models.json`:

- Baseten/Modal: `estimate_training_cost()` picks GPU count from `_BASETEN_GPU_COUNT_TIERS` keyed on `total_params_b`, estimates duration from FLOPs (`6·N·D` full / `4·N·D` LoRA at 35% assumed MFU), and bills GPU-count × minutes × the fixed H100 per-minute rate.
- Together: `training_price_per_million()` looks up a $/1M-token rate from `_TOGETHER_SFT_TIERS`, again keyed on `total_params_b`.

So as long as the new model's `total_params_b` is set correctly in `models.json`, cost estimation works automatically — don't add a new pricing branch or per-model rate to `finetuning_pricing.py`. The only case it returns `None` is a >100B-param Together model, which needs an individually negotiated rate (not in this catalog today).

The one thing to add by hand is the **marketing "from" floor**: `models.json`'s `pricing.train_from_usd` (paired with `pricing.run_from_usd_per_1m_output` — `model_library.py`'s `_pricing()` drops the whole block from the API response unless both are set). This is a display-only number for the model library UI, not read by the cost estimator. Set it by calling `estimate_training_run()`/`estimate_training_cost()` for a small representative dataset on the new model and rounding to a customer-facing number consistent with similarly-sized peer models already in the catalog (e.g. dense ~1-4B models cluster around `0.5–0.7`, larger dense/MoE tiers step up to `1.0–2.5`, frontier-scale up to `4.5–5.0`).

## 7. New `ModelFamily` needs a frontend icon

If the family has no existing icon mapping, it falls through to a generic simpleicons/placeholder fallback (see `model-provider.ts`'s `SIMPLEICONS_SLUG_FIXES`, and `model-provider-chip.tsx`'s `ProviderLogo` fallback chain). To add one:

1. Add a `ProviderId` variant and `PROVIDER_BY_SLUG` entry in `frontend/src/components/model-provider.ts` (or a `PROVIDER_ALIASES` entry if it should map onto an existing provider, e.g. Meta/NVIDIA).
1. If a dedicated icon should render (not the CDN fallback), import the `@lobehub/icons` component and add it to `PROVIDER_ICONS` in `frontend/src/components/model-provider-chip.tsx`.
1. Extend `inferProviderFromModelId()` if the model id doesn't carry an explicit provider prefix.

## Checklist for onboarding a new model

- [ ] Family resolves correctly (`modal_shared/modelfam/registry.py` pattern order) — add a `FamilySpec` only if genuinely new, otherwise reuse
- [ ] Family quirks live in a hooks module, not in `engine_unsloth.py`/`finetuning_runner.py`/`finetuning_policy.py`
- [ ] Chat template is pretok Path A on the **`hf_model_id` tokenizer** (log `pretok paths:` is `trl_training_template` or `native_generation_markers`). If TRL cannot auto-patch, add a byte-identical `{% generation %}` twin and register it in `training_chat_template.py` `KNOWN_TEMPLATE_PATCHES`; LoRA-only is enough to verify. Redeploy the SFT worker after jinja/pretok edits
- [ ] `finetuning.context_length` set to the real HF max position embeddings
- [ ] Context probed per training type, H100 before H200, via `calibrate_activation_budget.py`
- [ ] `training_type.{full,lora}.context_length` + `validated_context_length` set from actual probe results
- [ ] `real_max_context_length` set and consistent with validated training types
- [ ] `tests/test_modelfam.py` passes, including `test_baseten_real_max_context_length`
- [ ] `total_params_b` set correctly (drives auto-computed training cost — no manual rate needed)
- [ ] `openrouter_id` set if OpenRouter's slug for the model is not its lower-cased `id` (check `openrouter.ai/api/v1/models`); otherwise the baseline eval falls back to a Modal base deploy
- [ ] `pricing.train_from_usd` + `pricing.run_from_usd_per_1m_output` set for the model library display
- [ ] Frontend icon mapped if the family is new
- [ ] `scripts/sync_benchmarks.py` run once the model is in `models.json`, so any Artificial Analysis / HuggingFace benchmark results for it (`overbae/services/benchmarks/sync/leaderboard.py`, `sync/huggingface.py`) join the committed artifact and feed model recommendations
