import json
from types import MethodType

import torch
from torch.nn.attention import SDPBackend, sdpa_kernel
from unsloth.models.clef import EncodedQuestion, EncodedRecord
from unsloth.models.decision import ClefDataCollator, pad_length

from modal_shared.decision_batching import microbatches
from modal_shared.decisions import TARGET_FIELDS


def restore_record(row):
    stored = row["record"]
    return EncodedRecord(
        input_ids=tuple(row["input_ids"]),
        questions=tuple(
            EncodedQuestion(
                question_id=q["question_id"],
                question_type=q["question_type"],
                question_span=tuple(q["question_span"]),
                option_spans=tuple(tuple(span) for span in q["option_spans"]),
                option_ids=tuple(q["option_ids"]),
            )
            for q in stored["questions"]
        ),
        record_id=stored["record_id"],
    )


class DecisionCollator:
    def __init__(self, pad_token_id):
        self.upstream = ClefDataCollator(pad_token_id, permute_fields=False)

    def __call__(self, rows):
        items = []
        for row in rows:
            record = restore_record(row)
            target = row.get("target_probabilities", [0.0] * len(row["options"]))
            items.append(
                {
                    "input_ids": row["input_ids"],
                    "record": record,
                    "qtypes": [q.question_type for q in record.questions],
                    "targets": [[target[i] for i in row["option_order"]]],
                }
            )
        batch = self.upstream(items)
        values = torch.zeros_like(batch["target"])
        means = torch.zeros(len(rows), dtype=torch.float32)
        mean_mask = torch.zeros(len(rows), dtype=torch.bool)
        for i, row in enumerate(rows):
            if "target_mean" in row:
                axis = row["option_values"]
                span = axis[-1] - axis[0]
                values[i, : len(axis)] = torch.tensor(
                    [(axis[j] - axis[0]) / span for j in row["option_order"]]
                )
                means[i] = (row["target_mean"] - axis[0]) / span
                mean_mask[i] = True
        return {
            **batch,
            "option_values": values,
            "target_means": means,
            "mean_mask": mean_mask,
            "weights": torch.tensor([r.get("weight", 1.0) for r in rows], dtype=torch.float32),
            "option_order": [r["option_order"] for r in rows],
        }


def collate(rows, pad_token_id, device):
    return {
        key: value.to(device) if isinstance(value, torch.Tensor) else value
        for key, value in DecisionCollator(pad_token_id)(rows).items()
    }


def head_layer_norm(module, values):
    # Upstream compiled LayerNorm changes its first-call numerics and checkpoint save graph.
    dtype = module.weight.dtype if module.weight is not None else values.dtype
    result = torch.ops.aten.native_layer_norm(
        values.to(dtype), module.normalized_shape, module.weight, module.bias, module.eps
    )[0]
    return result.to(values.dtype)


def configure_decision_head(model):
    for module in model.head.modules():
        if isinstance(module, torch.nn.LayerNorm):
            module.forward = MethodType(head_layer_norm, module)


def decision_logits(model, batch):
    inputs = {key: batch[key] for key in ("input_ids", "attention_mask", "records")}
    device = batch["input_ids"].device
    autocast = device.type == "cuda" and not getattr(model, "_unsloth_forced_float32", False)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    with (
        torch.autocast(device.type, dtype=dtype, enabled=autocast),
        sdpa_kernel([SDPBackend.FLASH_ATTENTION, SDPBackend.EFFICIENT_ATTENTION, SDPBackend.MATH]),
    ):
        return model(**pad_length(model, inputs))[0]


def probability_vectors(logits, batch):
    valid = batch["marker_mask"].to(logits.device)
    if not torch.isfinite(logits[valid]).all():
        raise ValueError("Decision logits are not finite")
    logp = logits.float().masked_fill(~valid, -torch.inf).log_softmax(-1).detach().cpu()
    result = []
    for values, order in zip(logp, batch["option_order"], strict=True):
        original = values[[order.index(i) for i in range(len(order))]]
        result.append(
            {"probabilities": original.exp().tolist(), "log_probabilities": original.tolist()}
        )
    return result


def loss_terms(logits, batch):
    valid = batch["marker_mask"].to(logits.device)
    weights = batch["weights"].to(logits.device)
    if not torch.isfinite(logits[valid]).all():
        raise ValueError("Decision logits are not finite")
    logp = logits.float().masked_fill(~valid, -torch.inf).log_softmax(-1)
    terms = -(batch["target"].to(logits.device) * logp.masked_fill(~valid, 0)).sum(-1)
    estimate = (logp.exp() * batch["option_values"].to(logits.device)).sum(-1)
    mean_terms = (estimate - batch["target_means"].to(logits.device)).square()
    terms = torch.where(batch["mean_mask"].to(logits.device), mean_terms, terms)
    return (terms * weights).sum(), weights.sum()


@torch.no_grad()
def predict(model, pad_token_id, rows, output, model_identity, *, max_rows, max_tokens):
    model.eval()
    device = next(model.parameters()).device
    count = 0
    for records in microbatches(rows, max_rows=max_rows, max_padded_tokens=max_tokens):
        if any(set(row) & {*TARGET_FIELDS, "weight"} for row in records):
            raise ValueError("Prediction requires reference-free prepared inputs")
        batch = collate(records, pad_token_id, device)
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
