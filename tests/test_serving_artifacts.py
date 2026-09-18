import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from modal_shared.serving import artifacts


def base(tmp_path):
    (tmp_path / "config.json").write_text('{"model_type":"test"}')
    (tmp_path / "model.safetensors").write_bytes(b"base-weights")
    return tmp_path


def test_base_is_sealed_once_and_mutation_fails_closed(tmp_path, monkeypatch):
    directory = base(tmp_path)
    first = artifacts.seal_base(directory, "org/base")
    hasher = Mock(side_effect=AssertionError("must not rehash a sealed base"))
    monkeypatch.setattr(artifacts, "digest_file", hasher)
    assert artifacts.seal_base(directory, "org/base") == first
    with pytest.raises(ValueError, match="repository"):
        artifacts.seal_base(directory, "another/base")
    (directory / "model.safetensors").write_bytes(b"new-weights!")
    with pytest.raises(ValueError, match="changed"):
        artifacts.read_base_manifest(directory)


def test_base_manifest_tampering_is_rejected(tmp_path):
    directory = base(tmp_path)
    manifest = artifacts.seal_base(directory, "org/base")
    manifest["files"]["model.safetensors"]["sha256"] = "0" * 64
    (directory / artifacts.BASE_MANIFEST).write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="identity"):
        artifacts.read_base_manifest(directory)


def test_seal_rejects_file_changes_during_hash(tmp_path, monkeypatch):
    directory = base(tmp_path)
    original = artifacts.digest_file

    def changed(path):
        digest = original(path)
        path.write_bytes(b"changed")
        return digest

    monkeypatch.setattr(artifacts, "digest_file", changed)
    with pytest.raises(ValueError, match="while hashing"):
        artifacts.seal_base(directory, "org/base")
    assert not (directory / artifacts.BASE_MANIFEST).exists()


@pytest.mark.parametrize("name", ["../secret", "/tmp/secret", "..", "."])
def test_manifest_paths_cannot_escape(tmp_path, name):
    with pytest.raises(ValueError, match="basenames"):
        artifacts.validate_files(tmp_path, {name: {}})


def test_symlink_is_not_an_immutable_artifact(tmp_path):
    (tmp_path / "real").write_bytes(b"weight")
    (tmp_path / "linked").symlink_to(tmp_path / "real")
    with pytest.raises(ValueError, match="regular"):
        artifacts.file_state(tmp_path / "linked")


def test_artifact_contract_and_immutable_generation(tmp_path):
    contract = {"base": "a", "schema": 1, "profile": "engine"}
    assert artifacts.artifact_directory(tmp_path, contract) is None
    directory = tmp_path / artifacts.digest_json(contract) / ("a" * 32)
    directory.mkdir(parents=True)
    weights = directory / "part-0000.safetensors"
    weights.write_bytes(b"weight")
    payload = {
        "contract": contract,
        "files": {
            weights.name: {
                **artifacts.file_state(weights),
                "sha256": artifacts.digest_file(weights),
            }
        },
    }
    manifest = {**payload, "identity": artifacts.digest_json(payload)}
    artifacts.atomic_json(directory / "ready.json", manifest)
    artifacts.atomic_json(directory.parent / "current.json", {"generation": directory.name})
    assert artifacts.artifact_directory(tmp_path, contract) == directory
    with pytest.raises(ValueError, match="contract"):
        artifacts.read_artifact(directory, {**contract, "base": "b"})
    artifacts.atomic_json(directory.parent / "current.json", {"generation": "../escape"})
    with pytest.raises(ValueError, match="generation"):
        artifacts.artifact_directory(tmp_path, contract)


def test_atomic_json_does_not_publish_an_incomplete_marker(tmp_path):
    marker = tmp_path / "ready.json"
    artifacts.atomic_json(marker, {"previous": True})
    with pytest.raises(TypeError):
        artifacts.atomic_json(marker, {"not_json": Path("x")})
    assert json.loads(marker.read_text()) == {"previous": True}
    assert list(tmp_path.iterdir()) == [marker]
