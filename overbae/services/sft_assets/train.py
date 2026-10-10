"""Shared SFT entrypoint for Baseten and Modal.

CRITICAL: do NOT import torch/transformers/trl/peft/datasets at this module's top
level. unsloth must be the process's first heavyweight import (see engine_unsloth.py),
and even a "harmless" import here can pull in trl/transformers first and silently
disable Unsloth's optimizations.
"""

from __future__ import annotations

import os

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.training_telemetry import record_stage

if __name__ == "__main__":
    record_stage(os.environ.get("BT_RUN_DIR"), "initializing_training_runtime")
    print('BT_STAGE {"stage": "initializing_training_runtime"}', flush=True)
    if os.environ.get("TRAINING_OBJECTIVE") in DECISION_OBJECTIVES:
        from decision_engine import main
    else:
        from engine_unsloth import main

    main()
