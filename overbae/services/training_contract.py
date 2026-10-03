import hashlib
import json

from modal_shared.decisions import DECISION_OBJECTIVE, TEXT_OBJECTIVE


def contract(cell, hyperparameters=None):
    native = (hyperparameters or {}).get("objective") == DECISION_OBJECTIVE or (
        cell is not None and (cell.intent_report.get("train") or {}).get("format") == "decision"
    )
    return {
        "objective": DECISION_OBJECTIVE if native else TEXT_OBJECTIVE,
        "inference_contract": "decision" if native else "chat",
        "target": "full_probability_distribution" if native else "assistant_tokens",
        "methods": ["lora"] if native else ["lora", "full"],
        "providers": ["modal"] if native else ["modal", "baseten", "together_ai"],
        "metrics": ["cross_entropy", "brier", "hard_label_accuracy", "coverage"]
        if native
        else ["cross_entropy", "token_accuracy"],
        "evaluation": "native_probabilities" if native else "chat_generation",
        "max_gpus": 1 if native else None,
    }


def selection_record(
    cell,
    *,
    validation_cell=None,
    eval_cell=None,
    validation_enabled=True,
    validation_split_ratio=0.2,
    split_method="random",
    eval_set=None,
    evaluations=None,
):
    result = {}
    for name, selected in (("train", cell), ("validation", validation_cell), ("eval", eval_cell)):
        result[f"{name}_dataset"] = str(selected.dataset_id) if selected else None
        result[f"{name}_cell"] = str(selected.id) if selected else None
        result[f"{name}_fingerprint"] = selected.fingerprint if selected else None
        result[f"{name}_rows"] = selected.rows if selected else None
    result.update(
        validation_enabled=validation_enabled,
        validation_split_ratio=validation_split_ratio
        if validation_enabled and validation_cell is None
        else None,
        split_method=split_method if validation_enabled and validation_cell is None else None,
        eval_set=str(eval_set.id) if eval_set else None,
        evaluations=evaluations or {},
        grouping=["exact_input_content", "declared_case_group", "synthetic_seed"],
    )
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
