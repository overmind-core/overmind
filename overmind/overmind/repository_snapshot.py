from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

# Generated scan files cannot make their own source snapshot dirty.
_SOURCE_PATHS = [".", ":(exclude)overmind.toml", ":(exclude)overmind_capabilities.json", ":(exclude).overmind"]


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True, timeout=30).stdout


def _optional_git(root: Path, *args: str) -> str:
    try:
        return _git(root, *args).decode().strip()
    except subprocess.CalledProcessError:
        return ""


def _repository_name(remote: str, fallback: str) -> str:
    if "://" in remote:
        path = urlsplit(remote).path
    elif ":" in remote and "@" in remote.split(":", 1)[0]:
        path = remote.split(":", 1)[1]
    else:
        return fallback
    return path.strip("/").removesuffix(".git") or fallback


def capture_repository_snapshot(root: Path) -> dict | None:
    root = root.resolve()
    try:
        git_root = Path(_git(root, "rev-parse", "--show-toplevel").decode().strip())
        files = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *_SOURCE_PATHS)
        digest = hashlib.sha256()
        for name in sorted(set(files.split(b"\0")) - {b""}):
            path = root / os.fsdecode(name)
            digest.update(name + b"\0")
            if path.is_symlink():
                digest.update(b"link\0" + os.fsencode(os.readlink(path)))
            elif path.is_dir():
                nested = capture_repository_snapshot(path)
                if nested is None:
                    return None
                digest.update(b"submodule\0" + nested["fingerprint"].encode())
            elif path.exists():
                digest.update(str(path.stat().st_mode & 0o111).encode() + b"\0")
                with path.open("rb") as source:
                    content = hashlib.sha256()
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        content.update(chunk)
                digest.update(content.digest())
            else:
                digest.update(b"deleted")
            digest.update(b"\0")
        return {
            "repository": _repository_name(_optional_git(root, "remote", "get-url", "origin"), git_root.name),
            "directory": root.relative_to(git_root).as_posix(),
            "branch": _optional_git(root, "symbolic-ref", "--quiet", "--short", "HEAD"),
            "commit": _optional_git(root, "rev-parse", "--verify", "HEAD"),
            "dirty": bool(_git(root, "status", "--porcelain", "--untracked-files=all", "--", *_SOURCE_PATHS)),
            "fingerprint": digest.hexdigest(),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def begin_repository_scan(root: Path) -> None:
    root = root.resolve()
    snapshot = capture_repository_snapshot(root)
    directory = root / ".overmind"
    receipt = directory / "scan.json"
    if directory.is_symlink() or receipt.is_symlink():
        raise ValueError("Repository scan metadata cannot be written through a symlink.")
    directory.mkdir(exist_ok=True)
    receipt.write_text(
        json.dumps({
            "snapshot": snapshot,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        })
    )


def finish_repository_scan(root: Path) -> dict | None:
    receipt = root.resolve() / ".overmind" / "scan.json"
    if not receipt.exists():
        return None
    saved = json.loads(receipt.read_text())
    snapshot = saved["snapshot"]
    if snapshot is None:
        return None
    if capture_repository_snapshot(root) != snapshot:
        raise ValueError("Repository changed during the scan. Run /overmind setup again before syncing.")
    return {**snapshot, "scanned_at": saved["scanned_at"]}
