"""Point vLLM at the base checkpoint without copying its weights.

Shard files are symlinks. Config, tokenizer, and the adapter chat template are
small copies, so a rewrite cannot land on the shared weights volume.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def stage_base(base: Path, adapter: Path, output: Path) -> Path:
    base = base.resolve()
    output = output.resolve()
    if output == base or output.is_relative_to(base):
        raise ValueError(f"refusing to stage onto the base checkpoint: {output}")
    shards = sorted(base.glob("*.safetensors"))
    if not shards:
        raise FileNotFoundError(f"No *.safetensors in base checkpoint: {base}")
    if not (adapter / "adapter_model.safetensors").is_file():
        raise FileNotFoundError(
            f"LoRA adapter weights not found: {adapter / 'adapter_model.safetensors'}"
        )
    if not (adapter / "adapter_config.json").is_file():
        raise FileNotFoundError(f"LoRA adapter config not found: {adapter / 'adapter_config.json'}")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for shard in shards:
        (output / shard.name).symlink_to(shard)
    for path in base.iterdir():
        if path.suffix == ".safetensors":
            continue
        dest = output / path.name
        if path.is_dir():
            shutil.copytree(path, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(path, dest)
    for name in ("chat_template.jinja", "tokenizer_config.json", "tokenizer.json"):
        src = adapter / name
        if src.is_file():
            shutil.copy2(src, output / name)
    return output
