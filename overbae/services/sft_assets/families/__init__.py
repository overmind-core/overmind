"""Per-family TrainerHooks for the Unsloth / stock SFT engines.

Each module exposes a ``hooks`` object matching ``TrainerHooks``; engines look it
up via ``FamilySpec.hooks_module``.
"""

from __future__ import annotations

from typing import Any, Protocol


class TrainerHooks(Protocol):
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]: ...

    def fast_model_cls(self, fast_language_model: Any, fast_model: Any | None) -> Any: ...

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]: ...

    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None: ...

    def peft_gradient_checkpointing(self) -> bool | str: ...

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]: ...

    def device_map(self, n_gpus: int) -> str | dict | None: ...

    def peft_lora_dropout(self, default: float, *, model_id: str) -> float: ...


class DefaultHooks:
    def env_overrides(self, *, use_lora: bool) -> dict[str, str]:
        return {}

    def fast_model_cls(self, fast_language_model: Any, fast_model: Any | None) -> Any:
        return fast_language_model

    def load_kwargs(self, *, use_lora: bool) -> dict[str, Any]:
        return {}

    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None:
        return None

    def peft_gradient_checkpointing(self) -> bool | str:
        return "unsloth"

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]:
        return {"optim": "adamw_8bit", "gradient_checkpointing": True}

    def device_map(self, n_gpus: int) -> str | dict | None:
        return None

    def peft_lora_dropout(self, default: float, *, model_id: str) -> float:
        return default


_DEFAULT = DefaultHooks()


def load_hooks(hooks_module: str | None) -> TrainerHooks:
    if not hooks_module:
        return _DEFAULT
    import importlib

    mod = importlib.import_module(hooks_module)
    return getattr(mod, "hooks", _DEFAULT)
