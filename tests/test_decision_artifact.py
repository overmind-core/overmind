import json

import pytest

from modal_shared.decision_artifact import read_artifact, seal_artifact


def checkpoint(path):
    path.mkdir()
    (path / "adapter_model.safetensors").write_bytes(b"qualified adapter bytes")
    (path / "adapter_config.json").write_text(
        json.dumps({"base_model_name_or_path": "pinned/base"})
    )
    (path / "tokenizer.json").write_text("{}")
    (path / "decision.json").write_text(
        json.dumps(
            {
                "objective": "decision_cross_entropy",
                "renderer": "option_codes_json",
                "codebook": [{"code": "A", "token_id": 1}, {"code": "B", "token_id": 2}],
                "vocab_fingerprint": "vocab",
                "training": {"base_identity": "base-content-id"},
            }
        )
    )


def test_sealed_artifact_is_relocatable_and_records_exact_qualified_files(tmp_path):
    path = tmp_path / "candidate"
    checkpoint(path)
    report = {"decisions": 64, "max_absolute_error": 0, "tolerance": 1e-4}
    manifest = seal_artifact(path, report)
    path.rename(tmp_path / "final")
    assert read_artifact(tmp_path / "final") == manifest
    assert manifest["verification"] == report
    assert manifest["base_repository"] == "pinned/base"
    assert manifest["training"]["base_identity"] == "base-content-id"
    assert manifest["files"]["adapter_model.safetensors"]["size"] == 23


@pytest.mark.parametrize("change", ["weights", "tokenizer", "extra", "manifest"])
def test_changed_checkpoint_cannot_be_used_as_the_same_model(tmp_path, change):
    path = tmp_path / "candidate"
    checkpoint(path)
    seal_artifact(path, {"decisions": 64, "max_absolute_error": 0, "tolerance": 1e-4})
    file = {
        "weights": "adapter_model.safetensors",
        "tokenizer": "tokenizer.json",
        "extra": "unexpected.safetensors",
        "manifest": "artifact.json",
    }[change]
    (path / file).write_text("tampered")
    with pytest.raises(ValueError):
        read_artifact(path)


@pytest.mark.parametrize(
    "report",
    [
        {"decisions": 0, "max_absolute_error": 0, "tolerance": 1e-4},
        {"decisions": 64, "max_absolute_error": 0.1, "tolerance": 1e-4},
    ],
)
def test_unverified_checkpoint_is_not_published(tmp_path, report):
    path = tmp_path / "candidate"
    checkpoint(path)
    with pytest.raises(ValueError):
        seal_artifact(path, report)
    assert not (path / "artifact.json").exists()
