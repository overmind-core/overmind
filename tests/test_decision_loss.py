import copy
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from overbae.services.sft_assets.decision_readout import (  # noqa: E402
    collate,
    collate_inputs,
    decision_logits,
    loss_terms,
    probability_vectors,
)


class Decoder(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.embedding = torch.nn.Embedding(20, 4)

    def forward(self, input_ids, attention_mask, use_cache, return_dict):
        return SimpleNamespace(last_hidden_state=self.embedding(input_ids))


class Model(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.decoder = Decoder()
        self.head = torch.nn.Linear(4, 20)

    def get_decoder(self):
        return self.decoder

    def get_output_embeddings(self):
        return self.head

    def get_input_embeddings(self):
        return self.decoder.embedding


def rows():
    return [
        {
            "input_ids": [1, 2],
            "option_token_ids": [3, 5],
            "target_probabilities": [0.2, 0.8],
            "weight": 2,
        },
        {
            "input_ids": [4, 5, 6],
            "option_token_ids": [4, 6, 7],
            "target_probabilities": [0.1, 0.3, 0.6],
            "weight": 1,
        },
        {
            "input_ids": [7],
            "option_token_ids": [3, 6],
            "target_probabilities": [0.7, 0.3],
            "weight": 0.5,
        },
    ]


def test_padded_batch_matches_individual_readouts_and_retains_gradients():
    torch.manual_seed(4)
    model = Model()
    batch = collate(rows(), 0, "cpu")
    logits = decision_logits(model, batch)
    for index, row in enumerate(rows()):
        single = decision_logits(model, collate([row], 0, "cpu"))
        torch.testing.assert_close(logits[index, : len(row["option_token_ids"])], single[0])
    loss, weight = loss_terms(logits, batch)
    (loss / weight).backward()
    assert model.decoder.embedding.weight.grad.abs().sum() > 0
    assert model.head.weight.grad.abs().sum() > 0
    assert torch.isfinite(loss)
    assert logits.softmax(-1)[0, 2] == 0


def test_unequal_microbatches_match_full_optimizer_update():
    torch.manual_seed(5)
    full = Model()
    split = copy.deepcopy(full)
    for model, groups in [(full, [rows()]), (split, [rows()[:2], rows()[2:]])]:
        optimizer = torch.optim.AdamW(model.parameters(), lr=0.01)
        weight = sum(row["weight"] for row in rows())
        for group in groups:
            batch = collate(group, 0, "cpu")
            loss, _ = loss_terms(decision_logits(model, batch), batch)
            (loss / weight).backward()
        optimizer.step()
    for actual, expected in zip(split.parameters(), full.parameters(), strict=True):
        torch.testing.assert_close(actual, expected)


def test_binary_soft_ce_matches_bce():
    logits = torch.tensor([[0.2, 0.9]], requires_grad=True)
    batch = collate([rows()[0]], 0, "cpu")
    loss, weight = loss_terms(logits, batch)
    expected = torch.nn.functional.binary_cross_entropy_with_logits(
        logits[:, 1] - logits[:, 0], torch.tensor([0.8])
    )
    torch.testing.assert_close(loss / weight, expected)


def test_reference_free_readout_matches_training_with_ragged_options():
    torch.manual_seed(15)
    model = Model().eval()
    inputs = [{key: row[key] for key in ("input_ids", "option_token_ids")} for row in rows()]
    batch = collate_inputs(inputs, 0, "cpu")
    assert "target_probabilities" not in batch and "weights" not in batch
    logits = decision_logits(model, batch)
    torch.testing.assert_close(logits, decision_logits(model, collate(rows(), 0, "cpu")))
    results = probability_vectors(logits, batch)
    for index, result in enumerate(results):
        assert len(result["probabilities"]) == len(inputs[index]["option_token_ids"])
        assert sum(result["probabilities"]) == pytest.approx(1)
        torch.testing.assert_close(
            torch.tensor(result["log_probabilities"]).exp(),
            torch.tensor(result["probabilities"]),
        )


def test_probability_readout_keeps_finite_logs_when_probabilities_underflow():
    batch = collate_inputs([{"input_ids": [1], "option_token_ids": [3, 5]}], 0, "cpu")
    result = probability_vectors(torch.tensor([[1000.0, -1000.0]]), batch)[0]
    assert result["probabilities"] == [1.0, 0.0]
    assert result["log_probabilities"] == [0.0, -2000.0]
    with pytest.raises(ValueError, match="finite"):
        probability_vectors(torch.tensor([[0.0, torch.nan]]), batch)


def test_inference_collator_refuses_reference_fields():
    with pytest.raises(ValueError, match="reference"):
        collate_inputs(rows(), 0, "cpu")


def test_mean_supervision_optimises_expectation_without_imposing_a_distribution():
    row = {
        "input_ids": [1],
        "option_token_ids": [3, 5, 6],
        "target_mean": 2.0,
        "target_semantics": "ordinal_mean",
        "option_values": [1.0, 2.0, 3.0],
        "weight": 1,
    }
    batch = collate([row], 0, "cpu")
    for probabilities in ([0.25, 0.5, 0.25], [0.45, 0.1, 0.45]):
        logits = torch.tensor([probabilities]).log().requires_grad_()
        loss, weight = loss_terms(logits, batch)
        torch.testing.assert_close(loss / weight, torch.tensor(0.0))
    logits = torch.tensor([[2.0, 0.0, -2.0]], requires_grad=True)
    loss, _ = loss_terms(logits, batch)
    loss.backward()
    assert torch.isfinite(logits.grad).all()
    assert logits.grad[0, 0] > 0 and logits.grad[0, 2] < 0


def test_mixed_mean_and_distribution_gradients_match_separate_batches():
    mean = {
        "input_ids": [1],
        "option_token_ids": [3, 5, 6],
        "target_mean": 2.2,
        "target_semantics": "ordinal_mean",
        "option_values": [1.0, 2.0, 3.0],
        "weight": 2,
    }
    data = [rows()[0], mean]
    model = Model()
    combined = collate(data, 0, "cpu")
    total, _ = loss_terms(decision_logits(model, combined), combined)
    individual = []
    for row in data:
        batch = collate([row], 0, "cpu")
        loss, _ = loss_terms(decision_logits(model, batch), batch)
        individual.append(loss)
    torch.testing.assert_close(total, sum(individual))
