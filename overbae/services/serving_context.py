from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from modal_shared.context_budget import CONTEXT_HEADROOM, DEFAULT_OUTPUT_TOKENS
from overbae.core.errors import InputValidationError
from overbae.modal.gpu_selector import select_gpu
from overbae.modal.model_registry import get_model_config_any_backend
from overbae.services.datasets.alignment import system_prompt
from overbae.services.datasets.rows import iter_rows, verify
from overbae.services.datasets.text import approx_tokens


@dataclass(frozen=True)
class InferenceBudget:
    input_tokens: int = 0
    output_tokens: int = DEFAULT_OUTPUT_TOKENS
    rows: int = 0

    @property
    def required_context(self) -> int:
        return self.input_tokens + self.output_tokens + CONTEXT_HEADROOM


def evaluation_budget(cell=None, *, capability=None) -> InferenceBudget:
    prompt_tokens = approx_tokens(system_prompt(capability)) if capability else 0
    longest_input = longest_output = count = 0
    if cell is not None:
        verify(cell)
        for row in iter_rows(cell):
            count += 1
            longest_input = max(
                longest_input,
                approx_tokens(row.input) + approx_tokens((row.extra or {}).get("tools") or ""),
            )
            if row.expected_output is not None:
                longest_output = max(longest_output, approx_tokens(row.expected_output))
    # Planning estimates include tokenizer/template variation; runtime serving uses
    # the actual tokenizer and rejects a request that cannot reserve its output.
    return InferenceBudget(
        input_tokens=math.ceil((longest_input + prompt_tokens) * 1.25),
        output_tokens=max(
            DEFAULT_OUTPUT_TOKENS, math.ceil(longest_output * 1.5) + CONTEXT_HEADROOM
        ),
        rows=count,
    )


def serving_plan(base_model: str, budget: InferenceBudget) -> dict[str, Any]:
    config = get_model_config_any_backend(base_model) or {}
    limits = [
        int(value)
        for value in (
            config.get("context_length"),
            (config.get("finetuning") or {}).get("context_length"),
        )
        if value
    ]
    default = int((config.get("inference") or {}).get("max_model_len") or 16384)
    # The serving stack does not configure RoPE scaling: do not use a larger
    # advertised, extended window than the native context in the training catalog.
    limit = min(limits) if limits else default
    required = budget.required_context
    if required > limit:
        raise InputValidationError(
            f"Estimated evaluation input and reserved output need {required:,} tokens, "
            f"but {base_model} supports {limit:,} in this serving stack. "
            "Choose a longer-context model or revise the evaluation workload."
        )
    context = min(limit, max(default, 1 << (required - 1).bit_length()))
    select_gpu(config if config.get("moe") else {**config, "fp8_supported": False}, context)
    return {
        **asdict(budget),
        "required_context": required,
        "max_model_len": context,
        "model_context_limit": limit,
        "estimated": True,
    }


def job_serving_plan(job) -> dict[str, Any]:
    return serving_plan(job.base_model, evaluation_budget(job.eval_cell, capability=job.capability))
