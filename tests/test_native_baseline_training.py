import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("transformers")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae/services/sft_assets"))
decision_engine = importlib.import_module("overbae.services.sft_assets.decision_engine")


class TinyDecisionModel(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = torch.nn.Embedding(8, 4)
        self.head = torch.nn.Linear(4, 8)

    def get_input_embeddings(self):
        return self.embedding

    def get_output_embeddings(self):
        return self.head

    def get_decoder(self):
        return self

    def forward(self, input_ids, **kwargs):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))

    def save_pretrained(self, path):
        torch.save(self.state_dict(), Path(path) / "weights.pt")


@pytest.mark.parametrize("baseline", [None, "1", "0"])
def test_native_training_can_skip_baseline_without_skipping_final_validation(
    baseline, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    run = tmp_path / "run"
    checkpoint = run / "checkpoint"
    checkpoint.mkdir(parents=True)
    for key, value in {
        "RUN_DIR": str(run),
        "CHECKPOINT_DIR": str(checkpoint),
        "PER_DEVICE_BATCH": 2,
        "GRAD_ACCUM": 1,
        "N_EPOCHS": 1,
        "MAX_STEPS": 0,
    }.items():
        monkeypatch.setattr(decision_engine, key, value)
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    monkeypatch.setattr(decision_engine, "training_base_identity", lambda *args: "test-base")
    monkeypatch.setenv("BASE_MODEL_PATH", str(tmp_path))
    monkeypatch.setenv("MODEL_ID", "test/tiny")
    monkeypatch.delenv("RUNTIME_PROFILE", raising=False)
    monkeypatch.delenv("DECISION_CHECKPOINT_POLICY", raising=False)
    if baseline is None:
        monkeypatch.delenv("DECISION_PRE_TRAINING_BASELINE", raising=False)
    else:
        monkeypatch.setenv("DECISION_PRE_TRAINING_BASELINE", baseline)
    rows = [
        {
            "key": str(i),
            "kind": "choice",
            "input_ids": [1, 2 + i],
            "option_token_ids": [3, 4],
            "target_probabilities": [0.2, 0.8],
            "weight": 1,
        }
        for i in range(4)
    ]
    for name in ("data.jsonl", "val.jsonl"):
        Path(name).write_text("".join(json.dumps(row) + "\n" for row in rows))
    Path("preparation.json").write_text(
        json.dumps(
            {
                "objective": "decision_cross_entropy",
                "renderer": "fixture",
                "codebook": [],
                "vocab_fingerprint": "fixture",
            }
        )
    )
    model = TinyDecisionModel()
    original = model.head.weight.detach().clone()
    tokenizer = SimpleNamespace(pad_token_id=0, save_pretrained=Mock())
    decision_engine.train(model, tokenizer)
    assert not torch.equal(original, model.head.weight)
    assert (run / "decision-before.jsonl").exists() is (baseline != "0")
    assert json.loads((run / "decision-after.json").read_text())["decisions"] == 4
    assert len((run / "decision-reload-reference.jsonl").read_text().splitlines()) == 4
    signature = json.loads((checkpoint / "decision.json").read_text())["training"]
    assert signature["pre_training_baseline"] is (baseline != "0")
    telemetry = json.loads((run / "telemetry.json").read_text())
    assert telemetry["pre_training_baseline"]["status"] == (
        "completed" if baseline != "0" else "not_requested"
    )
    initial_events = (run / "stages.jsonl").read_text().count('"stage": "initial_validation"')
    # The CPU fixture has no CUDA RNGs to restore after the engine's one-GPU admission check.
    monkeypatch.setattr(torch.cuda, "device_count", Mock(side_effect=[1, 0]))
    decision_engine.train(model, tokenizer)
    assert (run / "stages.jsonl").read_text().count(
        '"stage": "initial_validation"'
    ) == initial_events
    monkeypatch.setenv("DECISION_PRE_TRAINING_BASELINE", "1" if baseline == "0" else "0")
    monkeypatch.setattr(torch.cuda, "device_count", lambda: 1)
    with pytest.raises(ValueError, match="different base/data/configuration"):
        decision_engine.train(model, tokenizer)
