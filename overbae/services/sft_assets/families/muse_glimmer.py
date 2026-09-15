"""Muse Glimmer Unsloth hooks — multimodal FastModel, LoRA-only in catalog."""

from __future__ import annotations

from typing import Any

from families import DefaultHooks


class MuseGlimmerHooks(DefaultHooks):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        # get_model_name() makes an uncached network call to raw GitHub to check whether a
        # newer Unsloth release added a supported-model mapping for this (private, non-cataloged)
        # repo. A reachable GitHub raises NotImplementedError telling us to upgrade; an
        # unreachable one silently skips the check and loads fine — same run, same context length,
        # flaky pass/fail purely on that one HTTP call. Neuter it so loading is deterministic;
        # the actual weight download is unaffected (separate huggingface_hub code path).
        try:
            from unsloth.models import loader as _loader
            from unsloth.models import loader_utils as _lu

            _orig_get_model_name = _lu.get_model_name

            def _patched_get_model_name(*args: Any, **kwargs: Any) -> str:
                try:
                    return _orig_get_model_name(*args, **kwargs)
                except NotImplementedError:
                    return args[0] if args else kwargs["model_name"]

            _lu.get_model_name = _patched_get_model_name
            _loader.get_model_name = _patched_get_model_name
        except Exception:  # noqa: BLE001 — best-effort; worst case the flake returns
            pass
        return {}

    def fast_model_cls(self, fast_language_model: Any, fast_model: Any | None) -> Any:
        if fast_model is not None:
            return fast_model
        return fast_language_model

    def peft_gradient_checkpointing(self) -> bool | str:
        return "unsloth"

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]:
        return {
            "optim": "adamw_8bit",
            "gradient_checkpointing": True,
        }


hooks = MuseGlimmerHooks()
