from overbae.services.sft_assets.packing import (
    packs_safely,
    sft_collator_flags,
    trl_flattens_micro_batch,
)


def test_sdpa_does_not_isolate_packed_rows():
    assert not packs_safely("sdpa")
    assert not packs_safely("eager")
    assert not packs_safely(None)


def test_flash_varlen_isolates_packed_rows():
    assert packs_safely("flash_attention_2")
    assert packs_safely("kernels-community/vllm-flash-attn3")


def test_collator_flags_do_not_flatten_a_packed_micro_batch():
    flags = sft_collator_flags()
    assert not trl_flattens_micro_batch(flags["packing"], flags["padding_free"])
    # padding_free=False is not enough: bfd packing turns it back on.
    assert trl_flattens_micro_batch(packing=True, padding_free=False)
