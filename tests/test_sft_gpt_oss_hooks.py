from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "overbae" / "services" / "sft_assets"))


def test_lora_env_reenables_flex_compile(monkeypatch):
    monkeypatch.setenv("MODEL_ID", "unsloth/gpt-oss-20b-BF16")
    import families.gpt_oss as gpt_oss

    monkeypatch.setattr(gpt_oss, "_require_kernels", lambda: None)
    env = gpt_oss.hooks.env_overrides(use_lora=True)
    assert env["UNSLOTH_COMPILE_DISABLE"] == "0"
    assert env["UNSLOTH_ENABLE_FLEX_ATTENTION"] == "1"
    assert "gpt_oss" in env["UNSLOTH_MODEL_NAME"]
    assert env["UNSLOTH_GPT_OSS_BNB4BIT_DISABLE"] == "1"


def test_full_ft_env_does_not_flip_compile(monkeypatch):
    import families.gpt_oss as gpt_oss

    monkeypatch.setattr(gpt_oss, "_require_kernels", lambda: None)
    assert gpt_oss.hooks.env_overrides(use_lora=False) == {}
