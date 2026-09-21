from __future__ import annotations

from overbae.services.datasets.alignment import system_prompt


def model_context(run, *, capability=None) -> dict:
    if capability is None and run.dataset_id:
        capability = run.dataset.capability
    if capability is None and run.eval_set_id:
        capability = run.eval_set.capability
    return {
        "execution_mode": "model",
        "system_prompt": system_prompt(capability) if capability else "",
    }


def snapshot_context(run, variants) -> None:
    for variant in variants:
        if variant.mode != variant.Mode.GENERATE or "system_prompt" in (variant.params or {}):
            continue
        params = {**model_context(run), **(variant.params or {})}
        if variant.prompt_id:
            params["system_prompt"] = variant.prompt.system_prompt
        variant.params = params
        variant.save(update_fields=["params"])
