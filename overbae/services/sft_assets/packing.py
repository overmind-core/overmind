"""Whether packed rows stay isolated under the loaded attention kernel.

TRL's padding-free collator resets position_ids and emits no attention mask.
Only flash-attn varlen uses that reset to split documents. SDPA causal
attention still crosses packed rows, which is also why TRL warns.
"""

from __future__ import annotations

# trl.trainer.sft_trainer.FLASH_ATTENTION_VARIANTS (v1.10.0).
_VARLEN_ATTN = frozenset(
    {
        "flash_attention_2",
        "flash_attention_3",
        "kernels-community/flash-attn2",
        "kernels-community/flash-attn3",
        "kernels-community/vllm-flash-attn3",
    }
)


def packs_safely(attn_implementation: str | None) -> bool:
    return attn_implementation in _VARLEN_ATTN


def use_row_packing(requested: bool, attn_implementation: str | None) -> bool:
    """Concatenate rows only when the kernel splits them.

    Varlen kernels split documents only if position ids restart per row, and
    this trainer does not emit those ids. SDPA's causal mask never splits
    them. A dense block mask does, but it is quadratic in context.
    """
    if not (requested and packs_safely(attn_implementation)):
        return False
    # Varlen still needs per-row position ids, which this trainer does not emit.
    return False


def trl_flattens_micro_batch(
    packing: bool, padding_free: bool, packing_strategy: str = "bfd"
) -> bool:
    """TRL v1.10 forces padding-free for the bfd strategies even when the flag is false."""
    return padding_free or (packing and packing_strategy in {"bfd", "bfd_split"})


def sft_collator_flags() -> dict[str, bool]:
    """SFTConfig packing flags. Both stay off.

    Examples are already packed to max length. TRL's default strategy flattens
    the micro-batch into one longer sequence, and Unsloth truncates that to
    max_seq_length.
    """
    flags = {"packing": False, "padding_free": False}
    if trl_flattens_micro_batch(flags["packing"], flags["padding_free"]):
        raise RuntimeError("SFT collator would flatten an already-packed micro-batch.")
    return flags
