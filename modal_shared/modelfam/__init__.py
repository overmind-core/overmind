"""Pure-stdlib model-family registry shared by training, serving, and Django.

No torch / django / modal imports — ships into bare Modal/Baseten containers.
"""

from modal_shared.modelfam.chat_template import (
    has_training_generation_markers,
    restore_serve_chat_template,
    strip_training_generation_markers,
)
from modal_shared.modelfam.registry import (
    FAMILY_NOTES,
    all_force_mask_pairs,
    all_pretok_headers,
    family_key,
    fixups_for_model_type,
    is_llama31_family,
    is_natively_multimodal_blob,
    needs_trust_remote,
    resolve,
    serve_image_from_checkpoint,
    serve_image_key,
)
from modal_shared.modelfam.spec import FamilySpec
from modal_shared.modelfam.thinking import (
    ALWAYS_ON_THINKING_HF_IDS,
    catalog_hf_id,
    is_always_on_thinking_template,
)

__all__ = [
    "ALWAYS_ON_THINKING_HF_IDS",
    "FAMILY_NOTES",
    "FamilySpec",
    "all_force_mask_pairs",
    "all_pretok_headers",
    "catalog_hf_id",
    "family_key",
    "fixups_for_model_type",
    "has_training_generation_markers",
    "is_always_on_thinking_template",
    "is_llama31_family",
    "is_natively_multimodal_blob",
    "needs_trust_remote",
    "resolve",
    "restore_serve_chat_template",
    "serve_image_from_checkpoint",
    "serve_image_key",
    "strip_training_generation_markers",
]
