"""Dataset context extraction. :class:`DatasetContext` caches profiling +
semantic analysis + auto-rubric so consumers never re-run the multi-second
LLM call after the first build."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from overbae.core.llms import RETRY_DEADLINE_INTERACTIVE, call_llm
from overbae.core.model_resolver import TaskType, model_chain, resolve_model
from overbae.services.eval import profiler, semantic_recommender
from overbae.services.eval.grounding import (
    EvalGroundingContext,
    render_grounding_pack,
    resolve_grounding,
)
from overbae.services.eval.rubric_compiler import compile_rubric

logger = logging.getLogger(__name__)

_RUBRIC_SAMPLE_SIZE = 5


class _AutoRubric(BaseModel):
    rubric_md: str = Field(
        description=(
            "A complete evaluation rubric (2–5 paragraphs of natural language) "
            "for scoring an AI agent's end-to-end responses on this specific task. "
            "Reference the domain and task type. Use {{input}}, {{output}}, and "
            "{{reference}} as template variables where helpful."
        )
    )


_AUTO_RUBRIC_SYSTEM = """\
You are a senior ML evaluation expert. Your job is to write a bespoke evaluation
rubric for an AI agent that works on a specific domain and task. The rubric will
be used by an LLM judge to score responses end-to-end.

Be precise, domain-specific, and actionable. Avoid generic advice like
"the answer should be correct" — describe *what correctness looks like* for
this exact task. Prefer 3–6 independently-verifiable quality dimensions.
"""

_AUTO_RUBRIC_TEMPLATE = """\
## Domain & task
{domain}: {task_description}

{grounding_block}## Sample data ({n} rows)
{samples}

## Instructions
Write a complete natural-language rubric for judging whether an AI agent is
performing well on this task. The rubric must:
1. Be specific to this domain and task (not generic).
2. Cover end-to-end quality: input understanding, reasoning, and output quality.
3. Use {{{{input}}}}, {{{{output}}}}, and {{{{reference}}}} as template variables.
4. Describe 3–6 distinct quality dimensions in plain prose paragraphs.

Return JSON with a single `rubric_md` string field.
"""


def enqueue_refresh(dataset_id) -> None:
    try:
        from overbae.tasks import refresh_dataset_context  # noqa: PLC0415

        refresh_dataset_context.delay(str(dataset_id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not enqueue context refresh for dataset %s: %s", dataset_id, exc)


def extract_and_save(dataset):
    from overbae.models import DatasetContext, Evaluator  # noqa: PLC0415

    logger.info("Extracting dataset context for dataset %s", dataset.id)

    profile = profiler.profile_dataset(dataset)

    project_id = dataset.project_id or (
        dataset.capability.project_id if dataset.capability_id else None
    )
    evaluators = list(Evaluator.objects.library_for_project(project_id))

    try:
        grounding = resolve_grounding(dataset)
    except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
        logger.warning("Grounding resolution failed for dataset %s: %s", dataset.id, exc)
        grounding = None

    semantic = semantic_recommender.analyze_dataset_semantically(
        dataset, evaluators, profile, grounding=grounding
    )

    domain = ""
    task_description = ""
    suggested_rubrics: list[dict[str, Any]] = []
    evaluator_scores: dict[str, Any] = {}
    notes = ""
    auto_rubric_md = ""
    auto_rubric_checklist: list[dict[str, Any]] = []

    if semantic.get("available"):
        domain = semantic.get("domain", "")
        task_description = semantic.get("task_description", "")
        suggested_rubrics = semantic.get("suggested_rubrics", [])
        evaluator_scores = semantic.get("evaluator_scores", {})

        auto_rubric_md = _generate_auto_rubric(
            dataset, domain, task_description, profile, grounding=grounding
        )
        if auto_rubric_md:
            compiled = compile_rubric(auto_rubric_md)
            auto_rubric_checklist = compiled.get("checklist", [])
    else:
        notes = semantic.get("error", "Semantic analysis unavailable")

    task_type, task_type_source = _resolve_task_type(semantic, profile, dataset)

    sample_count = min(profile.get("sampled", 0), profile.get("count", 0))

    ctx, _ = DatasetContext.objects.update_or_create(
        dataset=dataset,
        defaults={
            "project_id": project_id,
            "profile": profile,
            "domain": domain,
            "task_description": task_description,
            "task_type": task_type,
            "task_type_source": task_type_source,
            "notes": notes,
            "suggested_rubrics": suggested_rubrics,
            "evaluator_scores": evaluator_scores,
            "auto_rubric_md": auto_rubric_md,
            "auto_rubric_checklist": auto_rubric_checklist,
            "sample_count": sample_count,
            "extracted_at": datetime.now(UTC),
        },
    )

    logger.info(
        "Dataset context saved for dataset %s (domain=%r, %d checklist items)",
        dataset.id,
        domain,
        len(auto_rubric_checklist),
    )
    return ctx


def _resolve_task_type(
    semantic: dict[str, Any], profile: dict[str, Any], dataset
) -> tuple[str, str]:
    from overbae.services.benchmarks.classify import classify_task_type  # noqa: PLC0415
    from overbae.services.benchmarks.taxonomy import TaskType  # noqa: PLC0415

    candidate = str(semantic.get("task_type") or "").strip().lower()
    if candidate in set(TaskType.values):
        return candidate, "semantic"
    return classify_task_type(profile, _length_stats(dataset)), "heuristic"


def _length_stats(dataset) -> dict[str, Any] | None:
    """Row lengths separate summarization from creative writing; they only refine the
    free-text branch, so a failure degrades the classification instead of the call."""
    from overbae.services.datasets.rows import dataset_stats  # noqa: PLC0415

    try:
        return dataset_stats(dataset)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Dataset stats unavailable for dataset %s: %s", dataset.id, exc)
        return None


def _generate_auto_rubric(
    dataset,
    domain: str,
    task_description: str,
    profile: dict,
    grounding: EvalGroundingContext | None = None,
) -> str:
    samples = _sample_for_rubric(dataset)
    if not samples:
        return ""

    samples_text = "\n".join(
        f"[{i}] Input: {s['input']}\n    Expected: {s['expected_output']}"
        for i, s in enumerate(samples, 1)
    )

    grounding_block = ""
    if grounding is not None:
        pack = render_grounding_pack(grounding)
        if pack:
            grounding_block = f"## Grounding context\n{pack}\n\n"

    prompt = _AUTO_RUBRIC_TEMPLATE.format(
        domain=domain or "General",
        task_description=task_description or "AI agent responses",
        grounding_block=grounding_block,
        n=len(samples),
        samples=samples_text,
    )

    try:
        model = resolve_model(TaskType.CRITERIA_GENERATION)
        raw, _ = call_llm(
            prompt,
            system_prompt=_AUTO_RUBRIC_SYSTEM,
            response_format=_AutoRubric,
            model=model,
            fallback_models=model_chain(TaskType.CRITERIA_GENERATION),
            retry_deadline=RETRY_DEADLINE_INTERACTIVE,
            max_tokens=1200,
        )
        parsed = _AutoRubric.model_validate_json(raw)
        return parsed.rubric_md.strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Auto-rubric generation failed for dataset %s: %s", dataset.id, exc)
        return ""


def _sample_for_rubric(dataset) -> list[dict[str, Any]]:
    from overbae.services.datasets.rows import sample_rows  # noqa: PLC0415

    points = sample_rows(dataset, _RUBRIC_SAMPLE_SIZE)
    out = []
    for p in points:
        inp = p.input
        if isinstance(inp, list):
            inp = next(
                (
                    m.get("content", "")
                    for m in reversed(inp)
                    if isinstance(m, dict) and m.get("role") == "user"
                ),
                str(inp),
            )
        expected = p.expected_output or "(none)"
        out.append(
            {
                "input": str(inp)[:400],
                "expected_output": str(expected)[:400],
            }
        )
    return out
