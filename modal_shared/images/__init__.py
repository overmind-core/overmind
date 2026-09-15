"""Helpers for shipping pure-stdlib packages into Modal containers."""

from __future__ import annotations

from pathlib import Path

import modal

_MODAL_SHARED_DIR = Path(__file__).resolve().parents[1]  # modal_shared/
_REPO_ROOT = _MODAL_SHARED_DIR.parent
_ASSETS_DIR = _REPO_ROOT / "overbae" / "services" / "sft_assets"
_ASSETS_REMOTE = "/root/sft_assets"


def attach_modal_shared(image: modal.Image) -> modal.Image:
    """Mount the whole modal_shared package at /root so containers can import it.

    modal_shared has zero dependency on the ``overbae`` Django package (which
    would drag in Celery/Django settings on import), so it's safe to side-load
    wholesale — covers modelfam, shared.py, and serving/args.py alike.
    """
    return image.add_local_python_source("modal_shared")


# Back-compat aliases — both used to ship distinct subsets before modal_shared
# became a single self-contained package.
attach_modelfam = attach_modal_shared
attach_serving_args = attach_modal_shared


def attach_sft_assets(image: modal.Image) -> modal.Image:
    return image.add_local_dir(str(_ASSETS_DIR), remote_path=_ASSETS_REMOTE)
