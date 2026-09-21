import hashlib
import json
import os
import re
import uuid
from pathlib import Path

BASE_MANIFEST = ".base-manifest.json"
ARTIFACT_SCHEMA = 1
SHARD_BYTES = 2 * 1024**3


def digest_file(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def digest_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def file_state(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular immutable file: {path}")
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def atomic_json(path: Path, value) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as target:
            json.dump(value, target, sort_keys=True)
            target.flush()
            os.fsync(target.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_files(directory: Path, files: dict) -> None:
    if not files:
        raise ValueError("Immutable manifest has no files")
    for name, expected in files.items():
        if Path(name).name != name or name in {".", ".."}:
            raise ValueError("Manifest filenames must be local basenames")
        if file_state(directory / name) != {
            "size": expected["size"],
            "mtime_ns": expected["mtime_ns"],
        }:
            raise ValueError(f"Immutable file changed: {name}")


def read_base_manifest(directory: Path) -> dict:
    manifest = json.loads((directory / BASE_MANIFEST).read_text())
    expected = digest_json({"repo": manifest["repo"], "files": manifest["files"]})
    if manifest["identity"] != expected:
        raise ValueError("Base manifest identity mismatch")
    validate_files(directory, manifest["files"])
    return manifest


def seal_base(directory: Path, repo: str) -> dict:
    # Only fetch_base_model calls this, under its existing single-writer mutex.
    if (directory / BASE_MANIFEST).exists():
        manifest = read_base_manifest(directory)
        if manifest["repo"] != repo:
            raise ValueError("Cached base repository mismatch")
        return manifest
    files = {}
    for path in sorted(directory.iterdir()):
        if path.name.startswith(".") or path.name == "chat_template.serve.jinja":
            continue
        if path.is_dir():
            continue
        before = file_state(path)
        checksum = digest_file(path)
        if file_state(path) != before:
            raise ValueError(f"Base changed while hashing {path.name}")
        files[path.name] = {**before, "sha256": checksum}
    if "config.json" not in files or not any(name.endswith(".safetensors") for name in files):
        raise ValueError("Cannot seal a base without config and safetensors weights")
    payload = {"repo": repo, "files": files}
    manifest = {**payload, "identity": digest_json(payload)}
    atomic_json(directory / BASE_MANIFEST, manifest)
    return manifest


def read_artifact(directory: Path, contract: dict) -> dict:
    marker = directory / "ready.json"
    manifest = json.loads(marker.read_text())
    if manifest["contract"] != contract:
        raise ValueError("Inference artifact contract mismatch")
    if manifest["identity"] != digest_json({k: v for k, v in manifest.items() if k != "identity"}):
        raise ValueError("Inference artifact manifest checksum mismatch")
    validate_files(directory, manifest["files"])
    return manifest


def artifact_directory(root: Path, contract: dict) -> Path | None:
    pointer = root / digest_json(contract) / "current.json"
    if not pointer.exists():
        return None
    generation = json.loads(pointer.read_text())["generation"]
    if not isinstance(generation, str) or re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("Invalid artifact generation")
    directory = pointer.parent / generation
    read_artifact(directory, contract)
    return directory
