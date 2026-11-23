"""Helpers for shipping pure-stdlib packages into Modal containers."""

from __future__ import annotations

from pathlib import Path

import modal

_MODAL_SHARED_DIR = Path(__file__).resolve().parents[1]  # modal_shared/
_REPO_ROOT = _MODAL_SHARED_DIR.parent
_ASSETS_DIR = _REPO_ROOT / "overbae" / "services" / "sft_assets"
_ASSETS_REMOTE = "/root/sft_assets"
_CATALOG = _REPO_ROOT / "overbae" / "modal" / "models.json"
# engine.py parents[2] is /root when modal_shared lives at /root/modal_shared.
CATALOG_REMOTE = "/root/overbae/modal/models.json"


def attach_modal_shared(image: modal.Image) -> modal.Image:
    """Mount modal_shared and the model catalog.

    The catalog file is data. The Django package is not imported.
    """
    return image.add_local_python_source("modal_shared").add_local_file(
        str(_CATALOG), remote_path=CATALOG_REMOTE
    )


# Back-compat aliases — both used to ship distinct subsets before modal_shared
# became a single self-contained package.
attach_modelfam = attach_modal_shared
attach_serving_args = attach_modal_shared


def attach_sft_assets(image: modal.Image) -> modal.Image:
    return image.add_local_dir(str(_ASSETS_DIR), remote_path=_ASSETS_REMOTE)
