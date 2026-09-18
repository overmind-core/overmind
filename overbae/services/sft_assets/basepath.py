"""Where a training run loads base weights from.

Kept out of ``common``, which imports torch and so cannot be exercised outside a training
container.
"""

from __future__ import annotations

import os


def base_weights_for(model_id: str) -> str:
    """The shared ``.base_models/`` snapshot. Celery waits on ``fetch_base_model`` first.

    ``MODEL_ID`` stays the identity everywhere else — family resolution, pretok and the adapter's
    recorded base all need the repo id, not a path. The name check matters because env_overrides
    can swap ``MODEL_ID`` after the worker resolved the path, and loading a different model's
    weights under this model's identity would corrupt the run silently.
    """
    staged = os.getenv("BASE_MODEL_PATH", "")
    want = model_id.replace("/", "--")
    if not staged or os.path.basename(staged) != want:
        raise RuntimeError(
            f"BASE_MODEL_PATH must be the volume snapshot for {model_id}, got {staged!r}"
        )
    if not _snapshot_is_usable(staged):
        raise RuntimeError(
            f"staged snapshot at {staged} is absent or incomplete — "
            "fetch_base_model must finish before GPU training starts"
        )
    return staged


def _snapshot_is_usable(path: str) -> bool:
    """``snapshot_download`` fetches small files first and streams shards through
    ``*.incomplete``, so a bare ``config.json`` proves nothing.
    """
    if not os.path.isfile(os.path.join(path, "config.json")):
        return False
    weights = False
    for root, _dirs, files in os.walk(path):
        for name in files:
            if name.endswith(".incomplete"):
                return False
            if name.endswith(".safetensors"):
                weights = weights or os.path.getsize(os.path.join(root, name)) > 0
    return weights
