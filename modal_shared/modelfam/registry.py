"""Ordered family resolver. Replaces sft_assets/catalog.family_key and the
six scattered substring classifiers across training / serving / Django.
"""

from __future__ import annotations

from modal_shared.modelfam.families import (
    ANTARES,
    DEFAULT,
    GEMMA4,
    GPT_OSS,
    LFM2,
    LLAMA,
    MINISTRAL,
    MUSE_GLIMMER,
    NEMOTRON,
    NEMOTRON35,
    OLMO,
    PHI4,
    QWEN,
    QWEN3_CODER,
    QWEN35,
    QWEN38,
    QWEN_MM,
    SHARED_PRETOK_HEADERS,
)
from modal_shared.modelfam.spec import FamilySpec

# Order is load-bearing — specific before general (qwen35/coder before qwen_mm before qwen).
_FAMILIES: tuple[FamilySpec, ...] = (
    GPT_OSS,
    PHI4,
    GEMMA4,
    MUSE_GLIMMER,
    LFM2,
    MINISTRAL,
    ANTARES,
    NEMOTRON35,
    NEMOTRON,
    LLAMA,
    QWEN38,
    QWEN35,
    QWEN3_CODER,
    QWEN_MM,
    QWEN,
    OLMO,
)

_BY_KEY: dict[str, FamilySpec] = {f.key: f for f in _FAMILIES}
_BY_KEY[DEFAULT.key] = DEFAULT

FAMILY_NOTES: dict[str, str] = {f.key: f.notes for f in _FAMILIES if f.notes}


def resolve(model_id: str) -> FamilySpec:
    """Return the FamilySpec for ``model_id``. Never raises — falls back to DEFAULT."""
    mid = (model_id or "").lower()
    for fam in _FAMILIES:
        if any(p in mid for p in fam.patterns):
            return fam
    return DEFAULT


def serve_image_key(*blobs: str) -> str:
    """First non-default FamilySpec.serve_image across id fragments, else ``vllm``."""
    for blob in blobs:
        if not blob:
            continue
        key = resolve(blob).serve_image
        if key != "vllm":
            return key
    return "vllm"


def serve_image_from_checkpoint(
    *,
    model_name: str = "",
    base_model: str = "",
    model_type: str = "",
    architectures: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Resolve serve image for a fine-tune whose served name may not contain the family."""
    return serve_image_key(
        model_name,
        base_model,
        model_type,
        " ".join(architectures or ()),
    )


def family_key(model_id: str) -> str:
    """Backward-compat shim for callers that only need the string key."""
    return resolve(model_id).key


def get(key: str) -> FamilySpec:
    return _BY_KEY.get(key, DEFAULT)


def all_families() -> tuple[FamilySpec, ...]:
    return _FAMILIES


def all_pretok_headers() -> tuple[str, ...]:
    """Union of every family's pretok header patterns + shared fallbacks.

    pretok tries all of these against rendered chat text — order doesn't matter
    for correctness as long as every family header is present.
    """
    seen: set[str] = set()
    out: list[str] = []
    for fam in _FAMILIES:
        for pat in fam.pretok_headers:
            if pat not in seen:
                seen.add(pat)
                out.append(pat)
    for pat in SHARED_PRETOK_HEADERS:
        if pat not in seen:
            seen.add(pat)
            out.append(pat)
    return tuple(out)


def all_force_mask_pairs() -> tuple[tuple[str, str], ...]:
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for fam in _FAMILIES:
        for pair in fam.force_mask_pairs:
            if pair not in seen:
                seen.add(pair)
                out.append(pair)
    return tuple(out)


def needs_trust_remote(model_id: str) -> bool:
    mid = (model_id or "").lower()
    if mid.startswith("nvidia/"):
        return True
    return resolve(model_id).trust_remote_code


def is_llama31_family(model_id: str) -> bool:
    """True for Llama-3.1/3.2/3.3 — the models the tool-track hand-rolled
    renderer was written for."""
    mid = (model_id or "").lower()
    fam = resolve(model_id)
    if fam.key != "llama":
        return False
    return any(p in mid for p in fam.llama31_patterns)


def is_natively_multimodal_blob(*parts: str) -> bool:
    """Match normalized model-id blob against any family's multimodal tokens."""
    blob = " ".join(parts).lower().replace("_", "-").replace(".", "-")
    for fam in _FAMILIES:
        if fam.natively_multimodal and any(tok in blob for tok in fam.multimodal_id_tokens):
            return True
    return False


def fixups_for_model_type(model_type: str) -> tuple[str, ...]:
    """Config-rewrite keys for weight_ops.fix_model_type, keyed by HF model_type.

    weight_ops only sees config.json (no catalog model_id), so this maps the
    serialized model_type string onto the same fixup keys FamilySpec carries.
    """
    mtype = (model_type or "").lower()
    if mtype == "qwen3_5_text":
        return ("nest_qwen3_5_text",)
    if mtype in ("lfm2", "lfm2_moe") or mtype.startswith("lfm"):
        return ("lfm_layer_types",)
    if mtype == "nemotron_h":
        return ("nemotron_h",)
    # Granite/Antares only — do NOT catch-all. gpt_oss (and others) use Hub
    # ``full_attention`` which vLLM/HF validate; rewriting to ``attention``
    # crashes serve (StrictDataclassClassValidationError).
    if "granite" in mtype or mtype.startswith("antares"):
        return ("granite_layer_types",)
    return ()
