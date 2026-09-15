"""Detect chat templates that force reasoning with no serve-time off switch.

Console policy: only ship bases where thinking can be disabled (Qwen/Gemma
``enable_thinking``, or instruct templates that never open ``<think>``).
Pure-reasoning checkpoints (e.g. LFM2.5-2.6B) must stay ``disabled`` in models.json.
"""

from __future__ import annotations

import re

# HF ids that hard-open thinking. Keep disabled if present in models.json.
ALWAYS_ON_THINKING_HF_IDS: frozenset[str] = frozenset(
    {
        "LiquidAI/LFM2.5-2.6B",
        "LiquidAI/LFM2.5-1.2B-Thinking",
    }
)

_GEN_PROMPT_BLOCK = re.compile(
    r"\{%-?\s*if\s+add_generation_prompt\s*-?%\}(.*?)\{%-?\s*endif\s*-?%\}",
    re.DOTALL | re.IGNORECASE,
)
_THINK_OPEN = re.compile(r"<\s*think\s*>", re.IGNORECASE)


def is_always_on_thinking_template(template: str) -> bool:
    """True when the generation prompt hard-opens ``<think>`` with no ``enable_thinking`` gate.

    Controllable families (Qwen3.5, Gemma 4) reference ``enable_thinking`` and are False.
    Instruct LFM templates omit ``<think>`` from the generation prompt and are False.
    """
    if not template or "enable_thinking" in template:
        return False
    for block in _GEN_PROMPT_BLOCK.findall(template):
        if _THINK_OPEN.search(block):
            return True
    # Inline / minified templates may omit Jinja if-wrapping in the snippet we see.
    if "add_generation_prompt" not in template and _THINK_OPEN.search(template):
        return False
    return False


def catalog_hf_id(entry: dict) -> str:
    return str(entry.get("hf_model_id") or entry.get("id") or "")
