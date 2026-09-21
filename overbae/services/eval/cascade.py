"""Long-trajectory judging cascade, cheapest-first: deterministic gates, one
batched per-step call (windowed on overflow), then optional holistic calls."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from overbae.services.eval import chatml, decisions
from overbae.services.eval import funnel as judging
from overbae.services.eval.evaluators.base import (
    EvalUnit,
    JudgeResult,
    ScoreDraft,
    normalize_numeric,
)

logger = logging.getLogger(__name__)

# ``max_steps_judged: 0`` is unlimited under ``thorough`` but no per-step judging
# under ``fast`` (``final_only`` never reaches the step loop).
PRESETS: dict[str, dict[str, Any]] = {
    "fast": {
        "long_trace_strategy": "final_only",
        "aggregation": "mean",
        "judge_salient_only": True,
        "max_steps_judged": 0,
    },
    "balanced": {
        "long_trace_strategy": "auto",
        "aggregation": "weighted",
        "judge_salient_only": True,
        "max_steps_judged": 12,
        "step_window": 12,
    },
    "thorough": {
        "long_trace_strategy": "agentic",
        "aggregation": "weighted",
        "judge_salient_only": False,
        "max_steps_judged": 0,
        "step_window": 10,
    },
}

DEFAULT_PRESET = "balanced"


def resolve_config(config: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(config or {})
    preset = PRESETS.get(config.get("preset", DEFAULT_PRESET), PRESETS[DEFAULT_PRESET])
    return {**preset, **config}


# Judge context budget (tokens); ``auto`` escalates above the fraction.
_DEFAULT_BUDGET_TOKENS = 60_000
_ESCALATE_FRACTION = 0.6
# chars
_STEP_ARG_CHARS = 1200
_STEP_RESULT_CHARS = 1200
_DEFAULT_STEP_WINDOW = 12

# Adapted from agentevals' TRAJECTORY_ACCURACY_PROMPT.
_BATCH_SYSTEM = (
    "You are an expert data labeler grading the steps of an AI agent's trajectory.\n"
    "An accurate trajectory:\n"
    "- Makes logical sense between steps\n"
    "- Shows clear progression toward the goal\n"
    "- Is relatively efficient (does not need to be perfectly efficient)\n"
    "For EACH listed step decide if it was justified and correct given the goal and context. "
    "Judge substance, not verbosity. Return one item per step id, plus an overall score."
)

_HOLISTIC_PROMPT_NO_REF = """\
You are an expert data labeler grading an AI agent's trajectory.

<Rubric>
An accurate trajectory:
- Makes logical sense between steps
- Shows clear progression toward the goal
- Is relatively efficient, though it does not need to be perfectly efficient
</Rubric>

<Instructions>
First understand the goal by examining the user message and the final answer.
Then grade the trajectory as it relates to achieving that goal.
</Instructions>

<Trajectory>
{trajectory}
</Trajectory>

Return JSON {{"score": <0..1>, "reasoning": "one concise sentence"}}.
"""

_HOLISTIC_PROMPT_WITH_REF = """\
You are an expert data labeler grading an AI agent's trajectory.

<Rubric>
An accurate trajectory:
- Makes logical sense between steps
- Shows clear progression toward the goal
- Is relatively efficient, though it does not need to be perfectly efficient
- Is semantically equivalent to the provided reference trajectory
</Rubric>

<Reference trajectory>
{reference}
</Reference trajectory>

<Actual trajectory>
{trajectory}
</Actual trajectory>

Return JSON {{"score": <0..1>, "reasoning": "one concise sentence"}}.
"""


@dataclass
class CascadeOutcome:
    drafts: list[ScoreDraft]
    context_coverage: float = 1.0
    structured_updates: dict[str, Any] = field(default_factory=dict)


def maybe_route(unit: EvalUnit, evaluator, ctx: dict[str, Any]) -> list[ScoreDraft] | None:
    # Outcome is one holistic judgment; the cascade averages per-step scores.
    role = ((getattr(evaluator, "config", None) or {}).get("behaviour") or {}).get("role")
    if role == "outcome":
        return None

    config = resolve_config(evaluator.config)
    strategy = config.get("long_trace_strategy", "auto")
    if strategy == "final_only":
        return None

    structured = unit.structured or {}
    approx = structured.get("approx_tokens")
    if approx is None:
        approx = chatml.approx_tokens(
            chatml.messages_text((unit.trajectory or {}).get("messages", []))
        )
    budget = int(config.get("budget_tokens", _DEFAULT_BUDGET_TOKENS))

    needs = strategy in ("per_step", "per_step+mapreduce", "agentic") or (
        strategy == "auto" and approx > budget * _ESCALATE_FRACTION
    )
    if not needs:
        return None

    outcome = run_cascade(
        unit, evaluator, ctx, strategy=strategy, budget=budget, approx_tokens=approx
    )
    # Stashed for the task layer to persist onto the sample.
    ctx.setdefault("_cascade", {})["context_coverage"] = outcome.context_coverage
    return outcome.drafts


def run_cascade(
    unit: EvalUnit,
    evaluator,
    ctx: dict[str, Any],
    *,
    strategy: str,
    budget: int,
    approx_tokens: int,
) -> CascadeOutcome:
    started = time.monotonic()
    project_id = ctx.get("project_id")
    config = resolve_config(evaluator.config)
    structured = unit.structured or {}
    nodes: list[dict[str, Any]] = (structured.get("tool_graph") or {}).get("nodes", [])

    gate_cap, gate_reason = _apply_gates(unit, evaluator)
    if gate_cap is not None:
        value, passed = normalize_numeric(gate_cap, evaluator)
        draft = ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=value,
            passed=passed,
            reasoning=gate_reason,
            failure_role="root_cause",
            scope=evaluator.scope or "trajectory",
            cost=0.0,
        )
        return CascadeOutcome(drafts=[draft], context_coverage=1.0)

    judge = judging.resolve_judge(evaluator.judge_model, project_id)
    total_cost = 0.0

    steps_to_judge = _select_steps(nodes, structured, config)
    if not steps_to_judge:
        # Null, not 0.0 — downstream rollups must not read "nothing to judge"
        # as a real failure.
        draft = ScoreDraft(
            name=evaluator.name,
            data_type="numeric",
            value=None,
            reasoning=(
                "No tool-call steps found in this trajectory. "
                "Trajectory judges require at least one capability step."
            ),
            scope=evaluator.scope or "trajectory",
            cost=0.0,
        )
        return CascadeOutcome(drafts=[draft], context_coverage=1.0)

    step_scores, batch_cost = _batch_judge(
        unit, evaluator, steps_to_judge, judge, project_id, config
    )
    total_cost += batch_cost
    batch_decisions = [step.pop("decision") for step in step_scores if "decision" in step]
    costs = [item.get("total_cost") for item in batch_decisions]

    _attribute_failures(step_scores, evaluator)

    aggregation = config.get("aggregation", "weighted")
    agg_value = _aggregate(step_scores, aggregation)

    holistic_reason = ""
    holistic_ran = False
    if strategy in ("per_step+mapreduce", "agentic") and step_scores:
        summary = _summary_from_steps(unit, step_scores)
        holistic_value, holistic_reason, h_cost = _map_reduce_verdict(
            unit, evaluator, summary, judge, project_id
        )
        total_cost += h_cost or 0.0
        costs.append(h_cost)
        if holistic_value is not None:
            agg_value = (
                (agg_value + holistic_value) / 2 if agg_value is not None else holistic_value
            )
            holistic_ran = True

    if strategy == "agentic" and approx_tokens > budget:
        agentic_value, agentic_reason, a_cost = _agentic_verdict(unit, evaluator, judge, project_id)
        total_cost += a_cost or 0.0
        costs.append(a_cost)
        if agentic_value is not None:
            agg_value = (agg_value + agentic_value) / 2 if agg_value is not None else agentic_value
            holistic_reason = (holistic_reason + " " + agentic_reason).strip()
            holistic_ran = True

    value, passed = (
        normalize_numeric(agg_value, evaluator) if agg_value is not None else (None, None)
    )
    coverage = _coverage(
        sum(s["score"] is not None for s in step_scores),
        len(nodes),
        approx_tokens,
        budget,
        holistic_ran,
    )

    root_causes = [s for s in step_scores if s.get("failure_role") == "root_cause"]
    summary_reason = holistic_reason or _reason_from_steps(step_scores, root_causes)
    if step_scores and batch_decisions:
        sources = {item.get("source") for item in batch_decisions}
        step_scores[0]["_decision"] = {
            "source": next(iter(sources)) if len(sources) == 1 else "mixed",
            "batches": batch_decisions,
            "total_cost": None if any(cost is None for cost in costs) else sum(costs),
        }

    draft = ScoreDraft(
        name=evaluator.name,
        data_type="numeric",
        value=value,
        passed=passed,
        reasoning=summary_reason,
        sub_scores=step_scores,
        failure_role="root_cause" if root_causes else "none",
        scope=evaluator.scope or "trajectory",
        cost=total_cost,
        latency_ms=(time.monotonic() - started) * 1000,
    )
    return CascadeOutcome(drafts=[draft], context_coverage=coverage)


def _apply_gates(unit: EvalUnit, evaluator) -> tuple[float | None, str]:
    from overbae.services.eval.evaluators import deterministic

    gates = (evaluator.config or {}).get("gates", [])
    for gate in gates:
        fake = _FakeEvaluator(
            config=gate,
            score_min=0.0,
            score_max=1.0,
            scope=evaluator.scope,
            pass_threshold=gate.get("pass_threshold", 1.0),
        )
        drafts = deterministic.evaluate(unit, fake, {})
        d = drafts[0] if drafts else None
        if d and d.value is not None and d.passed is False:
            return 0.0, f"Gate '{gate.get('check')}' failed: {d.reasoning}"
    return None, ""


@dataclass
class _FakeEvaluator:
    config: dict
    score_min: float
    score_max: float
    scope: str
    pass_threshold: float | None
    name: str = "gate"


def _select_steps(
    nodes: list[dict[str, Any]], structured: dict[str, Any], config: dict[str, Any]
) -> list[dict[str, Any]]:
    selected = nodes
    if config.get("judge_salient_only"):
        salient_ids = {
            s.get("ref")
            for s in structured.get("salient_steps", [])
            if isinstance(s.get("ref"), str) and s.get("ref", "").startswith("step_")
        }
        if salient_ids:
            selected = [n for n in nodes if n.get("id") in salient_ids]
        if not selected:
            selected = nodes

    max_steps = int(config.get("max_steps_judged", 0) or 0)
    if max_steps and len(selected) > max_steps:
        head = (max_steps + 1) // 2
        tail = max_steps // 2
        selected = selected[:head] + (selected[-tail:] if tail else [])
    return selected


def _deterministic_seed(unit: EvalUnit, evaluator_name: str) -> int:
    """Stable across retries, yet decorrelates position bias from content."""
    key = f"{unit.sample_id}:{evaluator_name}"
    return int(hashlib.md5(key.encode()).hexdigest()[:8], 16)  # noqa: S324


def _batch_judge(
    unit: EvalUnit,
    evaluator,
    steps: list[dict[str, Any]],
    judge,
    project_id,
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], float]:
    """Steps shuffle within a window against position bias; results come back
    in chronological order."""
    if not steps:
        return [], 0.0

    window = int(config.get("step_window", _DEFAULT_STEP_WINDOW) or _DEFAULT_STEP_WINDOW)
    windows = [steps[i : i + window] for i in range(0, len(steps), max(1, window))]

    rng = random.Random(_deterministic_seed(unit, getattr(evaluator, "name", "")))  # noqa: S311

    step_scores: list[dict[str, Any]] = []
    total_cost = 0.0
    goal = _goal_text(unit)
    for chunk in windows:
        shuffled = list(chunk)
        rng.shuffle(shuffled)

        prompt = _batch_prompt(evaluator, goal, shuffled)

        def fallback(prompt=prompt):
            return judging.invoke_judge(
                prompt,
                response_format=JudgeResult,
                judge=judge,
                project_id=project_id,
                system_prompt=_BATCH_SYSTEM,
            )

        def convert(answers):
            items = [
                {
                    "id": key,
                    "score": float(answer.choice)
                    if answer.choice not in {None, "insufficient"}
                    else None,
                    "reasoning": getattr(answer, "reasoning", "")
                    or f"Step {key}: {answer.choice}.",
                }
                for key, answer in answers.items()
            ]
            scores = [item["score"] for item in items if item["score"] is not None]
            return JudgeResult(
                items=items,
                score=sum(scores) / len(scores) if scores else 0.0,
                abstained=not scores,
                reasoning="Step quality measured against the supplied goal and rubric.",
            )

        outcome = decisions.invoke(
            {
                "goal": goal,
                "conversation": (unit.trajectory or {}).get("messages", []),
                "steps": shuffled,
            },
            {
                str(node["id"]): decisions.decision_question(
                    f"{_BATCH_SYSTEM}\nRubric: {evaluator.rubric_md}\n"
                    f"Rate only the step with id {node['id']}, using the other steps as context.",
                    {
                        "0": "The step was unjustified or incorrect.",
                        "0.5": "The step was partly justified and partly correct.",
                        "1": "The step was justified and correct.",
                        "insufficient": "The available evidence cannot establish step quality.",
                    },
                )
                for node in shuffled
            },
            convert=convert,
            fallback=fallback,
            project_id=project_id,
            workload="trace_step_batch",
            contract="step_anchored@1",
            policy=decisions.policy_for(evaluator),
            uncertain_choices=frozenset({"insufficient"}),
            independent=True,
            judge=judge,
        )
        total_cost += float(outcome.stats.get("response_cost", 0) or 0)
        items_by_id: dict[str, Any] = {}
        if outcome.parsed is not None:
            for it in outcome.parsed.items:
                items_by_id[it.id] = it

        for index, node in enumerate(chunk):
            it = items_by_id.get(node["id"])
            score = float(it.score) if (it and it.score is not None) else None
            reasoning = (it.reasoning if it else "") or ""
            step_scores.append(
                {
                    "id": node["id"],
                    "tool": node.get("tool"),
                    "score": score,
                    "reasoning": reasoning,
                    "depends_on": node.get("depends_on", []),
                    "error": node.get("error", ""),
                    **(
                        {
                            "decision": outcome.stats.get("decision")
                            or {
                                "source": "generative",
                                "total_cost": outcome.stats.get("response_cost"),
                                "usage": outcome.stats,
                            }
                        }
                        if index == 0
                        else {}
                    ),
                }
            )
    return step_scores, total_cost


def _goal_text(unit: EvalUnit) -> str:
    messages = (unit.trajectory or {}).get("messages", [])
    first_user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
    return first_user[:500]


def _batch_prompt(evaluator, goal: str, steps: list[dict[str, Any]]) -> str:
    custom_rubric = evaluator.rubric_md.strip() if evaluator.rubric_md else ""
    rubric_block = (
        f"<Additional rubric>\n{custom_rubric}\n</Additional rubric>\n\n" if custom_rubric else ""
    )
    lines = [
        "<Task>",
        "Grade each step of an AI agent's trajectory. An accurate trajectory makes logical",
        "sense, shows clear progression toward the goal, and is relatively efficient.",
        "</Task>",
        "",
        rubric_block.rstrip() if rubric_block else "",
        f"<Goal>\n{goal}\n</Goal>" if goal else "",
        "",
        "<Steps to grade>",
    ]
    for node in steps:
        args = json.dumps(node.get("arguments"), default=str)[:_STEP_ARG_CHARS]
        result = json.dumps(node.get("result"), default=str)[:_STEP_RESULT_CHARS]
        err = node.get("error") or "none"
        lines.append(
            f"[{node['id']}] tool={node.get('tool')} args={args} result={result} error={err}"
        )
    lines.append("</Steps to grade>")
    lines.append("")
    lines.append(
        'Return JSON {"items":[{"id":"step_x","score":<0..1>,"reasoning":"..."}], '
        '"score":<overall 0..1>, "reasoning":"..."}. Include one item per step id above.'
    )
    return "\n".join(line for line in lines if line is not None)


def _clip_clean(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if sentence > limit // 2:
        return cut[: sentence + 1]
    word = cut.rfind(" ")
    if word > 0:
        cut = cut[:word]
    return cut.rstrip(",;:") + "…"


def _summary_from_steps(unit: EvalUnit, step_scores: list[dict[str, Any]]) -> str:
    lines = [f"User goal: {_goal_text(unit)}", "Step grades:"]
    for s in step_scores:
        reasoning = _clip_clean(s.get("reasoning", ""), 140)
        score = f"{s['score']:.2f}" if s["score"] is not None else "unknown"
        lines.append(f"- {s['id']} ({s.get('tool')}): {score} {reasoning}")
    summary = "\n".join(lines)
    return summary[-3500:]


def _aggregate(step_scores: list[dict[str, Any]], policy: str) -> float | None:
    step_scores = [step for step in step_scores if step["score"] is not None]
    if not step_scores:
        return None
    vals = [s["score"] for s in step_scores]
    if policy == "min":
        return min(vals)
    if policy == "product":
        out = 1.0
        for v in vals:
            out *= v
        return out
    if policy == "progress_rate":
        return sum(1 for v in vals if v >= 0.5) / len(vals)
    if policy == "weighted":
        total_w = 0.0
        acc = 0.0
        for i, s in enumerate(step_scores):
            w = 1.0 + (0.5 if s.get("tool") and i == len(step_scores) - 1 else 0.0)
            acc += s["score"] * w
            total_w += w
        return acc / total_w if total_w else 0.0
    return sum(vals) / len(vals)


def _attribute_failures(step_scores: list[dict[str, Any]], evaluator) -> None:
    threshold = float((evaluator.config or {}).get("step_fail_threshold", 0.5))
    by_id = {s["id"]: s for s in step_scores}
    for s in step_scores:
        s["failure_role"] = "none"
    for s in step_scores:
        if s["score"] is None or s["score"] >= threshold:
            continue
        parents = [by_id.get(pid) for pid in s.get("depends_on", []) if by_id.get(pid)]
        parent_failed = any(
            p and p["score"] is not None and p["score"] < threshold for p in parents
        )
        s["failure_role"] = "propagated" if parent_failed else "root_cause"


def _reason_from_steps(step_scores: list[dict[str, Any]], root_causes: list[dict[str, Any]]) -> str:
    step_scores = [step for step in step_scores if step["score"] is not None]
    if root_causes:
        rc = root_causes[0]
        reasoning = _clip_clean(rc.get("reasoning", ""), 300)
        return f"Root-cause failure at {rc['id']} ({rc.get('tool')}): {reasoning}"
    if step_scores:
        worst = min(step_scores, key=lambda s: s["score"])
        reasoning = _clip_clean(worst.get("reasoning", ""), 300)
        return f"Weakest step {worst['id']} scored {worst['score']:.2f}: {reasoning}"
    return "No resolved step scores."


def _map_reduce_verdict(
    unit, evaluator, summary, judge, project_id
) -> tuple[float | None, str, float | None]:
    final = (unit.trajectory or {}).get("final_output", "")
    trajectory_text = f"{summary}\n\nFinal answer:\n{final[:2000]}"

    reference_traj = (evaluator.config or {}).get("reference_trajectory_text")
    if reference_traj:
        prompt = _HOLISTIC_PROMPT_WITH_REF.format(
            reference=str(reference_traj)[:3000],
            trajectory=trajectory_text[-4000:],
        )
    else:
        prompt = _HOLISTIC_PROMPT_NO_REF.format(trajectory=trajectory_text[-4000:])

    outcome = judging.invoke_judge(
        prompt, response_format=JudgeResult, judge=judge, project_id=project_id
    )
    cost = outcome.stats.get("response_cost")
    if outcome.parsed is None:
        return None, "", cost
    return float(outcome.parsed.score), outcome.parsed.reasoning, cost


def _agentic_verdict(unit, evaluator, judge, project_id) -> tuple[float | None, str, float | None]:
    structured = unit.structured or {}
    index = {
        "num_turns": structured.get("num_turns"),
        "num_tool_calls": structured.get("num_tool_calls"),
        "salient_steps": structured.get("salient_steps", []),
        "tools": [n.get("tool") for n in (structured.get("tool_graph") or {}).get("nodes", [])],
    }
    final = (unit.trajectory or {}).get("final_output", "")
    index_text = (
        f"Num turns: {index.get('num_turns')}  "
        f"Tool calls: {index.get('num_tool_calls')}\n"
        f"Tools used: {', '.join(index.get('tools', []))}\n"
        f"Salient steps: {json.dumps(index.get('salient_steps', []), default=str)[:2000]}\n"
        f"Final answer: {final[:2000]}"
    )
    prompt = _HOLISTIC_PROMPT_NO_REF.format(trajectory=index_text)
    outcome = judging.invoke_judge(
        prompt, response_format=JudgeResult, judge=judge, project_id=project_id
    )
    cost = outcome.stats.get("response_cost")
    if outcome.parsed is None:
        return None, "", cost
    return float(outcome.parsed.score), outcome.parsed.reasoning, cost


def _coverage(
    judged: int, total: int, approx_tokens: int, budget: int, holistic_ran: bool
) -> float:
    if total <= 0:
        return (
            1.0 if approx_tokens <= budget else round(min(1.0, budget / max(approx_tokens, 1)), 3)
        )
    frac = min(1.0, judged / total)
    if holistic_ran:
        frac = max(frac, 0.9)
    return round(frac, 3)
