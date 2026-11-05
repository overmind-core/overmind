import json
from pathlib import Path

from modal_shared.serving.artifacts import (
    atomic_json,
    digest_file,
    digest_json,
    file_state,
    validate_files,
)

MANIFEST = "checkpoint.json"


def read_checkpoint(directory: Path) -> dict:
    manifest = json.loads((directory / MANIFEST).read_text())
    if manifest["identity"] != digest_json(manifest["files"]):
        raise ValueError("Checkpoint manifest identity mismatch")
    validate_files(directory, manifest["files"])
    return manifest


def seal_checkpoint(directory: Path) -> dict:
    adapter = (directory / "adapter_config.json").is_file()
    config = "adapter_config.json" if adapter else "config.json"
    json.loads((directory / config).read_text())
    json.loads((directory / "tokenizer_config.json").read_text())
    if not any(
        (directory / name).is_file() for name in ("tokenizer.json", "tokenizer.model", "vocab.json")
    ):
        raise ValueError("Checkpoint has no tokenizer vocabulary")
    prefix = "adapter_model" if adapter else "model"
    index = directory / f"{prefix}.safetensors.index.json"
    weights = (
        set(json.loads(index.read_text())["weight_map"].values())
        if index.exists()
        else {f"{prefix}.safetensors"}
    )
    if not weights:
        raise ValueError("Checkpoint has no weights")
    for name in weights:
        if Path(name).name != name or not name.endswith(".safetensors"):
            raise ValueError("Checkpoint has an invalid weight shard path")
        if file_state(directory / name)["size"] == 0:
            raise ValueError("Checkpoint has an empty weight shard")
    files = {}
    for path in sorted(directory.iterdir()):
        if path.name == MANIFEST or path.name.startswith(".") or path.is_dir():
            continue
        before = file_state(path)
        checksum = digest_file(path)
        if file_state(path) != before:
            raise ValueError("Checkpoint changed during publication")
        files[path.name] = {**before, "sha256": checksum}
    manifest = {"files": files, "identity": digest_json(files)}
    atomic_json(directory / MANIFEST, manifest)
    return manifest
