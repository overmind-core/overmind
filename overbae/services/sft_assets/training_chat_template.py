"""Exact-match ``{% generation %}`` swaps for families TRL will not auto-patch.

Each training file must render byte-identical text to its base.
"""

from __future__ import annotations

from pathlib import Path

_ASSETS_DIR = Path(__file__).resolve().parent

KNOWN_TEMPLATE_PATCHES: tuple[tuple[str, str], ...] = (
    ("llama_templates/llama3_1.jinja", "llama_templates/llama3_1_training.jinja"),
    ("llama_templates/llama3_2.jinja", "llama_templates/llama3_2_training.jinja"),
    ("antares_templates/antares.jinja", "antares_templates/antares_training.jinja"),
    ("olmo_templates/olmo3.jinja", "olmo_templates/olmo3_training.jinja"),
    ("qwen_templates/qwen3_coder.jinja", "qwen_templates/qwen3_coder_training.jinja"),
    ("gemma_templates/gemma4_e2b.jinja", "gemma_templates/gemma4_e2b_training.jinja"),
    ("gemma_templates/gemma4_12b.jinja", "gemma_templates/gemma4_12b_training.jinja"),
    ("gpt_oss_templates/gpt_oss_unsloth.jinja", "gpt_oss_templates/gpt_oss_unsloth_training.jinja"),
    ("muse_templates/muse.jinja", "muse_templates/muse_training.jinja"),
    (
        "nemotron_templates/nemotron35.jinja",
        "nemotron_templates/nemotron35_training.jinja",
    ),
    ("qwen_templates/qwen3_unsloth_06b.jinja", "qwen_templates/qwen3_unsloth_06b_training.jinja"),
    ("qwen_templates/qwen3_unsloth.jinja", "qwen_templates/qwen3_unsloth_training.jinja"),
    ("qwen_templates/qwen35_unsloth_08b.jinja", "qwen_templates/qwen35_unsloth_08b_training.jinja"),
    ("qwen_templates/qwen35_unsloth_4b.jinja", "qwen_templates/qwen35_unsloth_4b_training.jinja"),
    ("qwen_templates/qwen36_unsloth.jinja", "qwen_templates/qwen36_unsloth_training.jinja"),
    ("qwen_templates/qwen38_unsloth.jinja", "qwen_templates/qwen38_unsloth_training.jinja"),
)


def patch_known_training_template(tokenizer) -> bool:
    if not getattr(tokenizer, "chat_template", None):
        return False
    live = tokenizer.chat_template.rstrip("\n")
    for base_rel, training_rel in KNOWN_TEMPLATE_PATCHES:
        base_path = _ASSETS_DIR / base_rel
        if not base_path.exists():
            continue
        if live == base_path.read_text(encoding="utf-8").rstrip("\n"):
            training_path = _ASSETS_DIR / training_rel
            tokenizer.chat_template = training_path.read_text(encoding="utf-8")
            print(f"Patched chat template with {training_rel} for assistant-only loss")
            return True
    return False
