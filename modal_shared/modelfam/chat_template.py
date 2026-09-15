"""Strip TRL ``{% generation %}`` markers so served chat templates stay inference-safe."""

from __future__ import annotations

import re

# TRL assistant-only-loss markers — byte-identical render under transformers≥4.46,
# but any version drift makes vLLM reject the template and 500 every chat request.
_GENERATION_TAG = re.compile(r"\{%-?\s*(?:end)?generation\s*-?%\}")


def strip_training_generation_markers(template: str) -> str:
    return _GENERATION_TAG.sub("", template)


def has_training_generation_markers(template: str | None) -> bool:
    return bool(template and _GENERATION_TAG.search(template))


def restore_serve_chat_template(tokenizer, original: str | None) -> None:
    """Put a serve-safe template back on ``tokenizer`` before checkpoint save.

    Prefer the pre-patch snapshot; if missing, strip markers from whatever is live.
    """
    live = getattr(tokenizer, "chat_template", None)
    if original is not None:
        if live != original:
            tokenizer.chat_template = original
        return
    if has_training_generation_markers(live):
        tokenizer.chat_template = strip_training_generation_markers(live)
