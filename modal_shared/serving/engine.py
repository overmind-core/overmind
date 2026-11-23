"""Dedicated and serverless engine for a base.

inference.dedicated_engine and inference.serverless_engine on the catalog row
win. Otherwise the family serve image. The catalog is mounted into Modal
images at /root/overbae/modal/models.json. A missing file means vllm
unless the family names another image.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

# Repo checkout, then the path attach_modal_shared writes into a Modal image.
_CATALOG_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "overbae" / "modal" / "models.json",
    Path("/root/overbae/modal/models.json"),
)
_ENGINE_KEYS = ("dedicated_engine", "serverless_engine")


def _catalog_file() -> Path | None:
    for path in _CATALOG_CANDIDATES:
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=1)
def _engines() -> dict[str, dict[str, str]]:
    catalog = _catalog_file()
    if catalog is None:
        return {}
    doc = json.loads(catalog.read_text())
    index: dict[str, dict[str, str]] = {}
    for row in doc.get("models") or []:
        inference = row.get("inference") or {}
        if not isinstance(inference, dict):
            continue
        chosen = {key: inference[key] for key in _ENGINE_KEYS if inference.get(key)}
        if not chosen:
            continue
        aliases = {
            row.get("id"),
            row.get("hf_model_id"),
            *(row.get("benchmark_hf_model_ids") or []),
        }
        for alias in aliases:
            if alias and alias not in index:
                index[str(alias)] = chosen
    return index


def _family_serve(repo: str) -> str:
    from modal_shared.modelfam.registry import resolve

    return resolve(repo).serve_image or "vllm"


def engine_for(repo: str) -> str:
    chosen = _engines().get(repo) or {}
    return chosen.get("dedicated_engine") or _family_serve(repo)


def serverless_engine(repo: str) -> str:
    """Shared-pool engine. Catalog measurement, then the family serve image."""
    chosen = _engines().get(repo) or {}
    return chosen.get("serverless_engine") or _family_serve(repo)
