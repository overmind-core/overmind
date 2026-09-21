import ast
import importlib.util
import json
import os
import sys
import time
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

ASSETS = Path(__file__).resolve().parents[1] / "overbae/services/sft_assets"


class Tensor(np.ndarray):
    device = "cpu"

    def numel(self):
        return self.size

    def element_size(self):
        return self.itemsize

    def to(self, device):
        return self

    def argmax(self, dim):
        return super().argmax(axis=dim)

    def sum(self, dim=None, **kwargs):
        return super().sum(axis=dim, **kwargs)


def tensor(values, **kwargs):
    return np.asarray(values, **kwargs).view(Tensor)


@pytest.fixture(params=["numpy"] + (["torch"] if importlib.util.find_spec("torch") else []))
def backend(request):
    if request.param == "torch":
        import torch

        return torch
    return SimpleNamespace(
        tensor=tensor,
        long=np.int64,
        no_grad=nullcontext,
        zeros=lambda shape, dtype, device: tensor(np.zeros(shape, dtype=dtype)),
    )


def load_class(filename, name, scope):
    # The GPU entrypoint imports Unsloth at module load. Run the actual metric
    # classes with CPU tensors without importing the CUDA-only training stack.
    path = ASSETS / filename
    node = next(
        node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.ClassDef) and node.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), scope)
    return scope[name]


class Decoder:
    def register_forward_hook(self, hook):
        self.hook = hook

    def __call__(self, hidden):
        self.hook(self, (), (hidden,))
        return hidden


class Head:
    def __init__(self, backend):
        self.weight = backend.tensor(np.eye(4).tolist())
        self.shapes = []

    def __call__(self, hidden):
        self.shapes.append(hidden.shape)
        return hidden @ self.weight.T


@pytest.fixture
def trainer(backend, monkeypatch):
    head, decoder = Head(backend), Decoder()
    model = SimpleNamespace(
        training=True,
        get_output_embeddings=lambda: head,
        get_decoder=lambda: decoder,
    )

    class BaseTrainer:
        def __init__(self):
            self.model = model
            self.accelerator = SimpleNamespace(gather_for_metrics=lambda value: value)
            self._metrics = defaultdict(lambda: defaultdict(list))
            self._total_train_tokens = 0

        def log(self, logs, *args, **kwargs):
            return logs

    forward = Mock()
    scope = {
        "os": os,
        "torch": backend,
        "TokenAccuracy": load_class("token_accuracy.py", "TokenAccuracy", {"torch": backend}),
        "SFTTrainer": BaseTrainer,
        "_HFTrainer": SimpleNamespace(compute_loss=forward),
    }
    instance = load_class("engine_unsloth.py", "_AccurateSFTTrainer", scope)()
    instance._token_accuracy.max_logit_bytes = 64
    monkeypatch.setenv("UNSLOTH_RETURN_LOGITS", "1")
    return instance, forward, decoder, head


@pytest.mark.parametrize("return_outputs", [False, True])
def test_accuracy_survives_empty_logits_and_mode_resets(trainer, backend, return_outputs):
    instance, forward, decoder, head = trainer
    labels = backend.tensor([[-100, -100, 1, 2], [-100, 2, -100, -100]])
    hidden = backend.tensor(np.eye(4)[[[0, 1, 0, 3], [2, 0, 0, 3]]].tolist())
    outputs = SimpleNamespace(logits=None)

    def compute_loss(self, model, inputs, **kwargs):
        assert os.environ["UNSLOTH_RETURN_LOGITS"] == "0"
        assert inputs["use_cache"] is False
        assert kwargs == {"return_outputs": True, "num_items_in_batch": 3}
        decoder(hidden)
        return 0.5, outputs

    forward.side_effect = compute_loss
    for mode in ("train", "eval", "train"):
        instance.model.training = mode == "train"
        os.environ["UNSLOTH_RETURN_LOGITS"] = "1"
        inputs = {
            "labels": labels,
            "attention_mask": backend.tensor([[1, 1, 1, 1], [1, 1, 0, 0]]),
        }
        result = instance.compute_loss(
            instance.model, inputs, return_outputs=return_outputs, num_items_in_batch=3
        )
        assert result == ((0.5, outputs) if return_outputs else 0.5)
        key = "eval_mean_token_accuracy" if mode == "eval" else "mean_token_accuracy"
        log = instance.log({"eval_loss" if mode == "eval" else "loss": 0.5})
        assert log[key] == pytest.approx(2 / 3)
        assert instance._token_accuracy.labels is None
        assert instance._token_accuracy.counts is None
        calls = len(head.shapes)
        decoder(hidden)  # Backward checkpoint recomputation must not count again.
        assert len(head.shapes) == calls

    assert instance._metrics["train"]["num_tokens"] == [12]
    assert forward.call_count == 3


def test_accuracy_is_token_weighted_and_train_eval_windows_are_separate(trainer, backend):
    instance, forward, decoder, _ = trainer

    def compute_loss(self, model, inputs, **kwargs):
        decoder(backend.tensor([[[1.0, 0.0, 0.0, 0.0]] * inputs["labels"].shape[1]]))
        return 0.5, SimpleNamespace(logits=None)

    forward.side_effect = compute_loss
    for mode, labels in [("train", [-100, 0]), ("eval", [-100, 1]), ("train", [-100, 1, 1, 1])]:
        instance.model.training = mode == "train"
        instance.compute_loss(instance.model, {"labels": backend.tensor([labels])})
    assert instance.log({"eval_loss": 0.5})["eval_mean_token_accuracy"] == 0
    assert instance.log({"loss": 0.5})["mean_token_accuracy"] == 0.25
    assert "mean_token_accuracy" not in instance.log({"loss": 0.5})


def test_chunked_projection_covers_large_batches_without_full_logits(trainer, backend):
    instance, _, decoder, head = trainer
    metric = instance._token_accuracy
    metric.max_logit_bytes = 4096
    metric.labels = backend.tensor([[-100] + [0] * 8192] * 2)
    hidden = backend.tensor([[[1.0, 0.0, 0.0, 0.0]] * 8193] * 2)
    decoder(hidden)
    assert metric.counts.tolist() == [[8192, 8192], [8192, 8192]]
    assert len(head.shapes) > 2
    assert max(shape[0] * 4 * head.weight.element_size() for shape in head.shapes) <= 4096


def test_fully_masked_tokens_do_not_invent_zero_accuracy(trainer, backend):
    instance, forward, decoder, head = trainer

    def compute_loss(*args, **kwargs):
        decoder(backend.tensor([[[1.0, 0.0, 0.0, 0.0]] * 2]))
        return 0.5, SimpleNamespace(logits=None)

    forward.side_effect = compute_loss
    instance.compute_loss(instance.model, {"labels": backend.tensor([[-100, -100]])})
    assert "mean_token_accuracy" not in instance.log({"loss": 0.5})
    assert not head.shapes


def test_forward_failure_clears_metric_capture(trainer, backend):
    instance, forward, _, _ = trainer
    forward.side_effect = ValueError("model failure")
    with pytest.raises(ValueError, match="model failure"):
        instance.compute_loss(instance.model, {"labels": backend.tensor([[-100, 1]])})
    assert instance._token_accuracy.labels is None


def test_missing_decoder_capture_is_not_silently_ignored(trainer, backend):
    instance, forward, _, _ = trainer
    forward.return_value = 0.5, SimpleNamespace(logits=None)
    with pytest.raises(RuntimeError, match="did not capture"):
        instance.compute_loss(instance.model, {"labels": backend.tensor([[-100, 1]])})


def test_decoder_label_mismatch_is_rejected(trainer, backend):
    instance, _, decoder, _ = trainer
    instance._token_accuracy.labels = backend.tensor([[-100, 1, 2]])
    with pytest.raises(ValueError, match="does not match"):
        decoder(backend.tensor([[[1.0, 0.0, 0.0, 0.0]] * 2]))


def test_accuracy_logs_reach_persisted_training_and_validation_records(tmp_path):
    callback_type = load_class(
        "common.py",
        "ProgressCallback",
        {
            "TrainerCallback": object,
            "TrainingArguments": object,
            "TrainerState": object,
            "TrainerControl": object,
            "Any": object,
            "Path": Path,
            "PER_DEVICE_BATCH": 4,
            "MAX_LENGTH": 4096,
            "torch": SimpleNamespace(cuda=Mock()),
            "_vram_gb": lambda *args: 0.0,
            "_host_rss_gb": lambda: 0.0,
            "time": time,
            "sys": sys,
            "json": json,
        },
    )
    callback = callback_type(tmp_path)
    args = SimpleNamespace(num_train_epochs=3)
    state = SimpleNamespace(global_step=1, epoch=0.5)
    callback.on_log(args, state, None, {"loss": 0.5, "mean_token_accuracy": 2 / 3})
    callback.on_evaluate(args, state, None, {"eval_loss": 0.4, "eval_mean_token_accuracy": 0.75})
    records = [json.loads(line) for line in (tmp_path / "metrics.jsonl").read_text().splitlines()]
    assert records[0]["event"] == "BT_PROGRESS"
    assert records[0]["token_accuracy"] == 0.6667
    assert records[1]["event"] == "BT_EVAL"
    assert records[1]["eval_token_accuracy"] == 0.75


@pytest.mark.parametrize("family", ["qwen3", "qwen3_5"])
def test_real_decoder_metric_matches_full_logits_without_changing_gradients(family):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    torch.manual_seed(42)
    text = {
        "vocab_size": 32,
        "hidden_size": 16,
        "intermediate_size": 32,
        "num_hidden_layers": 1,
        "num_attention_heads": 2,
        "num_key_value_heads": 1,
        "head_dim": 8,
        "layer_types": ["full_attention"],
        "attention_dropout": 0.0,
    }
    if family == "qwen3":
        model = transformers.Qwen3ForCausalLM(transformers.Qwen3Config(**text))
    else:
        config = transformers.Qwen3_5Config(
            text_config=text,
            vision_config={
                "depth": 1,
                "hidden_size": 16,
                "intermediate_size": 32,
                "num_heads": 2,
                "out_hidden_size": 16,
                "num_position_embeddings": 16,
            },
        )
        model = transformers.Qwen3_5ForConditionalGeneration(config)
    model.train()
    ids = torch.randint(3, 32, (2, 8))
    labels = ids.clone()
    labels[:, :3] = -100
    labels[1, 6:] = -100
    baseline = model(input_ids=ids, labels=labels, use_cache=False)
    expected = (baseline.logits[:, :-1].argmax(-1) == labels[:, 1:]) & (labels[:, 1:] != -100)
    baseline.loss.backward()
    gradients = {name: p.grad.clone() for name, p in model.named_parameters() if p.grad is not None}
    model.zero_grad()

    metric = load_class("token_accuracy.py", "TokenAccuracy", {"torch": torch})(
        model, max_logit_bytes=256
    )
    metric.labels = labels
    measured = model(input_ids=ids, labels=labels, use_cache=False)
    metric.labels = None
    torch.testing.assert_close(metric.counts[:, 0], expected.sum(dim=1))
    torch.testing.assert_close(metric.counts[:, 1], (labels[:, 1:] != -100).sum(dim=1))
    torch.testing.assert_close(measured.loss, baseline.loss, rtol=0, atol=0)
    measured.loss.backward()
    for name, parameter in model.named_parameters():
        if name in gradients:
            torch.testing.assert_close(parameter.grad, gradients[name], rtol=0, atol=0)
    metric.handle.remove()
