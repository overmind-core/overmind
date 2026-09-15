"""Antares/Granite hooks — TRL num_experts alias; disable MoE aux loss."""

from __future__ import annotations

from typing import Any

from families import DefaultHooks


def _alias_num_experts(cfg: Any) -> None:
    """TRL chunked CE reads ``text_config.num_experts`` (same as gpt_oss).

    GraniteMoeHybridConfig only has ``num_local_experts`` — AttributeError otherwise.
    """
    try:
        _ = cfg.num_experts  # transformers PretrainedConfig raises if missing
    except AttributeError:
        cfg.num_experts = int(getattr(cfg, "num_local_experts", 0) or 0)


class AntaresHooks(DefaultHooks):
    def post_load(self, model: Any, tokenizer: Any, *, use_lora: bool) -> None:
        cfg = getattr(model, "config", None)
        if cfg is not None:
            _alias_num_experts(cfg)
            text = getattr(cfg, "text_config", None)
            if text is not None and text is not cfg:
                _alias_num_experts(text)
            cfg.output_router_logits = False
            cfg.router_aux_loss_coef = 0.0
        if hasattr(model, "router_aux_loss_coef"):
            model.router_aux_loss_coef = 0.0

    def sft_config_overrides(self, *, use_lora: bool) -> dict[str, Any]:
        # TRL treats any config with ``output_router_logits`` defined as MoE and
        # re-enables router logits unless SFTConfig.router_aux_loss_coef is 0.
        # Granite's load_balancing_loss_func returns int 0 → ``.to()`` crashes.
        return {
            "optim": "adamw_8bit",
            "gradient_checkpointing": True,
            "router_aux_loss_coef": 0.0,
        }


hooks = AntaresHooks()
