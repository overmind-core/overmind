"""Model family classification — thin shim over modal_shared.modelfam.

The registry lives in ``modal_shared/`` at the repo root, deliberately outside
``overbae`` so it stays pure stdlib and never drags Celery/Django into a training
container. This file only bootstraps the import so the same code works from the
local source tree and from a Modal/Baseten container (``modal_shared`` at /root).
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_modelfam() -> None:
    try:
        import modal_shared.modelfam  # noqa: F401

        return
    except ImportError:
        pass
    candidates = (
        Path("/root"),
        # overbae/services/sft_assets/catalog.py → platform root
        Path(__file__).resolve().parents[3],
    )
    for root in candidates:
        if (root / "modal_shared" / "modelfam").is_dir() and str(root) not in sys.path:
            sys.path.insert(0, str(root))
            return


_ensure_modelfam()

from modal_shared.modelfam import FAMILY_NOTES, family_key, resolve  # noqa: E402

__all__ = ["FAMILY_NOTES", "_ensure_modelfam", "family_key", "resolve"]
