"""Warrant-checked dispatch: a member runs only when its grain matches the
unit's position and its warrant is a subset of the envelope's labels; anything
else abstains for free with named unmet clauses. A failed member never voids
the rest.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field, create_model

from overbae.models import EvalSetMember, Span, Verdict
from overbae.models.traces import is_tool_operation
from overbae.services.eval import funnel
from overbae.services.eval.evaluators import base as eval_base
from overbae.services.eval.funnel import JudgeExecutor, JudgeTask
from overbae.services.eval.rubric_compiler import numeric_anchor_scale
from overbae.services.eval.specs import DETAIL_TIERS, EvaluatorSpec, Warrant

logger = logging.getLogger(__name__)

GROUNDING_VERDICT_NAME = "grounding"
_GROUNDING_RUBRIC_VERSION = "grounding@v2"

_OUTCOME_MAP = {
    eval_base.OUTCOME_SCORED: Verdict.Outcome.SCORED,
    eval_base.OUTCOME_ABSTAINED: Verdict.Outcome.ABSTAINED,
    eval_base.OUTCOME_NOT_APPLICABLE: Verdict.Outcome.NOT_APPLICABLE,
    eval_base.OUTCOME_ERROR: Verdict.Outcome.ERROR,
}

_PANEL_SIZE = 3


@dataclass
class EnvelopeLabels:
    """Dispatch is a set comparison against these, never a heuristic over payloads."""

    detail: str = "full"
    provenance: set[str] = field(default_factory=set)
    evidence: set[str] = field(default_factory=set)


def _walk_tree(tree: list[dict[str, Any]] | None):
    stack = list(tree or [])
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.get("children") or [])


def _string_leaves(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _string_leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _string_leaves(v)


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


_ECHO_MIN_CHARS = 24
_ECHO_MARKER = "[agent-authored echo removed]"
_SHINGLE_TOKENS = 8
_SHINGLE_STRIDE = 4
_ECHO_RATIO = 0.5


def _is_echo(leaf_norm: str, input_haystack: str) -> bool:
    """Shingles so truncated or wrapper-prefixed copies still register as echoes."""
    if leaf_norm in input_haystack:
        return True
    tokens = leaf_norm.split()
    if len(tokens) < _SHINGLE_TOKENS:
        return False
    shingles = [
        " ".join(tokens[i : i + _SHINGLE_TOKENS])
        for i in range(0, len(tokens) - _SHINGLE_TOKENS + 1, _SHINGLE_STRIDE)
    ]
    hits = sum(s in input_haystack for s in shingles)
    return hits / len(shingles) >= _ECHO_RATIO


def _scrub_echoes(value: Any, input_haystack: str) -> tuple[Any, bool]:
    """A tool output that reproduces its own input (``done``-style terminal
    tools) is self-report, never environment evidence."""
    if isinstance(value, str):
        normalized = _norm(value)
        if len(normalized) >= _ECHO_MIN_CHARS and _is_echo(normalized, input_haystack):
            return _ECHO_MARKER, False
        return value, len(normalized) >= _ECHO_MIN_CHARS
    if isinstance(value, dict):
        out, independent = {}, False
        for key, item in value.items():
            out[key], leaf_independent = _scrub_echoes(item, input_haystack)
            independent = independent or leaf_independent
        return out, independent
    if isinstance(value, list):
        out, independent = [], False
        for item in value:
            scrubbed, leaf_independent = _scrub_echoes(item, input_haystack)
            out.append(scrubbed)
            independent = independent or leaf_independent
        return out, independent
    return value, False


def environment_corpus(unit_spans: list[Span], span_tree: list[dict[str, Any]] | None) -> list[str]:
    """Environment-provenance payloads only; agent self-report never enters."""
    corpus: list[str] = []
    for node in _walk_tree(span_tree):
        if not is_tool_operation(node.get("type")):
            continue
        if any(is_tool_operation(c.get("type")) for c in node.get("children") or []):
            continue  # dispatcher parent; the leaf carries the payload
        out = node.get("outputs")
        if out in (None, "", {}, []):
            continue
        inputs = node.get("inputs")
        haystack = _norm(
            inputs
            if isinstance(inputs, str)
            else json.dumps(inputs, default=str, ensure_ascii=False)
        )
        scrubbed, independent = _scrub_echoes(out, haystack)
        if not independent and any(
            len(_norm(leaf)) >= _ECHO_MIN_CHARS for leaf in _string_leaves(out)
        ):
            continue  # pure echo
        text = (
            scrubbed
            if isinstance(scrubbed, str)
            else json.dumps(scrubbed, default=str, ensure_ascii=False)
        )
        corpus.append(f"[tool:{node.get('tool') or node.get('name')}] {text[:40_000]}")
    for span in unit_spans:
        attrs = span.attributes or {}
        if str(attrs.get("overmind.provenance") or "") == "environment":
            payload = attrs.get("overmind.output.data") or attrs.get("overmind.output_data")
            if payload not in (None, ""):
                text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
                corpus.append(f"[span:{span.name}] {text[:40_000]}")
        for event in span.events or []:
            if "observation" in str(event.get("name") or "").lower():
                text = json.dumps(event.get("attributes") or {}, default=str)
                corpus.append(f"[observation] {text[:40_000]}")
    return corpus


def unit_labels(
    trajectory: dict[str, Any],
    structured: dict[str, Any],
    unit_spans: list[Span],
    trace_corpus: list[str] | None = None,
) -> EnvelopeLabels:
    provenance: set[str] = set()
    evidence: set[str] = set()

    messages = trajectory.get("messages") or []
    if any(m.get("role") == "user" for m in messages):
        provenance.add("user")
    if any(m.get("role") == "assistant" for m in messages):
        provenance.add("agent")

    for span in unit_spans:
        attrs = span.attributes or {}
        declared = str(attrs.get("overmind.provenance") or "")
        if declared in ("user", "agent", "environment", "harness"):
            provenance.add(declared)
        span_type = str(span.span_type or "")
        if span_type in ("llm_call", "agent"):
            provenance.add("agent")
        elif span_type == "entry_point":
            provenance.add("harness")

    nodes = (structured.get("tool_graph") or {}).get("nodes") or []
    if nodes:
        evidence.add("tool_io")
    # A terminal unit's own subtree (a done-style echo) may carry no evidence;
    # the run's observations count for it.
    corpus = environment_corpus(unit_spans, trajectory.get("span_tree")) or trace_corpus
    if corpus:
        provenance.add("environment")
        evidence.add("environment_evidence")
    if str(trajectory.get("final_output") or "").strip():
        evidence.add("final_output")
    runtime = trajectory.get("runtime") or {}
    if isinstance(runtime.get("intent"), dict):
        evidence.add("intent")
    if runtime.get("expectations"):
        evidence.add("reference")

    detail = "compacted" if (trajectory.get("metadata") or {}).get("truncated") else "full"
    return EnvelopeLabels(detail=detail, provenance=provenance, evidence=evidence)


def warrant_unmet(warrant: Warrant, labels: EnvelopeLabels) -> list[str]:
    unmet = [f"provenance:{cls}" for cls in warrant.provenance if cls not in labels.provenance]
    if DETAIL_TIERS.index(labels.detail) < DETAIL_TIERS.index(warrant.detail):
        unmet.append(f"detail:{warrant.detail}")
    unmet.extend(f"evidence:{req}" for req in warrant.requires if req not in labels.evidence)
    return unmet


def grain_matches(
    grain: str,
    *,
    is_terminal: bool,
    behaviour_bound: bool = False,
    turn_slice: bool = False,
    capability_terminal: bool | None = None,
) -> bool:
    """A trajectory claim graded on a mid-run turn slice reads earlier turns as
    this turn's failure, so it binds once at the capability's last unit;
    behaviour-bound members are already scoped by their anchors."""
    if grain in ("terminal", "session"):
        return is_terminal
    if grain == "trajectory" and turn_slice and not behaviour_bound:
        return is_terminal if capability_terminal is None else capability_terminal
    return True


@dataclass
class DispatchPlan:
    runnable: list[EvalSetMember] = field(default_factory=list)
    abstained: dict[str, list[str]] = field(default_factory=dict)  # name -> unmet clauses
    skipped: list[str] = field(default_factory=list)  # grain mismatch — not this unit's claim
    # Delivery-grade claims on an interrupted unit — the run died before
    # delivering, so there is no terminal deliverable to grade.
    interrupted: list[str] = field(default_factory=list)


# With none of these and no ask, an outcome judge has nothing but a guess.
_OUTCOME_EVIDENCE = frozenset({"tool_io", "final_output", "environment_evidence"})


def plan_members(
    members: list[EvalSetMember],
    specs: dict[str, EvaluatorSpec],
    labels: EnvelopeLabels,
    *,
    is_terminal: bool,
    turn_slice: bool = False,
    capability_terminal: bool | None = None,
    has_ask: bool = True,
    interrupted: bool = False,
) -> DispatchPlan:
    plan = DispatchPlan()
    for member in members:
        name = member.evaluator.name
        spec = specs[name]
        config = member.evaluator.config or {}
        behaviour_bound = bool(config.get("behaviour"))
        # An interrupted run's completed evidence is still judgeable — but its
        # delivery never happened, so terminal/session claims (task success,
        # delivery quality, behaviour outcome gates) have no subject. They
        # skip retryably; unit/trajectory claims still grade the steps that
        # DID happen.
        if interrupted and spec.claim.grain in ("terminal", "session"):
            plan.interrupted.append(name)
            continue
        if not grain_matches(
            spec.claim.grain,
            is_terminal=is_terminal,
            behaviour_bound=behaviour_bound,
            turn_slice=turn_slice,
            capability_terminal=capability_terminal,
        ):
            plan.skipped.append(name)
            continue
        # Warrant is not a second filter on bound members — except an outcome
        # judge with no ask and no evidence, which has nothing to grade.
        if behaviour_bound:
            role = str((config.get("behaviour") or {}).get("role") or "")
            if role == "outcome" and not has_ask and not (labels.evidence & _OUTCOME_EVIDENCE):
                plan.abstained[name] = ["evidence:unit_output"]
                continue
            plan.runnable.append(member)
            continue
        unmet = warrant_unmet(spec.warrant, labels)
        if unmet:
            plan.abstained[name] = unmet
            continue
        plan.runnable.append(member)
    return plan


def member_identifier(member: EvalSetMember, spec: EvaluatorSpec) -> str:
    """ "" for deterministic code; the judge contract with the RESOLVED model,
    never the priority alias."""
    evaluator = member.evaluator
    if evaluator.kind in ("deterministic", "statistical"):
        return ""
    digest = _rubric_digest(evaluator)
    if _is_panel_member(member, spec):
        judges = funnel.cross_family_judges(_PANEL_SIZE)
        models = ",".join(sorted(funnel.resolved_model_name(j) for j in judges))
        return f"panel:{funnel.rubric_hash(models)}:{digest}"[:255]
    judge = funnel.resolve_judge(evaluator.judge_model or "")
    return funnel.judge_contract_identifier(judge, digest)


def _rubric_digest(evaluator) -> str:
    stored = str((evaluator.config or {}).get("content_hash") or "")
    digest = stored[:16] or funnel.rubric_hash(
        evaluator.rubric_md or "",
        json.dumps(evaluator.checklist or [], sort_keys=True, default=str),
        evaluator.scope,
        evaluator.score_type,
    )
    # The anchor scale is part of the judge contract: changing it re-dispatches.
    anchors = numeric_anchor_scale(evaluator)
    if anchors:
        return funnel.rubric_hash(digest, anchors)
    return digest


def _is_panel_member(member: EvalSetMember, spec: EvaluatorSpec) -> bool:
    return (
        spec.claim is not None
        and spec.claim.type in ("conformance", "verification")
        and member.evaluator.kind in ("llm_judge", "agentic")
    )


def _panel_evaluate(member: EvalSetMember, unit: eval_base.EvalUnit, ctx: dict[str, Any]):
    """One judge per provider family, geometric median (score) and majority
    (passed). The judge_model override is in-memory only, never saved."""
    drafts: list[eval_base.ScoreDraft] = []
    panel_meta: list[dict[str, Any]] = []
    for judge in funnel.cross_family_judges(_PANEL_SIZE):
        clone = copy.copy(member.evaluator)
        clone.judge_model = judge.model_name or ""
        result = eval_base.evaluate(unit, clone, ctx)
        if not result:
            continue
        draft = result[0]
        drafts.append(draft)
        panel_meta.append(
            {
                "model": funnel.resolved_model_name(judge),
                "family": judge.family,
                "value": draft.value,
                "passed": draft.passed,
                "outcome": draft.outcome,
            }
        )
    scored = [d for d in drafts if d.outcome == eval_base.OUTCOME_SCORED and d.value is not None]
    if not scored:
        return drafts[:1] if drafts else []
    values = [float(d.value) for d in scored]
    value = funnel.geometric_median(values)
    # A persistent 0.0 spread is the evidence for reducing the panel to one judge.
    spread = round(max(values) - min(values), 4)
    verdicts = [d.passed for d in scored if d.passed is not None]
    # A tie is no majority: the boolean abstains rather than failing by default.
    yes = sum(1 for v in verdicts if v)
    no = len(verdicts) - yes
    passed = True if yes > no else (False if no > yes else None)
    costs = [d.cost for d in scored]
    lead = min(scored, key=lambda d: abs((d.value or 0.0) - value))
    merged = eval_base.ScoreDraft(
        name=member.evaluator.name,
        data_type=lead.data_type,
        value=value,
        passed=passed,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning=lead.reasoning,
        scope=lead.scope,
        sub_scores=[*lead.sub_scores, {"_panel": {"members": panel_meta, "spread": spread}}],
        judge_trace_id=lead.judge_trace_id,
        cost=sum(costs) if all(isinstance(c, (int, float)) for c in costs) else 0.0,
        latency_ms=max((d.latency_ms or 0) for d in scored),
    )
    return [merged]


_TURN_SCOPES = frozenset({"turn", "step"})


def _per_turn_units(unit: eval_base.EvalUnit) -> list[tuple[int, eval_base.EvalUnit]]:
    """Tool-graph nodes are built in message order, so slicing them by each
    turn's ``tool_calls`` count attributes every node exactly."""
    trajectory = unit.trajectory or {}
    structured = unit.structured or {}
    messages = trajectory.get("messages") or []
    nodes = (structured.get("tool_graph") or {}).get("nodes") or []
    units: list[tuple[int, eval_base.EvalUnit]] = []
    cursor = 0
    for turn in structured.get("turns") or []:
        turn_msgs = [messages[i] for i in turn.get("messages") or [] if 0 <= i < len(messages)]
        n_calls = int(turn.get("tool_calls") or 0)
        turn_nodes = nodes[cursor : cursor + n_calls]
        cursor += n_calls
        final = next(
            (m.get("content") or "" for m in reversed(turn_msgs) if m.get("role") == "assistant"),
            "",
        )
        turn_trajectory: dict[str, Any] = {
            "messages": turn_msgs,
            "final_output": final,
            "metadata": trajectory.get("metadata") or {},
            "modality": trajectory.get("modality"),
        }
        # Runtime declarations are unit-wide; per-turn predicates still apply.
        if trajectory.get("runtime"):
            turn_trajectory["runtime"] = trajectory["runtime"]
        units.append(
            (
                int(turn.get("index") or 0),
                eval_base.EvalUnit(
                    trajectory=turn_trajectory,
                    structured={
                        "tool_graph": {"nodes": turn_nodes, "edges": []},
                        "turns": [turn],
                        "num_turns": 1,
                        "num_tool_calls": len(turn_nodes),
                    },
                    sample_id=unit.sample_id,
                    output_fields=unit.output_fields,
                    output_schema=unit.output_schema,
                ),
            )
        )
    return units


def _aggregate_turn_drafts(
    evaluator, turn_results: list[tuple[int, eval_base.ScoreDraft]]
) -> eval_base.ScoreDraft:
    subs = [
        {
            "_turn": {
                "index": index,
                "value": draft.value,
                "passed": draft.passed,
                "outcome": draft.outcome,
                "reasoning": (draft.reasoning or "")[:300],
            }
        }
        for index, draft in turn_results
    ]
    scored = [
        draft
        for _, draft in turn_results
        if draft.outcome == eval_base.OUTCOME_SCORED and draft.value is not None
    ]
    if not scored:
        outcomes = {draft.outcome for _, draft in turn_results}
        outcome = (
            eval_base.OUTCOME_NOT_APPLICABLE
            if outcomes == {eval_base.OUTCOME_NOT_APPLICABLE}
            else eval_base.OUTCOME_ABSTAINED
        )
        return eval_base.ScoreDraft(
            name=evaluator.name,
            data_type=evaluator.score_type,
            value=None,
            outcome=outcome,
            reasoning=f"no scored verdict across {len(turn_results)} turn(s)",
            scope=evaluator.scope,
            sub_scores=subs,
        )
    value = sum(draft.value for draft in scored) / len(scored)
    verdicts = [draft.passed for draft in scored if draft.passed is not None]
    passed = all(verdicts) if verdicts else None
    return eval_base.ScoreDraft(
        name=evaluator.name,
        data_type=scored[0].data_type,
        value=value,
        passed=passed,
        outcome=eval_base.OUTCOME_SCORED,
        reasoning=f"{len(scored)}/{len(turn_results)} turn(s) scored; mean {value:.2f}",
        scope=evaluator.scope,
        sub_scores=subs,
    )


def _evaluate_turns(member: EvalSetMember, unit: eval_base.EvalUnit, ctx: dict[str, Any]):
    """Collapses to ONE draft with per-turn sub_scores so the persisted shape is
    scope-independent."""
    turns = _per_turn_units(unit)
    if not turns:
        return eval_base.evaluate(unit, member.evaluator, ctx)
    results: list[tuple[int, eval_base.ScoreDraft]] = []
    for index, turn_unit in turns:
        drafts = eval_base.evaluate(turn_unit, member.evaluator, ctx)
        if drafts:
            results.append((index, drafts[0]))
    if not results:
        return []
    return [_aggregate_turn_drafts(member.evaluator, results)]


def execute_plan(
    plan: DispatchPlan,
    specs: dict[str, EvaluatorSpec],
    unit: eval_base.EvalUnit,
    ctx: dict[str, Any],
    executor: JudgeExecutor,
    *,
    timeout_s: float | None = None,
) -> tuple[dict[str, list[eval_base.ScoreDraft]], dict[str, BaseException]]:
    """Returns ``(drafts_by_name, errors_by_name)``; a failed member never voids the rest."""

    def _task(member: EvalSetMember) -> JudgeTask:
        spec = specs[member.evaluator.name]
        if _is_panel_member(member, spec):
            fn = lambda m=member: _panel_evaluate(m, unit, ctx)  # noqa: E731
            provider = "panel"
        elif member.evaluator.scope in _TURN_SCOPES and (unit.structured or {}).get("turns"):
            fn = lambda m=member: _evaluate_turns(m, unit, ctx)  # noqa: E731
            provider = _member_provider(member)
        else:
            fn = lambda m=member: eval_base.evaluate(unit, m.evaluator, ctx)  # noqa: E731
            provider = _member_provider(member)
        return JudgeTask(key=member.evaluator.name, fn=fn, provider=provider)

    drafts: dict[str, list[eval_base.ScoreDraft]] = {}
    errors: dict[str, BaseException] = {}
    outcomes = executor.run([_task(m) for m in plan.runnable], timeout_s=timeout_s)
    for member in plan.runnable:
        name = member.evaluator.name
        outcome = outcomes.get(name)
        if outcome is None or outcome.deferred:
            continue
        if outcome.error is not None:
            errors[name] = outcome.error
        else:
            drafts[name] = outcome.value or []
    return drafts, errors


def _member_provider(member: EvalSetMember) -> str:
    if member.evaluator.kind in ("deterministic", "statistical"):
        return "deterministic"
    return funnel.resolve_judge(member.evaluator.judge_model or "").family


def verdict_metadata(
    *,
    passed: bool | None,
    scope: str,
    grain: str,
    gate: bool = False,
    surface_area: str = "",
    sub_scores: list[Any] | None = None,
) -> dict[str, Any]:
    """What the Verdict columns don't carry: the boolean verdict and the
    composition inputs (scope/grain/gate/surface_area/sub_scores)."""
    return {
        "passed": passed,
        "scope": scope,
        "grain": grain,
        "gate": gate,
        "surface_area": surface_area,
        "sub_scores": sub_scores or [],
    }


def block_entry(verdict: Verdict) -> dict[str, Any]:
    """One verdict as an in-memory scoring-block entry — composition,
    behaviour scoring, ledger evidence and graph projection consume this
    shape. Derived on every pass, never persisted."""
    meta = verdict.metadata if isinstance(verdict.metadata, dict) else {}
    passed = meta.get("passed")
    sub_scores = meta.get("sub_scores")
    return {
        "score": verdict.score,
        "passed": passed if isinstance(passed, bool) else None,
        "outcome": verdict.outcome,
        "rationale": verdict.explanation,
        "scope": str(meta.get("scope") or ""),
        "grain": str(meta.get("grain") or ""),
        "gate": bool(meta.get("gate")),
        "surface_area": str(meta.get("surface_area") or ""),
        "sub_scores": sub_scores if isinstance(sub_scores, list) else [],
        "evaluator_id": str(verdict.evaluator_id) if verdict.evaluator_id else None,
    }


def verdict_kwargs_from_draft(
    draft: eval_base.ScoreDraft,
    member: EvalSetMember,
    *,
    project_id: str,
    target_id: str,
    identifier: str,
) -> dict[str, Any]:
    evaluator = member.evaluator
    score = None
    if draft.outcome == eval_base.OUTCOME_SCORED and isinstance(draft.value, (int, float)):
        score = funnel.normalize_unit_interval(
            float(draft.value), evaluator.score_min, evaluator.score_max
        )
    cost = draft.cost
    if evaluator.kind not in ("deterministic", "statistical") and not cost:
        cost = None  # unknown judge cost poisons totals rather than undercounting
    provenance = (evaluator.config or {}).get("provenance") or {}
    return {
        "project_id": project_id,
        "evaluator": evaluator,
        "evaluator_name": evaluator.name,
        "target_kind": Verdict.TargetKind.SPAN,
        "target_id": target_id,
        "identifier": identifier,
        "label": (draft.string_value or "")[:64],
        "score": score,
        "outcome": _OUTCOME_MAP.get(draft.outcome, Verdict.Outcome.ERROR),
        "explanation": draft.reasoning or "",
        "unmet": [],
        "metadata": verdict_metadata(
            passed=draft.passed,
            scope=draft.scope,
            grain=evaluator.spec.claim.grain,
            gate=evaluator.spec.claim.type in ("conformance", "verification"),
            surface_area=str(provenance.get("surface_area") or ""),
            sub_scores=draft.sub_scores,
        ),
        "annotator_kind": (
            Verdict.AnnotatorKind.CODE
            if evaluator.kind in ("deterministic", "statistical")
            else Verdict.AnnotatorKind.LLM
        ),
        "judge_trace_id": draft.judge_trace_id or "",
        "cost": cost if cost is None else float(cost),
        "latency_ms": int(draft.latency_ms) if draft.latency_ms else None,
    }


# What each warrant clause actually looked for, so an abstention explains
# itself instead of naming an internal label.
_UNMET_DETAIL = {
    "evidence:final_output": (
        "no terminal deliverable found — looked for an output payload on the "
        "unit's entry span, terminal assistant text, and the terminal "
        "tool-call's arguments"
    ),
    "evidence:tool_io": "no tool or retrieval spans recorded in this unit",
    "evidence:environment_evidence": (
        "no environment-provenance evidence — looked for leaf tool outputs, "
        "provenance=environment spans, and observation events"
    ),
    "evidence:intent": "no declared intent and no user message resolved for this unit",
    "evidence:reference": "no declared expectations on the runtime envelope",
    "evidence:unit_output": (
        "no ask and no output, tool, or environment evidence — nothing to grade service against"
    ),
    "detail:full": (
        "the trajectory was truncated during normalization; this judge "
        "requires the full, uncompacted trajectory"
    ),
}


def abstention_reason(unmet: list[str]) -> str:
    parts = []
    for clause in unmet:
        detail = _UNMET_DETAIL.get(clause)
        if detail is None and clause.startswith("provenance:"):
            detail = f"no {clause.split(':', 1)[1]}-provenance spans or messages in this unit"
        parts.append(f"{clause} ({detail})" if detail else clause)
    return "Warrant unmet: " + "; ".join(parts)


# Skips persist as not_applicable rows under the member's CURRENT identifier so
# a mis-skip is visible; a skip row never settles its series.
SKIP_GRAIN = "skip:grain"
SKIP_BEHAVIOUR = "skip:behaviour-filter"
SKIP_INTERRUPTED = "skip:interrupted"


def is_skip_verdict(verdict: Verdict) -> bool:
    return verdict.outcome == Verdict.Outcome.NOT_APPLICABLE and any(
        str(clause).startswith("skip:") for clause in verdict.unmet or []
    )


def skip_kwargs(
    member: EvalSetMember,
    clause: str,
    reason: str,
    *,
    project_id: str,
    target_id: str,
    identifier: str,
) -> dict[str, Any]:
    return abstention_kwargs(
        member,
        [clause],
        project_id=project_id,
        target_id=target_id,
        identifier=identifier,
        reason=reason,
        outcome=Verdict.Outcome.NOT_APPLICABLE,
    )


def abstention_kwargs(
    member: EvalSetMember,
    unmet: list[str],
    *,
    project_id: str,
    target_id: str,
    identifier: str,
    reason: str,
    outcome: str = Verdict.Outcome.ABSTAINED,
) -> dict[str, Any]:
    evaluator = member.evaluator
    return {
        "project_id": project_id,
        "evaluator": evaluator,
        "evaluator_name": evaluator.name,
        "target_kind": Verdict.TargetKind.SPAN,
        "target_id": target_id,
        "identifier": identifier,
        "label": "",
        "score": None,
        "outcome": outcome,
        "explanation": reason,
        "unmet": unmet,
        "metadata": verdict_metadata(
            passed=None, scope=evaluator.scope, grain=evaluator.spec.claim.grain
        ),
        "annotator_kind": Verdict.AnnotatorKind.LLM,
        "judge_trace_id": "",
        "cost": 0.0,
        "latency_ms": None,
    }


def permanent_error_kwargs(
    member: EvalSetMember,
    error: BaseException,
    *,
    project_id: str,
    target_id: str,
    identifier: str,
) -> dict[str, Any]:
    """Recorded so the sweep converges; a contract change still re-dispatches."""
    return abstention_kwargs(
        member,
        ["error:permanent"],
        project_id=project_id,
        target_id=target_id,
        identifier=identifier,
        reason=f"Permanent judge error: {str(error)[:400]}",
        outcome=Verdict.Outcome.ERROR,
    )


def persist_verdicts(rows: list[dict[str, Any]]) -> list[Verdict]:
    """Upsert on the series key — reruns under the same contract converge."""
    saved: list[Verdict] = []
    for kwargs in rows:
        keys = {
            "evaluator_name": kwargs["evaluator_name"],
            "target_kind": kwargs["target_kind"],
            "target_id": kwargs["target_id"],
            "identifier": kwargs["identifier"],
        }
        defaults = {k: v for k, v in kwargs.items() if k not in keys}
        verdict, _ = Verdict.objects.update_or_create(**keys, defaults=defaults)
        saved.append(verdict)
    return saved


def fetch_existing(project_id: str, target_id: str, wanted: dict[str, str]) -> dict[str, Verdict]:
    """The idempotency checkpoint; a changed contract misses and re-dispatches."""
    if not wanted:
        return {}
    rows = Verdict.objects.filter(
        project_id=project_id,
        target_kind=Verdict.TargetKind.SPAN,
        target_id=target_id,
        evaluator_name__in=list(wanted),
    )
    return {
        row.evaluator_name: row for row in rows if wanted.get(row.evaluator_name) == row.identifier
    }


_DECOMPOSE_SCHEMA = create_model(
    "ClaimDecomposition",
    explanation=(str, Field(description="Brief note on how the deliverable was split")),
    claims=(
        list[str],
        Field(description="Atomic, independently checkable factual claims", max_length=20),
    ),
)

_DECOMPOSE_PROMPT = """Decompose the deliverable below into atomic factual claims.
Each claim must be a single checkable statement about the world the run acted in —
what it asserts happened, was found, was created, or is true. Skip formatting,
style and meta-commentary. Never emit claims about the deliverable's own structure
or shape (field names, key counts, JSON layout, section ordering, word counts):
those are checkable only against the deliverable itself, never against
environment evidence. At most 20 claims.

Deliverable:
{deliverable}
"""

_NLI_PROMPT = """You are verifying factual claims against evidence collected from the
environment (tool outputs and observations recorded during the run). The agent's own
prose is NOT part of the evidence.

For each claim, decide:
- supported: the evidence entails the claim
- contradicted: the evidence entails the opposite
- insufficient: the evidence neither supports nor contradicts it

Evidence:
{corpus}

Claims:
{claims}
"""


def _nli_schema(n: int):
    item = create_model(
        "ClaimCheck",
        explanation=(str, ...),
        index=(int, Field(ge=0, le=max(0, n - 1))),
        verdict=(str, Field(pattern="^(supported|contradicted|insufficient)$")),
    )
    return create_model("ClaimChecks", checks=(list[item], Field(max_length=n)))


def grounding_identifier() -> str:
    judge = funnel.resolve_judge("")
    digest = funnel.rubric_hash(_GROUNDING_RUBRIC_VERSION, _DECOMPOSE_PROMPT, _NLI_PROMPT)
    return funnel.judge_contract_identifier(judge, digest)


def delete_grounding(project_id: str, target_id: str) -> None:
    Verdict.objects.filter(
        project_id=project_id,
        evaluator_name=GROUNDING_VERDICT_NAME,
        target_kind=Verdict.TargetKind.SPAN,
        target_id=target_id,
    ).delete()


def grounding_verdict(
    *,
    trajectory: dict[str, Any],
    unit_spans: list[Span],
    project_id: str,
    target_id: str,
    trace_corpus: list[str] | None = None,
) -> dict[str, Any]:
    """Score is supported / (supported + contradicted), coverage reported apart:
    a corpus that decides nothing is an abstention, never a zero."""
    judge = funnel.resolve_judge("")
    digest = funnel.rubric_hash(_GROUNDING_RUBRIC_VERSION, _DECOMPOSE_PROMPT, _NLI_PROMPT)
    identifier = funnel.judge_contract_identifier(judge, digest)
    base = {
        "project_id": project_id,
        "evaluator": None,
        "evaluator_name": GROUNDING_VERDICT_NAME,
        "target_kind": Verdict.TargetKind.SPAN,
        "target_id": target_id,
        "identifier": identifier,
        "annotator_kind": Verdict.AnnotatorKind.LLM,
    }

    deliverable = str(trajectory.get("final_output") or "").strip()
    corpus = trace_corpus or environment_corpus(unit_spans, trajectory.get("span_tree"))
    unmet = []
    if not deliverable:
        unmet.append("evidence:final_output")
    if not corpus:
        unmet.append("evidence:environment_evidence")
    if unmet:
        return {
            **base,
            "label": "",
            "score": None,
            "outcome": Verdict.Outcome.ABSTAINED,
            "explanation": "No environment-provenance evidence to ground the deliverable against."
            if corpus == []
            else "No terminal deliverable to decompose.",
            "unmet": unmet,
            "metadata": _grounding_metadata(),
            "cost": 0.0,
        }

    decomposed = funnel.invoke_judge(
        _DECOMPOSE_PROMPT.format(deliverable=deliverable[:6000]),
        response_format=_DECOMPOSE_SCHEMA,
        judge=judge,
        project_id=project_id,
    )
    claims = [c.strip() for c in getattr(decomposed.parsed, "claims", None) or [] if c.strip()]
    costs = [_call_cost(decomposed.stats)]
    if not claims:
        return {
            **base,
            "label": "",
            "score": None,
            "outcome": Verdict.Outcome.ABSTAINED,
            "explanation": "The deliverable decomposed into no checkable claims.",
            "unmet": ["claims:none"],
            "metadata": _grounding_metadata(),
            "judge_trace_id": decomposed.judge_trace_id,
            "cost": _accrue(costs),
        }

    corpus_text = "\n".join(corpus)[:240_000]
    claims_text = "\n".join(f"{i}. {c}" for i, c in enumerate(claims))
    checked = funnel.invoke_judge(
        _NLI_PROMPT.format(corpus=corpus_text, claims=claims_text),
        response_format=_nli_schema(len(claims)),
        judge=judge,
        project_id=project_id,
    )
    costs.append(_call_cost(checked.stats))
    checks = getattr(checked.parsed, "checks", None) or []
    verdict_by_index = {int(c.index): str(c.verdict) for c in checks}
    per_claim = [
        {"claim": claim, "verdict": verdict_by_index.get(i, "insufficient")}
        for i, claim in enumerate(claims)
    ]
    supported = sum(1 for c in per_claim if c["verdict"] == "supported")
    contradicted = sum(1 for c in per_claim if c["verdict"] == "contradicted")
    checkable = supported + contradicted
    if checkable == 0:
        explanation = (
            f"0/{len(per_claim)} claims checkable: every claim is insufficient "
            "(none supported, none contradicted) — the environment corpus does "
            "not cover the deliverable's content."
        )
        return {
            **base,
            "label": "",
            "score": None,
            "outcome": Verdict.Outcome.ABSTAINED,
            "explanation": explanation,
            "unmet": ["claims:uncheckable"],
            "metadata": {**_grounding_metadata(sub_scores=per_claim), "coverage": 0.0},
            "judge_trace_id": checked.judge_trace_id,
            "cost": _accrue(costs),
        }
    score = supported / checkable
    coverage = round(checkable / len(per_claim), 4)
    explanation = (
        f"{supported}/{checkable} checkable claims supported "
        f"({contradicted} contradicted); coverage {checkable}/{len(per_claim)} "
        "claims checkable against environment evidence"
    )
    sub_scores = [
        *per_claim,
        {
            "_grounding": {
                "supported": supported,
                "contradicted": contradicted,
                "insufficient": len(per_claim) - checkable,
                "coverage": coverage,
            }
        },
    ]
    return {
        **base,
        "label": "",
        "score": score,
        "outcome": Verdict.Outcome.SCORED,
        "explanation": explanation,
        "unmet": [],
        "metadata": {**_grounding_metadata(sub_scores=sub_scores), "coverage": coverage},
        "judge_trace_id": checked.judge_trace_id,
        "cost": _accrue(costs),
    }


def _grounding_metadata(sub_scores: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    # sub_scores is the per-claim record: every claim with its supported /
    # contradicted / insufficient verdict, so a low ratio is auditable.
    return verdict_metadata(
        passed=None, scope="final_output", grain="terminal", sub_scores=sub_scores
    )


def _call_cost(stats: dict[str, Any]) -> float | None:
    cost = stats.get("response_cost")
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        return None
    if cost <= 0 and not stats.get("cached"):
        return None
    return max(cost, 0.0)


def _accrue(costs: list[float | None]) -> float | None:
    """None poisons — one unknown per-call cost makes the total unknown."""
    if any(c is None for c in costs):
        return None
    return sum(costs)  # type: ignore[arg-type]
