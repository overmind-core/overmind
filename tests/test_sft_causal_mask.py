from types import SimpleNamespace

from overbae.services.sft_assets.causal_mask import install_training_causal_mask


def _model(module: str, model_type: str = "qwen3"):
    seen: dict = {}

    def forward(*_args, **kwargs):
        seen["mask"] = kwargs.get("causal_mask")
        return seen["mask"]

    forward.__module__ = module
    decoder = SimpleNamespace(forward=forward)
    model = SimpleNamespace(
        get_decoder=lambda: decoder,
        config=SimpleNamespace(model_type=model_type),
        decoder=decoder,
    )
    return model, seen


def test_missing_causal_mask_is_filled_for_qwen3():
    model, seen = _model("unsloth.models.llama")
    assert install_training_causal_mask(model, "triangular")
    model.decoder.forward(causal_mask=None)
    assert seen["mask"] == "triangular"
    model.decoder.forward(causal_mask="already")
    assert seen["mask"] == "already"


def test_other_forwards_are_left_alone():
    model, seen = _model("transformers.models.qwen3.modeling_qwen3")
    assert not install_training_causal_mask(model, "triangular")
    model.decoder.forward(causal_mask=None)
    assert seen["mask"] is None

    qwen35, _ = _model("unsloth.models.llama", model_type="qwen3_5")
    assert not install_training_causal_mask(qwen35, "triangular")
