from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from django.core.cache import cache

from overbae.core.llms import effective_max_tokens
from overbae.core.model_registry import TaskType, model_chain
from overbae.models import ModelRef, Prompt
from overbae.services.datasets.alignment import system_prompt
from overbae.services.datasets.rows import RowStoreError, iter_rows
from overbae.services.eval import chatml, decisions, funnel, normalizer, snapshots
from overbae.services.eval.context_suggestions import suggest_models
from overbae.services.eval.evaluators.base import (
    EvalUnit,
    default_variable_mapping,
    resolve_variables,
)
from overbae.services.eval.evaluators.gen_judge import (
    GEN_JUDGE_SYSTEM,
    ChecklistResult,
    is_proportional,
)
from overbae.services.eval.grounding import build_reference_context
from overbae.services.eval.judge_selection import uses_generative_judge
from overbae.services.eval.rubric_compiler import build_checklist_prompt, build_claims_prompt
from overbae.services.llm_context import assess_context, estimate_input_tokens, model_limits

logger = logging.getLogger(__name__)


def check_context(
    *, dataset, cell, variants: list[dict], evaluators: list, capability=None, judge_model: str = ""
) -> list[dict]:
    if cell is None:
        return []
    project_id = dataset.project_id
    capability = capability or dataset.capability
    checks = []
    targets = []
    variant_models = []
    for variant in variants:
        if variant.get("mode", "generate") != "generate":
            continue
        ref = variant.get("model_ref")
        if ref and not isinstance(ref, ModelRef):
            ref = ModelRef.objects.filter(pk=ref, project_id=project_id).first()
        model = ref.model_id if ref else variant.get("model_name", "")
        variant_models.append(model)
        params = dict(variant.get("params") or {})
        if "system_prompt" not in params and variant.get("prompt"):
            prompt = variant["prompt"]
            if not isinstance(prompt, Prompt):
                prompt = Prompt.objects.filter(pk=prompt, capability__project_id=project_id).first()
            if prompt:
                params["system_prompt"] = prompt.system_prompt
        targets.append(
            SimpleNamespace(
                model=model,
                label=variant.get("label") or model,
                role="generation",
                params=params,
                evaluator=None,
                custom=bool(ref and ref.provider == "custom"),
                uses_tools=False,
                limits=model_limits(
                    model, project_id=project_id, custom=bool(ref and ref.provider == "custom")
                ),
                inputs=[],
                indices=[],
                output_tokens=variant.get("output_tokens")
                or ((ref.params or {}).get("max_tokens") if ref else None)
                or effective_max_tokens(model),
            )
        )
    for evaluator in evaluators:
        if not uses_generative_judge(evaluator):
            continue
        try:
            judge = (
                funnel.resolve_judge(evaluator.judge_model, str(project_id))
                if evaluator.judge_model
                else funnel.resolve_default_judge(variant_models)
                or funnel.resolve_judge("", str(project_id))
            )
        except RuntimeError:
            judge = funnel.ResolvedJudge(model_chain(TaskType.JUDGE_SCORING)[0], None, "unknown")
        configured_model = funnel.resolved_model_name(judge)
        if judge_model:
            judge = funnel.resolve_judge(judge_model, str(project_id))
        model = funnel.resolved_model_name(judge)
        targets.append(
            SimpleNamespace(
                model=model,
                label=f"Judge · {evaluator.name}",
                role="judge",
                params={},
                evaluator=evaluator,
                configured_model=configured_model,
                custom=bool(judge.model_spec and judge.model_spec.provider == "custom"),
                uses_tools=False,
                limits=model_limits(
                    model,
                    project_id=project_id,
                    custom=bool(judge.model_spec and judge.model_spec.provider == "custom"),
                ),
                inputs=[],
                indices=[],
                output_tokens=(
                    judge.model_spec.params.get("max_tokens") if judge.model_spec else None
                )
                or effective_max_tokens(model),
            )
        )
        if decisions.policy_for(evaluator).backend == "jev":
            targets[-1].label += " (generative fallback)"
    reference_context = build_reference_context(dataset) if evaluators else {}
    prompt_proxy = max(
        [system_prompt(capability) if capability else ""]
        + [
            target.params.get("system_prompt") or ""
            for target in targets
            if target.role == "generation"
        ],
        key=len,
    )
    try:
        for row in iter_rows(cell):
            for target in targets:
                if target.role == "generation":
                    target.uses_tools = target.uses_tools or bool(row.extra.get("tools"))
                    inputs = {
                        "input": row.input,
                        "tools": row.extra.get("tools", []),
                        "system_prompt": target.params.get(
                            "system_prompt", system_prompt(capability) if capability else ""
                        ),
                    }
                else:
                    # Generated answers do not exist yet; the reference is only an output-size proxy.
                    proxy = row.expected_output
                    output = (
                        proxy if isinstance(proxy, str) else json.dumps(proxy, ensure_ascii=False)
                    )
                    messages = chatml.parse_messages(row.input) or [
                        {
                            "role": "user",
                            "content": row.input
                            if isinstance(row.input, str)
                            else json.dumps(row.input, ensure_ascii=False),
                        }
                    ]
                    if prompt_proxy and not any(m.get("role") == "system" for m in messages):
                        messages = [{"role": "system", "content": prompt_proxy}, *messages]
                    trajectory = {
                        "messages": [*messages, {"role": "assistant", "content": output}],
                        "final_output": output,
                        "tool_definitions": row.extra.get("tools", []),
                        "metadata": {"row_extra": row.extra},
                    }
                    unit = EvalUnit(
                        trajectory=trajectory,
                        structured=normalizer.structure_trajectory(trajectory),
                        expected=proxy,
                        reference_context=reference_context,
                    )
                    variables = resolve_variables(
                        unit, target.evaluator.variable_mapping or default_variable_mapping()
                    )
                    build_prompt = (
                        build_claims_prompt
                        if is_proportional(target.evaluator)
                        else build_checklist_prompt
                    )
                    inputs = [
                        GEN_JUDGE_SYSTEM,
                        build_prompt(target.evaluator, variables),
                        ChecklistResult.model_json_schema(),
                        row.extra.get("tools", []),
                    ]
                target.inputs.append(estimate_input_tokens(inputs))
                target.indices.append(row.index)
    except (RowStoreError, OSError, ValueError):
        logger.warning(
            "Evaluation context estimate unavailable for cell %s", cell.pk, exc_info=True
        )
        for target in targets:
            target.inputs.clear()
            target.indices.clear()
    for target in targets:
        check = assess_context(
            model=target.model,
            inputs=target.inputs,
            output_tokens=target.output_tokens,
            limits=target.limits,
            role=target.role,
            label=target.label,
            row_indices=target.indices,
        )
        check.update(
            suggest_models(
                check,
                inputs=target.inputs,
                uses_tools=target.uses_tools,
                custom=target.custom,
                variant_models=variant_models,
            )
        )
        check["total_input_tokens"] = sum(target.inputs)
        check["configured_model"] = getattr(target, "configured_model", target.model)
        checks.append(check)
    return checks


def run_context_checks(run) -> list[dict]:
    if not run.dataset_id or not run.cell_id:
        return []
    key = f"eval_context:{run.pk}:{run.updated_at.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    variants = [
        {
            "model_name": v.model_name,
            "model_ref": v.model_ref,
            "mode": v.mode,
            "label": v.label,
            "params": v.params,
            "prompt": v.prompt,
        }
        for v in run.variants.select_related("model_ref", "prompt")
    ]
    evaluators = [
        snapshots.snapshot_to_obj(row.snapshot) for row in run.run_evaluators.filter(enabled=True)
    ]
    checks = check_context(
        dataset=run.dataset, cell=run.cell, variants=variants, evaluators=evaluators
    )
    cache.set(key, checks, 300)
    return checks
