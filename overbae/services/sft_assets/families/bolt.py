"""Nemotron 3.5 Bolt is a Gemma 4 E4B finetune whose checkpoint graph is not picklable.

Torch gradient checkpointing pickles ``ConfigModuleInstance`` and the step never
starts. Leaving checkpointing off fits a 512-token LoRA on one H100.
"""

from families.gemma4 import Gemma4Hooks


class BoltHooks(Gemma4Hooks):
    def peft_gradient_checkpointing(self) -> bool:
        return False

    def sft_config_overrides(self, *, use_lora: bool) -> dict:
        cfg = super().sft_config_overrides(use_lora=use_lora)
        cfg["gradient_checkpointing"] = False
        return cfg


hooks = BoltHooks()
