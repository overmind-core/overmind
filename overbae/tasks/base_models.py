"""Keep every catalog base staged on the weights Volume.

``.base_models/`` is the one place base weights live: the LoRA merge source, the shared serving
base an adapter rides, and the weights training loads. Fetching on demand works — the deploy path
blocks on ``fetch_base_model``, which is a global mutex — but the first job against a new model
then pays the whole download. This sweep pays it in advance instead.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable

from celery import shared_task

logger = logging.getLogger(__name__)

_REGISTER_APP = "overmind-register"


def catalog_base_models() -> list[str]:
    """The resolved HF repo id behind every enabled catalog entry, deduped.

    Resolution matters: the catalog id is what a user picks, but ``.base_models/`` is keyed on
    what actually gets downloaded, which for Modal is the unsloth mirror.
    """
    from overbae.modal.model_registry import all_model_entries, get_hf_base

    seen: dict[str, None] = {}
    for entry in all_model_entries():
        if entry.get("disabled"):
            continue
        try:
            seen.setdefault(get_hf_base(entry["id"], backend="modal"), None)
        except Exception:  # noqa: BLE001 — one bad row must not stall the sweep
            logger.warning("prewarm: could not resolve base for %s", entry.get("id"))
    return list(seen)


def missing_base_models(staged: Iterable[str]) -> list[str]:
    have = set(staged)
    return [repo for repo in catalog_base_models() if repo not in have]


@shared_task(name="overbae.tasks.base_models.prewarm_base_models")
def prewarm_base_models() -> dict:
    """Spawn a fetch for every catalog base not yet staged.

    Spawned rather than awaited: ``fetch_base_model`` is capped at one container, so the calls
    queue and download one at a time regardless. Re-running is free — a complete snapshot
    short-circuits — so the sweep converges instead of needing to track progress.
    """
    import modal

    env = os.environ.get("MODAL_ENVIRONMENT") or None

    try:
        staged = modal.Function.from_name(
            _REGISTER_APP, "list_base_models", environment_name=env
        ).remote()
    except Exception as exc:  # noqa: BLE001
        logger.warning("prewarm_base_models: could not list staged bases: %s", exc)
        return {"skipped": "unreachable"}

    missing = missing_base_models(staged)
    if not missing:
        return {"staged": len(staged), "missing": 0}

    fetch = modal.Function.from_name(_REGISTER_APP, "fetch_base_model", environment_name=env)
    spawned = []
    for repo in missing:
        try:
            fetch.spawn(base_model=repo)
            spawned.append(repo)
        except Exception as exc:  # noqa: BLE001
            logger.warning("prewarm_base_models: spawn failed for %s: %s", repo, exc)

    logger.info("prewarm_base_models: spawned %d fetches", len(spawned))
    return {"staged": len(staged), "missing": len(missing), "spawned": spawned}
