"""Decision-family views of the shared model catalog."""

from overbae.modal.model_registry import get_all_models_by_backend


def models() -> dict[str, dict]:
    return {
        model_id: {
            **entry,
            "training_available": entry["decision"]["family"] == "clef"
            and not entry.get("disabled"),
        }
        for model_id, entry in get_all_models_by_backend("decision", include_disabled=True).items()
    }


def get(model_id: str) -> dict | None:
    return next(
        (entry for key, entry in models().items() if key.casefold() == model_id.casefold()),
        None,
    )


def options() -> list[dict]:
    return [
        {
            "id": entry["id"],
            "name": entry["display"],
            "family": entry["decision"]["family"],
            "training_available": entry["training_available"],
            "unavailable_reason": entry.get("disabled_reason"),
            "max_context_length": entry["finetuning"]["context_length"],
            "limits": entry["decision"]["limits"],
            "decision_index": entry["decision"].get("decision_index"),
        }
        for entry in models().values()
    ]
