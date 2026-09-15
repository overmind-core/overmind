"""Read ``finetuning.training_type.{lora,full}`` from models.json catalog rows, and derive
which training kinds a dataset's longest row actually fits.

A model can support LoRA at a longer context than full fine-tuning (LoRA's smaller
optimizer state leaves more VRAM for activations) — ``models.json`` records each kind's
own ``context_length`` for exactly that reason. Everywhere a training_type choice is
offered for a specific dataset must go through ``dataset_training_type``/
``usable_training_kinds`` here, not the catalog-only ``enabled`` flag, so a kind that
would truncate targets never reaches the user as a selectable option.
"""

from __future__ import annotations

from typing import Any

_KINDS = ("lora", "full")


def _training_type_block(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not entry:
        return {}
    tt = entry.get("training_type")
    if tt is None and "finetuning" in entry:
        tt = (entry.get("finetuning") or {}).get("training_type")
    return tt if isinstance(tt, dict) else {}


def _kind(kind: str) -> str:
    return kind if kind in _KINDS else "lora"


def normalize_training_type(raw: Any) -> dict[str, dict[str, Any]]:
    """Return ``{lora: {enabled, context_length, validated_context_length}, full: {...}}``;
    missing keys default to disabled/unset."""
    tt = raw if isinstance(raw, dict) else {}
    return {
        kind: {
            "enabled": bool((tt.get(kind) or {}).get("enabled", False)),
            "context_length": (tt.get(kind) or {}).get("context_length"),
            "validated_context_length": bool(
                (tt.get(kind) or {}).get("validated_context_length", False)
            ),
        }
        for kind in _KINDS
    }


def training_enabled(entry: dict[str, Any] | None, kind: str) -> bool:
    """Whether catalog entry allows ``kind`` (``"lora"`` or ``"full"``) at all, regardless
    of any particular dataset's context length."""
    return normalize_training_type(_training_type_block(entry))[_kind(kind)]["enabled"]


def training_context_length(entry: dict[str, Any] | None, kind: str) -> int | None:
    """Max fine-tuning context for ``kind`` on this model: the per-training-type value
    when set, else the model's flat fine-tuning context (models not yet split by kind)."""
    if not entry:
        return None
    per_kind = normalize_training_type(_training_type_block(entry))[_kind(kind)]["context_length"]
    if per_kind is not None:
        return int(per_kind)
    flat = entry.get("context_length_sft")
    if flat is None:
        flat = (entry.get("finetuning") or {}).get("context_length")
    return int(flat) if flat is not None else None


def dataset_training_type(
    entry: dict[str, Any] | None, *, max_tokens: int | None, headroom: int = 0
) -> dict[str, dict[str, Any]]:
    """``training_type`` narrowed to what this dataset's longest row actually fits:
    ``enabled`` is catalog-enabled AND (no ``max_tokens``, or the kind's context covers
    ``max_tokens + headroom``)."""
    tt = normalize_training_type(_training_type_block(entry))
    if not max_tokens:
        return tt
    for kind in _KINDS:
        block = tt[kind]
        if not block["enabled"]:
            continue
        ctx = training_context_length(entry, kind)
        if ctx is not None and ctx < max_tokens + headroom:
            block["enabled"] = False
    return tt


def usable_training_kinds(
    entry: dict[str, Any] | None, *, max_tokens: int | None = None, headroom: int = 0
) -> list[str]:
    """Kinds actually offerable for this dataset — catalog-enabled and, when ``max_tokens``
    is given, with enough context length."""
    tt = dataset_training_type(entry, max_tokens=max_tokens, headroom=headroom)
    return [kind for kind in _KINDS if tt[kind]["enabled"]]
