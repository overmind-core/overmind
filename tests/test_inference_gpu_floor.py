import pytest

from overbae.modal.gpu_selector import select_gpu
from overbae.modal.model_registry import get_model_config_any_backend


@pytest.mark.parametrize("context", [512, 4096, 8192, 131072])
def test_gemma_e4b_uses_l40s(context):
    config = get_model_config_any_backend("google/gemma-4-E4B-it")
    assert select_gpu(config, context)[0] == "L40S"


def test_gpu_floor_still_allows_larger_tiers():
    config = {
        **get_model_config_any_backend("google/gemma-4-E4B-it"),
        "total_params_b": 40,
    }
    assert select_gpu(config, 8192)[0] == "H200"


def test_gpu_floor_without_architecture_constants():
    config = {
        "total_params_b": 8,
        "inference": {"gpu_type": "L4", "min_vram_gb": 48},
    }
    assert select_gpu(config, 512)[0] == "L40S"
