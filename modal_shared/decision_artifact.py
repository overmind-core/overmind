import json
import math
from pathlib import Path

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.serving.artifacts import atomic_json, digest_file, digest_json, file_state

MANIFEST = "artifact.json"


def inventory(directory):
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("Decision artifacts cannot contain symlinks")
        if path.is_dir() or path == directory / MANIFEST:
            continue
        before = file_state(path)
        checksum = digest_file(path)
        if file_state(path) != before:
            raise ValueError(f"Decision artifact changed while hashing {path.name}")
        files[path.relative_to(directory).as_posix()] = {
            "size": before["size"],
            "sha256": checksum,
        }
    if not {"adapter_config.json", "decision.json", "tokenizer.json"} <= files.keys():
        raise ValueError("Decision artifact is missing its adapter, tokenizer or decision contract")
    if not any(
        name.startswith("adapter_model") and name.endswith(".safetensors") for name in files
    ):
        raise ValueError("Decision artifact has no adapter weights")
    contract = json.loads((directory / "decision.json").read_text())
    if (
        contract.get("renderer") == "unsloth_clef"
        and not {"joint_head.safetensors", "joint_head_config.json", "unsloth_decision_config.json"}
        <= files.keys()
    ):
        raise ValueError("Decision artifact is missing its trained head or configuration")
    return files


def verify_report(report):
    decisions = report.get("decisions")
    error, tolerance = report.get("max_absolute_error"), report.get("tolerance")
    if type(decisions) is not int or decisions < 1:
        raise ValueError("Decision artifact requires nonempty reload verification")
    if not all(
        type(value) in {int, float} and math.isfinite(value) for value in (error, tolerance)
    ):
        raise ValueError("Decision reload verification must be finite")
    if not 0 <= error <= tolerance <= 1e-3:
        raise ValueError("Decision artifact did not pass reload verification")
    if tolerance > 1e-4 and report.get("weight_reload") != "exact":
        raise ValueError("Mixed-precision replay requires exact saved-weight verification")


def read_artifact(directory):
    directory = Path(directory)
    marker = directory / MANIFEST
    file_state(marker)
    manifest = json.loads(marker.read_text())
    payload = {key: value for key, value in manifest.items() if key != "identity"}
    if manifest.get("schema") not in {1, 2} or manifest.get("identity") != digest_json(payload):
        raise ValueError("Decision artifact manifest checksum mismatch")
    verify_report(manifest["verification"])
    if inventory(directory) != manifest["files"]:
        raise ValueError("Decision artifact files changed")
    return manifest


def seal_artifact(directory, verification):
    directory = Path(directory)
    verify_report(verification)
    if (directory / MANIFEST).exists():
        manifest = read_artifact(directory)
        if manifest["verification"] != verification:
            raise ValueError("Decision artifact was sealed with different verification")
        return manifest
    files = inventory(directory)
    contract = json.loads((directory / "decision.json").read_text())
    adapter = json.loads((directory / "adapter_config.json").read_text())
    if contract.get("objective") not in DECISION_OBJECTIVES:
        raise ValueError("Checkpoint does not have the native decision objective")
    if not contract.get("training", {}).get("base_identity"):
        raise ValueError("Decision artifact requires an immutable base identity")
    repository = adapter.get("base_model_name_or_path")
    if not isinstance(repository, str) or not repository:
        raise ValueError("Decision artifact requires a base repository")
    payload = {
        "schema": 2,
        "objective": contract["objective"],
        "renderer": contract["renderer"],
        "vocab_fingerprint": contract["vocab_fingerprint"],
        "training": contract["training"],
        "base_repository": repository,
        "verification": verification,
        "files": files,
    }
    manifest = {**payload, "identity": digest_json(payload)}
    atomic_json(directory / MANIFEST, manifest)
    return read_artifact(directory)
