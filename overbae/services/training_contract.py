import hashlib
import json

from modal_shared.decisions import DECISION_OBJECTIVE, DECISION_OBJECTIVES, TEXT_OBJECTIVE


def dataset_objective(cell):
    report = (cell.intent_report.get("train") or {}) if cell else {}
    if report.get("format") == "decision":
        return report.get("objective", DECISION_OBJECTIVE)
    return TEXT_OBJECTIVE


def contract(cell, hyperparameters=None):
    objective = (hyperparameters or {}).get("objective") or dataset_objective(cell)
    native = objective in DECISION_OBJECTIVES
    return {
        "objective": objective,
        "inference_contract": "decision" if native else "chat",
        "target": "declared_decision_supervision" if native else "assistant_tokens",
        "target_semantics": (
            (cell.intent_report.get("train") or {}).get("target_semantics", {}) if cell else {}
        ),
        "losses": {
            "distribution": "cross_entropy",
            "ordinal_mean": "squared_error_of_normalized_expectation",
        }
        if native
        else {"assistant_tokens": "cross_entropy"},
        "methods": ["lora"] if native else ["lora", "full"],
        "providers": ["modal"] if native else ["modal", "baseten", "together_ai"],
        "metrics": [
            "cross_entropy",
            "brier",
            "hard_label_accuracy",
            "expected_score_mae",
            "coverage",
        ]
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
