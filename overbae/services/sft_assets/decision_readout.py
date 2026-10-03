import json

import torch

from modal_shared.decision_batching import microbatches


def collate_inputs(rows, pad_token_id, device):
    if any("target_probabilities" in row or "weight" in row for row in rows):
        raise ValueError("Inference inputs cannot contain reference fields")
    width = max(len(row["input_ids"]) for row in rows)
    classes = max(len(row["option_token_ids"]) for row in rows)
    ids = torch.full((len(rows), width), pad_token_id, dtype=torch.long, device=device)
    attention = torch.zeros_like(ids)
    options = torch.zeros((len(rows), classes), dtype=torch.long, device=device)
    valid = torch.zeros_like(options, dtype=torch.bool)
    for index, row in enumerate(rows):
        length, count = len(row["input_ids"]), len(row["option_token_ids"])
        ids[index, :length] = torch.tensor(row["input_ids"], device=device)
        attention[index, :length] = 1
        options[index, :count] = torch.tensor(row["option_token_ids"], device=device)
        valid[index, :count] = True
    return {
        "input_ids": ids,
        "attention_mask": attention,
        "option_token_ids": options,
        "valid_options": valid,
    }


def collate(rows, pad_token_id, device):
    batch = collate_inputs(
        [{key: row[key] for key in ("input_ids", "option_token_ids")} for row in rows],
        pad_token_id,
        device,
    )
    target = torch.zeros_like(batch["option_token_ids"], dtype=torch.float32)
    for index, row in enumerate(rows):
        target[index, : len(row["option_token_ids"])] = torch.tensor(
            row["target_probabilities"], device=device
        )
    return {
        **batch,
        "target_probabilities": target,
        "weights": torch.tensor(
            [row["weight"] for row in rows], dtype=torch.float32, device=device
        ),
    }


def probability_vectors(logits, batch):
    valid = batch["valid_options"].to(logits.device)
    if not torch.isfinite(logits[valid]).all():
        raise ValueError("Decision logits are not finite")
    logp = logits.float().masked_fill(~valid, -torch.inf).log_softmax(-1)
    return [
        {"probabilities": values.exp().tolist(), "log_probabilities": values.tolist()}
        for values, mask in zip(logp.detach().cpu(), valid.cpu(), strict=True)
        for values in [values[mask]]
    ]


def decision_logits(model, batch):
    output = model.get_decoder()(
        input_ids=batch["input_ids"],
        attention_mask=batch["attention_mask"],
        use_cache=False,
        return_dict=True,
    )
    hidden = output.last_hidden_state
    positions = batch["attention_mask"].sum(-1) - 1
    states = hidden[torch.arange(len(positions), device=hidden.device), positions.to(hidden.device)]
    head = model.get_output_embeddings()
    # Apply the effective head (including adapters/quantization), on one state per
    # decision. Never allocate sequence × vocabulary logits or detach the decoder.
    logits = head(states.to(head.weight.device)).float()
    selected = logits.gather(-1, batch["option_token_ids"].to(logits.device))
    return selected.masked_fill(~batch["valid_options"].to(logits.device), -torch.inf)


def loss_terms(logits, batch):
    valid = batch["valid_options"].to(logits.device)
    targets = batch["target_probabilities"].to(logits.device)
    weights = batch["weights"].to(logits.device)
    if not torch.isfinite(logits[valid]).all():
        raise ValueError("Decision logits are not finite")
    logp = logits.float().log_softmax(-1).masked_fill(~valid, 0)
    # Padded targets are zero; 0 * -inf would otherwise poison every loss.
    terms = -(targets * logp).sum(-1)
    return (terms * weights).sum(), weights.sum()


@torch.no_grad()
def predict(model, pad_token_id, rows, output, model_identity, *, max_rows, max_tokens):
    model.eval()
    device = model.get_input_embeddings().weight.device
    count = 0
    for records in microbatches(rows, max_rows=max_rows, max_padded_tokens=max_tokens):
        if any(
            set(row) != {"key", "input_sha256", "input_ids", "option_token_ids", "kind"}
            for row in records
        ):
            raise ValueError("Prediction requires reference-free prepared inputs")
        batch = collate_inputs(records, pad_token_id, device)
        vectors = probability_vectors(decision_logits(model, batch), batch)
        for row, vector in zip(records, vectors, strict=True):
            output.write(
                json.dumps(
                    {
                        "key": row["key"],
                        "input_sha256": row["input_sha256"],
                        "kind": row["kind"],
                        "model_identity": model_identity,
                        **vector,
                    }
                )
                + "\n"
            )
            count += 1
        output.flush()
    return count
