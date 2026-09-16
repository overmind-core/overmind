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
    # /root on Modal; the platform root when run from the repo; the staged workspace on
    # Baseten (where the file sits too shallow for a fixed parents[N] index). When the
    # package is already on the path and the import still failed, the caller's own import
    # re-raises the real error instead of this masking it.
    here = Path(__file__).resolve()
    for root in (Path("/root"), *here.parents):
        if (root / "modal_shared" / "modelfam").is_dir():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return


_ensure_modelfam()

from modal_shared.modelfam import FAMILY_NOTES, family_key, resolve  # noqa: E402

__all__ = ["FAMILY_NOTES", "_ensure_modelfam", "family_key", "resolve"]
