import hashlib
import json
from unittest.mock import patch

import pytest

pytest.importorskip("transformers")

from modal_shared.decision_inference import input_digest
from overbae.services.sft_assets import prepare_decisions


class Tokenizer:
    eos_token = "end"
    pad_token = "end"
    init_kwargs = {"_commit_hash": "source-revision"}
    chat_template = "template"

    def get_vocab(self):
        return {"A": 3, "B": 4}

    def apply_chat_template(self, messages, **kwargs):
        return messages[0]["content"] + "<decision>"

    def encode(self, text, **kwargs):
        _, suffix = text.split("<decision>", 1)
        return [1, 2] + ([self.get_vocab()[suffix]] if suffix else [])

    def save_pretrained(self, path):
        path.mkdir(exist_ok=True)
        (path / "tokenizer.json").write_text("fixture")


def test_foundation_preparation_uses_sealed_base_without_a_training_artifact(tmp_path):
    request = {"state": "Evidence", "question": "Pick", "kind": "choice", "options": ["a", "b"]}
    inputs = tmp_path / "inputs.jsonl"
    inputs.write_text(
        json.dumps({"key": "row", "decision": request, "input_sha256": input_digest(request)})
        + "\n"
    )
    (tmp_path / "request.json").write_text(
        json.dumps(
            {
                "input_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
                "artifact_identity": "base:base-seal",
                "foundation": {
                    "model": "catalog-model",
                    "hf_model": "org/model",
                    "base_path": str(tmp_path / "base"),
                    "base_identity": "base-seal",
                },
                "context_length": 128,
            }
        )
    )
    with (
        patch.object(
            prepare_decisions,
            "read_base_manifest",
            return_value={"identity": "base-seal", "repo": "org/model"},
        ),
        patch.object(prepare_decisions.AutoTokenizer, "from_pretrained", return_value=Tokenizer()),
        patch.object(
            prepare_decisions,
            "codebook",
            return_value=[{"code": "A", "token_id": 3}, {"code": "B", "token_id": 4}],
        ),
        patch.object(
            prepare_decisions, "read_artifact", side_effect=AssertionError("no training artifact")
        ),
    ):
        prepare_decisions.main(tmp_path, None)
    report = json.loads((tmp_path / "preparation.json").read_text())
    assert report["ready_decisions"] == 1
    assert report["artifact_identity"] == "base:base-seal"
    assert report["foundation"]["base_identity"] == "base-seal"
    assert not (tmp_path / "final").exists()
    assert "target_probabilities" not in (tmp_path / "tokens.jsonl").read_text()


def test_foundation_preparation_refuses_changed_base_identity(tmp_path):
    (tmp_path / "request.json").write_text(
        json.dumps(
            {
                "artifact_identity": "base:original",
                "foundation": {
                    "hf_model": "org/model",
                    "base_path": str(tmp_path),
                    "base_identity": "original",
                },
                "context_length": 128,
            }
        )
    )
    with (
        patch.object(
            prepare_decisions,
            "read_base_manifest",
            return_value={"identity": "changed", "repo": "org/model"},
        ),
        pytest.raises(ValueError, match="identity"),
    ):
        prepare_decisions.main(tmp_path, None)
