"""One snapshot per base repo on the decision volume.

A complete directory is the copy later training and inference load. Hub etag
conflicts and newer config files are not fetched over it.
"""

import json
import os
from pathlib import Path

ROOT = Path("/artifacts/.bases")


def ensure(source: str) -> tuple[Path, bool]:
    """Return the local snapshot and whether this call wrote it."""
    if source.startswith("/") or Path(source).is_dir():
        _offline()
        return Path(source), False
    folder = ROOT / source.replace("/", "--")
    if _complete(folder):
        _offline()
        return folder, False
    from huggingface_hub import snapshot_download

    folder.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=source,
            local_dir=str(folder),
            ignore_patterns=["*.bin", "original/*", "*.pt"],
            token=os.environ.get("HF_TOKEN") or None,
        )
    except Exception:
        if not _complete(folder):
            raise
    if not _complete(folder):
        raise RuntimeError(f"Decision base {source} is incomplete at {folder}")
    _offline()
    return folder, True


def _offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"


def _complete(folder: Path) -> bool:
    if not folder.is_dir() or not (folder / "config.json").is_file():
        return False
    if not ((folder / "tokenizer.json").is_file() or (folder / "tokenizer_config.json").is_file()):
        return False
    if any(folder.rglob("*.incomplete")):
        return False
    index = folder / "model.safetensors.index.json"
    if index.is_file():
        try:
            shards = set((json.loads(index.read_text()).get("weight_map") or {}).values())
        except (OSError, json.JSONDecodeError):
            return False
        return bool(shards) and all(
            (folder / name).is_file() and (folder / name).stat().st_size > 0 for name in shards
        )
    return any(
        path.is_file() and path.suffix == ".safetensors" and path.stat().st_size > 0
        for path in folder.iterdir()
    )
