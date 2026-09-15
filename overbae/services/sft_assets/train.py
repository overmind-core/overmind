"""Shared SFT entrypoint for both Baseten and Modal — dispatches on USE_UNSLOTH.

Two independent env vars pick the combination:
  FINETUNING_BACKEND   baseten | modal          — which provider runs the job
  USE_UNSLOTH          true | false (default)   — which trainer engine runs it

CRITICAL: do NOT import torch/transformers/trl/peft/datasets at this module's top
level. Under USE_UNSLOTH=true, unsloth must be the process's first heavyweight
import (see engine_unsloth.py), and even a "harmless" import here can pull in
trl/transformers first and silently disable Unsloth's optimizations.
"""

from __future__ import annotations

import os

USE_UNSLOTH = os.getenv("USE_UNSLOTH", "false").strip().lower() in ("1", "true", "yes")

if __name__ == "__main__":
    if USE_UNSLOTH:
        from engine_unsloth import main  # noqa: PLC0415 — unsloth-first import lives here
    else:
        from engine_stock import main  # noqa: PLC0415

    main()
