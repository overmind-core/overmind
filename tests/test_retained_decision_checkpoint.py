import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

pytest.importorskip("torch")
pytest.importorskip("transformers")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae/services/sft_assets"))

from overbae.services.sft_assets import decision_engine


def test_retained_checkpoint_resumes_verified_artifact_without_overwriting(tmp_path):
    model, tokenizer = Mock(), Mock()
    model.peft_config = {}

    def save_adapter(path):
        (path / "adapter_model.safetensors").write_bytes(b"adapter")
        (path / "adapter_config.json").write_text(
            json.dumps({"base_model_name_or_path": "test/base"})
        )

    model.save_pretrained.side_effect = save_adapter
    tokenizer.save_pretrained.side_effect = lambda path: (path / "tokenizer.json").write_text("{}")
    probe = tmp_path / "probe.jsonl"
    probe.write_text(json.dumps({"input_ids": [1]}) + "\n")
    prediction = {
        "key": "probe",
        "probabilities": [0.4, 0.6],
        "kind": "choice",
        "target_probabilities": [0, 1],
    }

    def evaluate(model, rows, tokenizer, destination):
        destination.write_text(json.dumps(prediction) + "\n")
        return {"eval_loss": 0.2}

    prepared = {
        "objective": "decision_cross_entropy",
        "renderer": "test",
        "codebook": [],
        "vocab_fingerprint": "vocab",
    }
    signature = {"base_identity": "base-seal"}
    with patch.object(decision_engine, "evaluate", side_effect=evaluate) as run:
        first = decision_engine.retain_checkpoint(
            model, tokenizer, tmp_path, probe, [1], prepared, signature, 10
        )
        repeated = decision_engine.retain_checkpoint(
            model, tokenizer, tmp_path, probe, [1], prepared, signature, 10
        )
    assert repeated == first
    assert run.call_count == 3
    assert model.save_pretrained.call_count == 1
    with pytest.raises(ValueError, match="another training state"):
        decision_engine.retain_checkpoint(
            model, tokenizer, tmp_path, probe, [1], prepared, {"base_identity": "changed"}, 10
        )
    assert not list((tmp_path / "checkpoints").glob("retaining-*"))
