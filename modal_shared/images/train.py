"""SFT training images keyed by frozen train-stack ids.

Pip pins live in ``docker/Dockerfile.train``. Do not edit a stack's pins in
place: add a stage, a new stack id, then point ``TRAIN_IMAGES`` at it. Modal's
``Image.from_dockerfile`` has no target flag, so ``ARG STACK`` selects the
stage. SageMaker builds that same file for ECR.
"""

from __future__ import annotations

from pathlib import Path

import modal

from modal_shared.images import attach_modelfam, attach_sft_assets
from modal_shared.stacks import (
    TRAIN_DECISION,
    TRAIN_U2026_7,
    TRAIN_U2026_8_18,
    TRAIN_U2026_8_GPOS,
    TRAIN_U2026_8_TF510,
    TRAIN_U2026_8_TF515,
    TRAIN_U2026_9_2,
    TRAIN_U2026_10_7,
)

_ROOT = Path(__file__).resolve().parents[2]
_DOCKERFILE = _ROOT / "docker" / "Dockerfile.train"


def _stack_image(stack: str) -> modal.Image:
    return attach_sft_assets(
        attach_modelfam(
            modal.Image.from_dockerfile(
                _DOCKERFILE,
                context_dir=_ROOT,
                build_args={"STACK": stack},
            )
        )
    )


# Every image in this module needs modal_shared attached, even ones whose
# functions never call resolve(): Modal re-imports the whole entrypoint file
# to reconstruct any function in it, and that file top-level-imports this
# module, regardless of which specific Function/image is being invoked.
light_image = attach_modelfam(modal.Image.debian_slim(python_version="3.11"))

TRAIN_IMAGES: dict[str, modal.Image] = {
    TRAIN_DECISION: _stack_image(TRAIN_DECISION),
    TRAIN_U2026_7: _stack_image(TRAIN_U2026_7),
    TRAIN_U2026_8_TF510: _stack_image(TRAIN_U2026_8_TF510),
    TRAIN_U2026_8_TF515: _stack_image(TRAIN_U2026_8_TF515),
    TRAIN_U2026_8_GPOS: _stack_image(TRAIN_U2026_8_GPOS),
    TRAIN_U2026_8_18: _stack_image(TRAIN_U2026_8_18),
    TRAIN_U2026_9_2: _stack_image(TRAIN_U2026_9_2),
    TRAIN_U2026_10_7: _stack_image(TRAIN_U2026_10_7),
}
