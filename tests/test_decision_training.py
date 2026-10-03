import copy
import math
import weakref

import pandas as pd
import pytest
from conftest import frozen_dataset

from modal_shared.decisions import decision_line, render_decision
from modal_shared.training_data import row_key
from overbae.models import Project
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.contract import measure, training_line
from overbae.services.finetuning_validator import validate_dataset, validate_rows
from overbae.services.training_preparation import request_preparation


def example():
    return {
        "decision": {
            "state": "A coin was tossed.",
            "question": "Which face is showing?",
            "kind": "choice",
            "options": ["Heads", "Tails"],
            "target_probabilities": [0.3, 0.7],
        }
    }


def test_soft_targets_survive_contract_export_and_preparation_identity():
    record = example()
    record["source"] = "coin"
    line = training_line(record)
    assert line == example()
    assert measure(pd.DataFrame([record]))["train"]["ok"]
    assert validate_rows([line]).valid
    other = copy.deepcopy(line)
    other["decision"]["target_probabilities"] = [0.4, 0.6]
    assert row_key(line) != row_key(other)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target_probabilities", [0.3, 0.6]),
        ("target_probabilities", [1.0]),
        ("target_probabilities", [float("nan"), 0.0]),
        ("target_probabilities", [-0.1, 1.1]),
        ("options", ["Heads", "Heads"]),
        ("kind", "freeform"),
        ("weight", 0),
        ("weight", math.inf),
        ("question", ""),
    ],
)
def test_invalid_decisions_cannot_enter_training(field, value):
    row = example()
    row["decision"][field] = value
    with pytest.raises(ValueError):
        decision_line(row)
    assert not validate_rows([row]).valid


def test_renderer_never_includes_targets_or_provenance():
    row = example()
    row["decision"]["target_probabilities"] = [0.123456789, 0.876543211]
    row["source"] = "secret-gold-provider"
    prompt = render_decision(row["decision"], ["A", "B"])
    assert "0.123456789" not in prompt
    assert "secret-gold-provider" not in prompt
    assert "Heads" in prompt and "Tails" in prompt
    assert "Which face is showing?" in prompt


def test_native_and_text_examples_cannot_be_mixed():
    text = {"messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hi"}]}
    assert not validate_rows([example(), text]).valid


@pytest.mark.django_db
def test_native_readiness_scans_without_retaining_large_input_rows(monkeypatch):
    project = Project.objects.create(name="Native readiness", slug="native-readiness")
    records = []
    for index in range(30):
        row = example()
        row["decision"]["state"] = str(index) + " evidence" * 1000
        records.append(row)
    dataset = frozen_dataset(project, records)
    original = row_store.iter_rows

    def bounded(cell):
        references = []
        for row in original(cell):
            assert sum(reference() is not None for reference in references) <= 2
            references.append(weakref.ref(row))
            yield row

    monkeypatch.setattr(row_store, "iter_rows", bounded)
    result = validate_dataset(str(dataset.id), validation_enabled=True, split_method="ordered")
    assert result.valid
    assert result.num_examples == 30
    assert result.stats["train_examples"] == 24 and result.stats["val_examples"] == 6


@pytest.mark.django_db
def test_native_full_weight_training_is_rejected_before_gpu_allocation(settings):
    settings.FINETUNING_BACKEND = "modal"
    project = Project.objects.create(name="Native method", slug="native-method")
    dataset = frozen_dataset(project, [example()])
    with pytest.raises(ValueError, match="decision training currently supports LoRA"):
        request_preparation(dataset.active_cell, "Qwen/Qwen3-8B", 4096, training_type="full")
