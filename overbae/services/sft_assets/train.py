"""Shared SFT entrypoint for Baseten and Modal.

CRITICAL: do NOT import torch/transformers/trl/peft/datasets at this module's top
level. unsloth must be the process's first heavyweight import (see engine_unsloth.py),
and even a "harmless" import here can pull in trl/transformers first and silently
disable Unsloth's optimizations.
"""

from __future__ import annotations

if __name__ == "__main__":
    from engine_unsloth import main  # noqa: PLC0415 — unsloth-first import lives here

    main()
