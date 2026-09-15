"""Live trace scoring. Each unit scores onto its own span against its own
capability; the root gets only the ``invocations`` summary. Verdict rows are
the source of truth and the Verdict series key is the idempotency checkpoint;
``feedback_score["trace_scoring"]`` is a derived cache list views read and the
pipeline never does.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from django.db import close_old_connections, connection
from django.utils import timezone

from overbae.models import (
    Behaviour,
    Capability,
    EvalSetMember,
    ScoringPass,
    Span,
    TaskExecution,
)
from overbae.models.traces import is_tool_operation
from overbae.services.behaviour import binder as behaviour_binder
from overbae.services.behaviour import ledger as behaviour_ledger
from overbae.services.behaviour import scoring as behaviour_scoring
from overbae.services.eval import chatml, composition, dispatch, funnel, normalizer
from overbae.services.eval import envelope as eval_envelope
from overbae.services.eval import grounding as eval_grounding
from overbae.services.eval.eval_set import STRUCTURAL_TRACE_GATES
from overbae.services.eval.evaluators import base as eval_base
from overbae.services.eval.funnel import JudgeExecutor
from overbae.services.eval.span_evidence import build_span_tree, produced_identity_fields
from overbae.services.eval.units import (
    ResolvedUnit,
    carve,
    fold_interior_steps,
    is_declared_boundary,
    run_surfaces,
)

logger = logging.getLogger(__name__)

FEEDBACK_KEY = "trace_scoring"

# Must NOT start with ``_`` — the frontend extractor skips underscore keys.
INVOCATIONS_SUMMARY_KEY = "invocations"

# Names only; each skip also persists a retryable not_applicable Verdict row.
SKIPPED_MEMBERS_KEY = "_skipped_members"

# Cross-unit fanout; judge calls within a unit are throttled by JudgeExecutor.
UNIT_FANOUT_WORKERS = 4

# OTel StatusCode ERROR (0=UNSET 1=OK 2=ERROR). A failed run is not a
# representative sample, so grading it would pollute the live quality signal.
STATUS_ERROR = 2


def execution_score(feedback_score: dict[str, Any] | None) -> float | None:
    """The unit's Task Execution Score in [0, 1]; ``None`` when nothing scored.
    Reads the persisted ``_execution`` marker — the only score copy on the
    span; per-evaluator results live on Verdict rows."""
    block = (feedback_score or {}).get(FEEDBACK_KEY) or {}
    persisted = block.get("_execution")
    if isinstance(persisted, dict) and isinstance(persisted.get("score"), (int, float)):
        return float(persisted["score"])
    return None


def _text_tokens(value: Any) -> set[str]:
    text = value if isinstance(value, str) else json.dumps(value, default=str) if value else ""
    return set(re.findall(r"\w+", text.lower()))


def _declared_delivery_unit(
    spans: list[Span], units: list[Span], unit_spans_by_id: dict[str, list[Span]]
) -> str | None:
    """SDK wire contract: ``overmind.delivery = true`` marks the terminal deliverable."""
    delivery_spans = [
        s for s in spans if str((s.attributes or {}).get("overmind.delivery")).lower() == "true"
    ]
    if not delivery_spans:
        return None
    for unit in units:
        unit_ids = {s.span_id for s in unit_spans_by_id[unit.span_id]}
        if any(d.span_id in unit_ids for d in delivery_spans):
            return unit.span_id
    return None


def _terminal_unit_span_id(
    spans: list[Span],
    units: list[Span],
    root: Span,
    unit_spans_by_id: dict[str, list[Span]],
) -> str | None:
    """Declared delivery wins; token coverage is the fallback for uninstrumented
    agents, later units winning ties so a unit that merely discusses the result
    never outranks the one that produced it."""
    if not units:
        return None
    declared = _declared_delivery_unit(spans, units, unit_spans_by_id)
    if declared is not None:
        return declared
    final_tokens = _text_tokens(chatml.pick(root.attributes or {}, chatml.OUTPUT_KEYS))
    if len(final_tokens) >= 8:
        best_id, best_cov = None, 0.3  # floor: below this nothing "carries" the output
        for unit in units:
            unit_tokens: set[str] = set()
            for span in unit_spans_by_id[unit.span_id]:
                unit_tokens |= _text_tokens(chatml.pick(span.attributes or {}, chatml.OUTPUT_KEYS))
            coverage = len(final_tokens & unit_tokens) / len(final_tokens)
            if coverage >= best_cov:
                best_id, best_cov = unit.span_id, coverage
        if best_id is not None:
            return best_id
    return units[-1].span_id


def _unit_tool_summaries(unit_spans: list[Span]) -> list[str]:
    """Leaf tool calls only: a dispatcher parent whose child is also a tool_call
    is the same call twice."""
    tool_spans = [s for s in unit_spans if is_tool_operation(s.span_type)]
    parents_of_tool_calls = {s.parent_span_id for s in tool_spans}
    out: list[str] = []
    for s in sorted(tool_spans, key=lambda x: x.start_time_ns or 0):
        if s.span_id in parents_of_tool_calls:
            continue
        attrs = s.attributes or {}
        raw_args = chatml.maybe_parse_json(chatml.pick(attrs, chatml.INPUT_KEYS))
        name, args = normalizer.unwrap_tool_call(
            attrs.get("tool.name") or s.operation or s.name, raw_args
        )
        if not name:
            continue
        arg_text = ""
        if args not in (None, "", {}):
            arg_text = eval_envelope.clip_text(
                json.dumps(args, default=str, ensure_ascii=False), 120
            )
        out.append(f"{name}({arg_text})" if arg_text else name)
    return out


def _trace_context(
    spans: list[Span],
    root: Span,
    units: list[Span],
    unit_spans_by_id: dict[str, list[Span]],
) -> dict[str, Any]:
    """Raw run structure for every unit's judges — never a pre-concluded verdict."""
    ordered = sorted(spans, key=lambda s: s.start_time_ns or 0)
    t0 = min((s.start_time_ns or 0) for s in ordered)
    t_end = max((s.end_time_ns or 0) for s in ordered)
    terminal_id = _terminal_unit_span_id(spans, units, root, unit_spans_by_id)
    timeline = []
    for index, unit in enumerate(units, start=1):
        timeline.append(
            {
                "index": index,
                "span_id": unit.span_id,
                "operation": unit.operation or unit.name,
                "started_s": round(((unit.start_time_ns or 0) - t0) / 1e9, 1),
                "duration_s": round((unit.duration_ns or 0) / 1e9, 1),
                "status": "error" if unit.status_code == STATUS_ERROR else "ok",
                "terminal": unit.span_id == terminal_id,
                "tools": _unit_tool_summaries(unit_spans_by_id[unit.span_id])[:10],
            }
        )
    declared = eval_envelope.extract_envelope(ordered).get("intent")
    raw_out = chatml.maybe_parse_json(chatml.pick(root.attributes or {}, chatml.OUTPUT_KEYS))
    final_output = ""
    if raw_out not in (None, ""):
        text = raw_out if isinstance(raw_out, str) else json.dumps(raw_out, default=str)
        final_output = eval_envelope.clip_text(text, 1500)
    return {
        "declared_intent": declared,
        "run_total_s": round((t_end - t0) / 1e9, 1),
        "root_final_output": final_output,
        "terminal_span_id": terminal_id,
        "timeline": timeline,
    }


_INTENT_TEXT_CAP = 2000

# A transcript dump handed to a helper capability as its "first user message";
# detected structurally, never by which agent sent it.
_SCAFFOLD_HEADERS = ("conversation context:",)
_SCAFFOLD_ROLE_LINE = re.compile(
    r"^\s*(user|assistant|system|human|ai)\s*:", re.IGNORECASE | re.MULTILINE
)


def _is_scaffold_text(text: str) -> bool:
    lowered = text.lstrip().lower()
    if any(lowered.startswith(header) for header in _SCAFFOLD_HEADERS):
        return True
    roles = {match.group(1).lower() for match in _SCAFFOLD_ROLE_LINE.finditer(text)}
    return len(roles) >= 2


def _this_turn_ask(trajectory: dict[str, Any]) -> dict[str, Any] | None:
    """For most agents the first message IS the ask, so only positive scaffold
    detection may demote it."""
    declared = (trajectory.get("runtime") or {}).get("intent")
    if isinstance(declared, dict) and str(declared.get("text") or "").strip():
        return {
            "text": str(declared["text"]).strip()[:_INTENT_TEXT_CAP],
            "source": str(declared.get("source") or "declared"),
        }
    for message in trajectory.get("messages") or []:
        if message.get("role") == "user" and str(message.get("content") or "").strip():
            text = str(message["content"]).strip()
            return {
                "text": text[:_INTENT_TEXT_CAP],
                "source": "scaffold" if _is_scaffold_text(text) else "first_message",
            }
    return None


def _resolve_intent(trajectory: dict[str, Any]) -> dict[str, Any] | None:
    """This-turn ask only; running intent is a ledger read, not a derivation."""
    current = _this_turn_ask(trajectory)
    runtime = trajectory.get("runtime") or {}
    context = runtime.get("context") if isinstance(runtime.get("context"), dict) else {}
    _, _, conv_id = eval_envelope.parse_conversation_context(context)
    if current is None:
        return None
    intent = dict(current)
    if conv_id:
        intent["conversation_id"] = conv_id
    return intent


def _result_summary(trajectory: dict[str, Any]) -> str:
    parts: list[str] = []
    identity = produced_identity_fields(trajectory.get("span_tree"))
    if identity:
        parts.append("Produced object identity fields:")
        parts.extend(
            f"- {json.dumps(item, default=str, ensure_ascii=False)}"
            for item in identity[:8]
            if isinstance(item, dict)
        )
    final = str(trajectory.get("final_output") or "").strip()
    if final:
        parts.append(f"Final output:\n{final[:1500]}")
    return "\n".join(parts)


def _verdict_summary(scored: dict[str, Any]) -> str:
    """Ledger-classifier evidence; process-grain graded members stay out."""
    lines: list[str] = []
    execution = scored.get("_execution") or {}
    if execution.get("score") is not None:
        lines.append(f"- composite execution score {execution.get('score')}")
    for name, entry in scored.items():
        if str(name).startswith("_") or not isinstance(entry, dict):
            continue
        if entry.get("outcome") != "scored":
            continue
        relevant = (
            entry.get("gate")
            or entry.get("passed") is not None
            or entry.get("scope") == "final_output"
        )
        if not relevant:
            continue
        rationale = str(entry.get("rationale") or "").strip()[:200]
        lines.append(
            f"- {name}: score={entry.get('score')} passed={entry.get('passed')}"
            f"{' — ' + rationale if rationale else ''}"
        )
    if not lines:
        return ""
    return "Independent judge verdicts for this turn:\n" + "\n".join(lines[:15])


def _record_ledger_safe(
    execution: TaskExecution | None, *, user_message: str, result_summary: str
) -> tuple[behaviour_ledger.LedgerState, list]:
    """The ledger must never sink scoring."""
    if execution is None or not (execution.conversation_id or "").strip():
        return behaviour_ledger.LedgerState(), []
    try:
        return behaviour_ledger.record_turn(
            execution, user_message=user_message, result_summary=result_summary
        )
    except Exception:  # noqa: BLE001
        logger.exception("ledger recording failed for execution %s", execution.id)
        return behaviour_ledger.LedgerState(), []


def _persist_intent(
    execution: TaskExecution | None,
    intent: dict[str, Any] | None,
    occupancy: dict[str, list[str]],
    entering_state: dict[str, Any] | None = None,
    ledger_turn: bool | None = None,
) -> None:
    if execution is None:
        return
    observed_route = dict(execution.observed_route or {})
    observed_route["cluster_occupancy"] = occupancy
    observed_route.pop("conversation_so_far", None)
    if ledger_turn is not None:
        # ``False`` marks a mid-run unit the ledger must never treat as a turn.
        observed_route["ledger_turn"] = ledger_turn
    if entering_state:
        observed_route["entering_task_state"] = entering_state
    else:
        observed_route.pop("entering_task_state", None)
    if execution.user_intent == (intent or {}) and execution.observed_route == observed_route:
        return
    execution.user_intent = intent or {}
    execution.observed_route = observed_route
    try:
        execution.save(update_fields=["user_intent", "observed_route", "updated_at"])
    except Exception:  # noqa: BLE001 — grounding persistence must never sink scoring
        logger.exception("intent/occupancy persist failed for execution %s", execution.id)


@dataclass
class _PassState:
    graph: composition.Graph
    executor: JudgeExecutor
    specs: dict[str, Any]
    identifiers: dict[str, str]
    deadline: float | None = None

    def timeout_s(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())


@dataclass
class _UnitOutcome:
    newly_scored: int = 0
    new_names: list[str] = field(default_factory=list)
    block: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    costs: list[float | None] = field(default_factory=list)


def _score_unit(
    *,
    unit_span: Span,
    unit_spans: list[Span],
    capability: Capability,
    members: list[EvalSetMember],
    project_id: str,
    sample_id: str,
    state: _PassState,
    is_terminal: bool = True,
    is_capability_terminal: bool | None = None,
    turn_slice: bool = False,
    interrupted: bool = False,
    execution: TaskExecution | None = None,
    behaviour_skipped: list[tuple[EvalSetMember, str]] | None = None,
    trace_context: dict[str, Any] | None = None,
    record_ledger: bool = True,
    trace_env_corpus: list[str] | None = None,
    mint_grounding: bool = True,
) -> _UnitOutcome:
    """One unit's warrant-checked dispatch: verdicts persisted first, then the
    span's feedback block rebuilt from them as a derived cache."""
    # An interrupted unit has no delivery: nothing to ground, and the last
    # recorded output is not the deliverable.
    mint_grounding = mint_grounding and not interrupted
    trajectory = normalizer.reconstruct_spans(unit_spans)
    trajectory.pop("_raw_tool_spans", None)
    structured = normalizer.structure_trajectory(trajectory)
    output_fields, output_schema, input_schema = eval_base.contract_from_capability(capability)
    unit = eval_base.EvalUnit(
        trajectory=trajectory,
        structured=structured,
        expected=None,
        sample_id=sample_id,
        output_fields=output_fields,
        output_schema=output_schema,
        input_schema=input_schema,
    )

    intent = _resolve_intent(trajectory)
    # A step unit's first user message is harness scaffold; the run-level
    # declared intent is the ask.
    declared = (trace_context or {}).get("declared_intent")
    if (
        isinstance(declared, dict)
        and str(declared.get("text") or "").strip()
        and (intent is None or intent.get("source") in ("first_message", "scaffold"))
    ):
        conv_id = (intent or {}).get("conversation_id") or (
            execution.conversation_id if execution is not None else ""
        )
        intent = {
            "text": str(declared["text"]).strip()[:_INTENT_TEXT_CAP],
            "source": str(declared.get("source") or "declared"),
        }
        if conv_id:
            intent["conversation_id"] = conv_id
    card = (capability.improvement_metadata or {}).get("capability_card") or {}
    tools_called = [
        str(node.get("tool") or "")
        for node in (structured.get("tool_graph") or {}).get("nodes") or []
    ]
    occupancy = eval_grounding.cluster_occupancy(tools_called, card.get("tool_spec") or [])
    # Classifying a scaffold dump as the ask opens phantom asks across capabilities.
    attested = intent is None or intent.get("source") != "scaffold"
    record_ledger = record_ledger and attested
    # The terminal turn is recorded AFTER the judges run so delivered vs
    # delivered_wrong rests on independent verdicts, not self-report.
    is_terminal_turn = bool(((trace_context or {}).get("position") or {}).get("is_terminal"))
    defer_ledger = record_ledger and is_terminal_turn
    if record_ledger and not defer_ledger:
        entering, turn_events = _record_ledger_safe(
            execution,
            user_message=str((intent or {}).get("text") or ""),
            result_summary=_result_summary(trajectory),
        )
    else:
        entering, turn_events = behaviour_ledger.LedgerState(), []
    _persist_intent(
        execution,
        intent,
        occupancy,
        behaviour_ledger.task_state_dict(entering) if entering.asks else None,
        ledger_turn=False
        if not attested
        else (record_ledger if trace_context is not None else None),
    )
    logger.info(
        "trace scoring grounding span=%s ledger_events=%d conv=%s",
        unit_span.span_id,
        len(turn_events),
        (intent or {}).get("conversation_id") or "",
    )

    behaviour_key = (
        execution.behaviour.key if execution is not None and execution.behaviour_id else ""
    )
    # Binding is evidence, not ground truth: the judge weighs route-vs-intent fit.
    binding = {}
    if behaviour_key:
        route = execution.observed_route or {}
        binding = {
            "behaviour_key": behaviour_key,
            "binding_source": execution.binding_source,
            "matched_anchors": route.get("matched_anchors") or [],
        }
    # Terminal units ground against the whole run's observations.
    labels = dispatch.unit_labels(
        trajectory,
        structured,
        unit_spans,
        trace_corpus=trace_env_corpus if is_terminal else None,
    )
    # Raw structure only: ledger conclusions are aggregation state, and feeding
    # judges pre-concluded verdicts breaks independence. ``recorded_evidence``
    # lets "no recorded output" read as a gap instead of a failure.
    ctx: dict[str, Any] = {
        "eval_surface": eval_base.SURFACE_TRACE_SCORING,
        "project_id": str(project_id),
    }
    grounding_block = {
        key: value
        for key, value in {
            "user_intent": intent,
            "trace_context": trace_context,
            "mapping": eval_grounding.capability_mapping_context(capability, behaviour_key),
            "cluster_occupancy": occupancy,
            "binding": binding,
            "produced_identity": produced_identity_fields(trajectory.get("span_tree")),
        }.items()
        if value
    }
    grounding_block["recorded_evidence"] = sorted(labels.evidence)
    ctx["grounding"] = grounding_block

    has_ask = bool(
        intent and str(intent.get("text") or "").strip() and intent.get("source") != "scaffold"
    )
    plan = dispatch.plan_members(
        members,
        state.specs,
        labels,
        is_terminal=is_terminal,
        turn_slice=turn_slice,
        capability_terminal=is_capability_terminal,
        has_ask=has_ask,
        interrupted=interrupted,
    )

    # Skipped members stay in ``wanted`` so their skip rows are visible and the
    # same series key is overwritten once the member dispatches here.
    behaviour_skipped = behaviour_skipped or []
    members_by_name = {m.evaluator.name: m for m in members}
    members_by_name.update({m.evaluator.name: m for m, _ in behaviour_skipped})
    wanted = {name: state.identifiers[name] for name in members_by_name}
    if is_terminal and mint_grounding:
        wanted[dispatch.GROUNDING_VERDICT_NAME] = dispatch.grounding_identifier()
    existing = dispatch.fetch_existing(project_id, unit_span.span_id, wanted)
    # A skip row never settles its series.
    settled = {n: v for n, v in existing.items() if not dispatch.is_skip_verdict(v)}

    plan.runnable = [m for m in plan.runnable if m.evaluator.name not in settled]
    fresh_abstained = {n: u for n, u in plan.abstained.items() if n not in settled}

    drafts, errors = dispatch.execute_plan(
        plan, state.specs, unit, ctx, state.executor, timeout_s=state.timeout_s()
    )

    rows: list[dict[str, Any]] = []
    for name, member_drafts in drafts.items():
        if not member_drafts:
            continue
        rows.append(
            dispatch.verdict_kwargs_from_draft(
                member_drafts[0],
                members_by_name[name],
                project_id=project_id,
                target_id=unit_span.span_id,
                identifier=state.identifiers[name],
            )
        )
    for name, unmet in fresh_abstained.items():
        rows.append(
            dispatch.abstention_kwargs(
                members_by_name[name],
                unmet,
                project_id=project_id,
                target_id=unit_span.span_id,
                identifier=state.identifiers[name],
                reason=dispatch.abstention_reason(unmet),
            )
        )
    skips = (
        [
            (
                members_by_name[name],
                dispatch.SKIP_GRAIN,
                _grain_skip_reason(state.specs[name].claim.grain),
            )
            for name in plan.skipped
        ]
        + [
            (
                members_by_name[name],
                dispatch.SKIP_INTERRUPTED,
                _interrupted_skip_reason(state.specs[name].claim.grain),
            )
            for name in plan.interrupted
        ]
        + [(m, dispatch.SKIP_BEHAVIOUR, reason) for m, reason in behaviour_skipped]
    )
    for member, clause, reason in skips:
        name = member.evaluator.name
        # An existing row — a prior skip, or a real verdict from a pass where
        # the member dispatched — is never overwritten by a skip. Exception:
        # an interrupted skip DOES overwrite a real verdict — spans only
        # arrive, so a unit still interrupted was interrupted when that
        # verdict was minted, and a delivery grade on a run that never
        # delivered is stale by rule, not evidence.
        if name in existing and (
            clause != dispatch.SKIP_INTERRUPTED or dispatch.is_skip_verdict(existing[name])
        ):
            continue
        rows.append(
            dispatch.skip_kwargs(
                member,
                clause,
                reason,
                project_id=project_id,
                target_id=unit_span.span_id,
                identifier=state.identifiers[name],
            )
        )
    if is_terminal and mint_grounding and dispatch.GROUNDING_VERDICT_NAME not in existing:
        try:
            rows.append(
                dispatch.grounding_verdict(
                    trajectory=trajectory,
                    unit_spans=unit_spans,
                    project_id=project_id,
                    target_id=unit_span.span_id,
                    trace_corpus=trace_env_corpus,
                )
            )
        except Exception:  # noqa: BLE001 — grounding must never sink the pass
            logger.exception("grounding node failed for unit %s", unit_span.span_id)
    elif is_terminal and interrupted:
        # Stale by the same rule as delivery grades: a grounding verdict on a
        # still-interrupted unit graded an output that was never the
        # deliverable. Re-minted if the trace completes and re-scores.
        dispatch.delete_grounding(project_id, unit_span.span_id)

    permanent_errors = 0
    for name, error in errors.items():
        # A transient error leaves no verdict so the sweep retries; a permanent
        # one is recorded so the sweep converges.
        logger.error(
            "trace scoring failed for sample=%s unit=%s evaluator=%s",
            sample_id,
            unit_span.span_id,
            name,
            exc_info=error,
        )
        if funnel.is_permanent_error(error):
            permanent_errors += 1
            rows.append(
                dispatch.permanent_error_kwargs(
                    members_by_name[name],
                    error,
                    project_id=project_id,
                    target_id=unit_span.span_id,
                    identifier=state.identifiers[name],
                )
            )

    saved = dispatch.persist_verdicts(rows)
    all_verdicts = {**existing, **{v.evaluator_name: v for v in saved}}

    outcome = _UnitOutcome()
    outcome.newly_scored = sum(1 for v in saved if v.outcome == "scored")
    outcome.new_names = sorted(v.evaluator_name for v in saved)
    outcome.costs = [v.cost for v in saved]
    dispatched = len(plan.runnable) + len(fresh_abstained)
    deferred = max(0, len(plan.runnable) - len(drafts) - len(errors))
    saved_skips = sum(1 for v in saved if dispatch.is_skip_verdict(v))
    outcome.counts = {
        "scored": sum(1 for v in saved if v.outcome == "scored"),
        "abstained": sum(1 for v in saved if v.outcome == "abstained"),
        "not_applicable": sum(1 for v in saved if v.outcome == "not_applicable") - saved_skips,
        "skipped": saved_skips,
        # The sweep re-enqueues on this number; permanent errors are settled.
        "error": len(errors) - permanent_errors,
        "permanent_error": permanent_errors,
        "skipped_existing": len(existing),
        "deferred": deferred,
        "dispatched": dispatched,
    }

    # The in-memory block is rebuilt wholesale from the verdicts — composition,
    # behaviour scoring, ledger evidence and graph projection consume it. An
    # abstained grounding node (empty environment corpus) keeps its Verdict but
    # stays out of the block — the UI would count it as an evaluation — and so
    # do skip verdicts and members this pass skipped: ``_skipped_members``
    # lists them, their rows carry the reason.
    skipped = sorted(
        {*(m.evaluator.name for m, _ in behaviour_skipped), *plan.skipped, *plan.interrupted}
    )
    scored: dict[str, Any] = {}
    scope_unit = eval_base.EvalUnit(structured=structured, trajectory=trajectory)
    for name, verdict in all_verdicts.items():
        if name == dispatch.GROUNDING_VERDICT_NAME and verdict.outcome != "scored":
            continue
        if name in skipped or dispatch.is_skip_verdict(verdict):
            continue
        scored[name] = eval_base.scope_entry_to_unit_tree(dispatch.block_entry(verdict), scope_unit)
    if skipped:
        scored[SKIPPED_MEMBERS_KEY] = skipped
    composed = composition.compose(scored, state.graph)
    if composed is not None:
        scored["_execution"] = composed

    had_error = bool(errors)
    attempted = dispatched > 0
    # Only the underscore markers persist on the span — the composed score,
    # conflict/failure markers and the skipped list, enough for SQL ordering
    # and the list rows. Per-evaluator results are read from Verdict.
    persisted = {k: v for k, v in scored.items() if str(k).startswith("_")}
    old_block = dict((unit_span.feedback_score or {}).get(FEEDBACK_KEY) or {})
    changed = {k: v for k, v in old_block.items() if k != "_scored_at"} != persisted
    if saved or changed or (attempted and not had_error):
        existing_feedback = dict(unit_span.feedback_score or {})
        persisted["_scored_at"] = timezone.now().isoformat()
        existing_feedback[FEEDBACK_KEY] = persisted
        Span.objects.filter(pk=unit_span.pk).update(feedback_score=existing_feedback)
        # ``.update()`` leaves the in-memory span stale; callers read it after this.
        unit_span.feedback_score = existing_feedback

    if defer_ledger:
        # An empty message keeps the classifier from minting a phantom re-prompt;
        # the verdicts are the delivery evidence.
        entering, _ = _record_ledger_safe(
            execution,
            user_message="",
            result_summary="\n\n".join(
                filter(None, [_result_summary(trajectory), _verdict_summary(scored)])
            ),
        )
        _persist_intent(
            execution,
            intent,
            occupancy,
            behaviour_ledger.task_state_dict(entering) if entering.asks else None,
            ledger_turn=True,
        )

    outcome.block = scored
    return outcome


def _unit_invocation_ok(scored_block: dict[str, Any]) -> bool:
    """Abstains are ignored. Only boolean evaluators carry ``passed``, so a
    graded score can never hard-fail an invocation here. Reads the composer's
    ``any_failed`` marker so a persisted (marker-only) block answers the same
    as the in-memory one."""
    execution = scored_block.get("_execution")
    return not (isinstance(execution, dict) and execution.get("any_failed") is True)


def _write_invocations_summary(
    root: Span,
    *,
    n_ok: int,
    n_total: int,
    unit_executions: list[dict[str, Any]] | None = None,
) -> None:
    existing = dict(root.feedback_score or {})
    scored = dict(existing.get(FEEDBACK_KEY) or {})
    # Drop amalgamated keys left by a prior whole-tree score, or the list keeps
    # showing the wrong unit's chips.
    for key in list(scored.keys()):
        if key == INVOCATIONS_SUMMARY_KEY or str(key).startswith("_"):
            continue
        del scored[key]
    scored[INVOCATIONS_SUMMARY_KEY] = {
        "score": None,
        "passed": n_ok == n_total and n_total > 0,
        "outcome": "scored",
        # The chip renderer regex-parses this string — keep it human-readable.
        "rationale": f"{n_ok}/{n_total} invocations passed",
        "scope": "trace",
        "lane": "summary",
        "scored_at": timezone.now().isoformat(),
    }
    values = [e["score"] for e in unit_executions or [] if isinstance(e.get("score"), (int, float))]
    scored["_execution"] = {
        "score": round(sum(values) / len(values), 4) if values else None,
        "invocations": n_total,
        "invocations_ok": n_ok,
        "units": unit_executions or [],
    }
    scored["_scored_at"] = timezone.now().isoformat()
    existing[FEEDBACK_KEY] = scored
    Span.objects.filter(pk=root.pk).update(feedback_score=existing)
    root.feedback_score = existing


def _bind_execution_safe(
    *,
    unit_span: Span,
    unit_spans: list[Span],
    capability: Capability | None,
    project_id: str,
    interrupted: bool = False,
    ancestor_spans: list[Span] | None = None,
    resolution: behaviour_binder.BindingResolution | None = None,
    folded_steps: list[dict[str, str]] | None = None,
    carve_source: str = "",
    degraded_carve: bool = False,
) -> TaskExecution | None:
    """Binding must never sink scoring."""
    try:
        return behaviour_binder.bind_execution(
            unit_span=unit_span,
            unit_spans=unit_spans,
            capability=capability,
            project_id=project_id,
            interrupted=interrupted,
            ancestor_spans=ancestor_spans,
            resolution=resolution,
            folded_steps=folded_steps,
            carve_source=carve_source,
            degraded_carve=degraded_carve,
        )
    except Exception:  # noqa: BLE001
        logger.exception("behaviour binding failed for unit %s", unit_span.span_id)
        return None


def _resolve_binding_safe(
    *,
    unit_span: Span,
    unit_spans: list[Span],
    capability: Capability | None,
    project_id: str,
    ancestor_spans: list[Span] | None = None,
) -> behaviour_binder.BindingResolution | None:
    try:
        return behaviour_binder.resolve_binding(
            unit_span=unit_span,
            unit_spans=unit_spans,
            capability=capability,
            project_id=project_id,
            ancestor_spans=ancestor_spans,
        )
    except Exception:  # noqa: BLE001
        logger.exception("binding resolution failed for unit %s", unit_span.span_id)
        return None


def _reconcile_stale_executions(project_id: str, trace_id: str, unit_span_ids: list[str]) -> None:
    """Re-key the single-unit case so the row keeps its id, scores and ledger
    links; otherwise delete — the bind pass recreates rows and
    ``ConversationEvent.execution`` is SET_NULL."""
    try:
        stale = list(
            TaskExecution.objects.filter(project_id=project_id, trace_id=trace_id).exclude(
                unit_span_id__in=unit_span_ids
            )
        )
        if not stale:
            return
        if (
            len(stale) == 1
            and len(unit_span_ids) == 1
            and not TaskExecution.objects.filter(
                project_id=project_id, unit_span_id=unit_span_ids[0]
            ).exists()
        ):
            TaskExecution.objects.filter(pk=stale[0].pk).update(unit_span_id=unit_span_ids[0])
            return
        TaskExecution.objects.filter(pk__in=[row.pk for row in stale]).delete()
    except Exception:  # noqa: BLE001 — reconciliation must never sink scoring
        logger.exception("execution reconciliation failed for trace %s", trace_id)


def _score_execution_safe(execution: TaskExecution | None, scored_block: dict[str, Any]) -> None:
    if execution is None:
        return
    try:
        behaviour_scoring.score_execution(execution, scored_block)
    except Exception:  # noqa: BLE001
        logger.exception("behaviour scoring failed for execution %s", execution.id)


def _behaviour_skipped(
    members: list[EvalSetMember],
    selected: list[EvalSetMember],
    execution: TaskExecution | None,
) -> list[tuple[EvalSetMember, str]]:
    selected_ids = {m.id for m in selected}
    return [
        (m, behaviour_scoring.behaviour_skip_reason(m, execution))
        for m in members
        if m.id not in selected_ids
    ]


def _grain_skip_reason(grain: str) -> str:
    where = (
        "its capability's terminal unit" if grain == "trajectory" else "the trace's terminal unit"
    )
    return (
        f"Skipped: {grain}-grain claim binds at {where}; this unit is not it. "
        "Retryable — the row is overwritten when the member dispatches here."
    )


def _interrupted_skip_reason(grain: str) -> str:
    return (
        f"Skipped: {grain}-grain claim grades the run's delivery; this run was "
        "interrupted before delivering, so there is no terminal deliverable to "
        "grade. Retryable — the row is overwritten if the trace completes and "
        "the member dispatches."
    )


def _merge_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for key, value in part.items():
        total[key] = total.get(key, 0) + value


def _finish_pass(
    scoring_pass: ScoringPass, counts: dict[str, int], costs: Iterable[float | None]
) -> None:
    costs = list(costs)
    total_cost = None if any(c is None for c in costs) else sum(costs)
    ScoringPass.objects.filter(pk=scoring_pass.pk).update(
        finished=timezone.now(), verdict_counts=counts, total_cost=total_cost
    )


def _pass_state(
    capability: Capability, members: list[EvalSetMember], *, deadline: float | None
) -> _PassState:
    specs = {m.evaluator.name: m.evaluator.spec for m in members}
    state = _PassState(
        graph=composition.compile_graph(specs),
        executor=JudgeExecutor(),
        specs=specs,
        identifiers={
            m.evaluator.name: dispatch.member_identifier(m, specs[m.evaluator.name])
            for m in members
        },
        deadline=deadline,
    )
    return state


@dataclass(frozen=True)
class _CapabilityScoring:
    capability: Capability
    members: list[EvalSetMember]
    state: _PassState


def _scoring_members(capability: Capability) -> list[EvalSetMember]:
    eval_set = capability.active_eval_set
    if eval_set is None:
        return []
    members = list(
        eval_set.members.filter(
            role=EvalSetMember.Role.TRACE_SCORING, enabled=True, evaluator__is_archived=False
        )
        .select_related("evaluator")
        .order_by("order", "created_at")
    )
    return [
        m
        for m in members
        if m.evaluator_id
        and (m.evaluator.surface or "any") != "model"
        and m.evaluator.name not in STRUCTURAL_TRACE_GATES
    ]


def _capability_scoring(
    capability: Capability,
    cache: dict[Any, _CapabilityScoring | None],
    *,
    deadline: float | None,
) -> _CapabilityScoring | None:
    """``None`` units bind executions but score nothing."""
    if capability.pk in cache:
        return cache[capability.pk]
    members = _scoring_members(capability)
    if not members:
        cache[capability.pk] = None
        return None
    ctx = _CapabilityScoring(
        capability=capability,
        members=members,
        state=_pass_state(capability, members, deadline=deadline),
    )
    cache[capability.pk] = ctx
    return ctx


def _unit_capability(unit: Span, unit_spans: list[Span]) -> Capability | None:
    """Span-level identity wins over the stored FK: process-global resource
    identity is often the first ``overmind.init()``."""
    resolved = behaviour_binder.resolve_unit_capability(str(unit.project_id), unit, unit_spans)
    if resolved is not None:
        return resolved
    if unit.capability_id:
        return unit.capability
    ordered = sorted(unit_spans, key=lambda s: s.start_time_ns or 0)
    return next((s.capability for s in ordered if s.capability_id), None)


@dataclass(frozen=True)
class _UnitPlan:
    index: int
    unit: Span
    is_terminal: bool
    is_capability_terminal: bool
    interrupted: bool
    record_ledger: bool
    ctx: _CapabilityScoring


def score_trace(trace_id: str, project_id: str, *, deadline: float | None = None) -> dict[str, Any]:
    """Idempotent per Verdict series; unscorable traces return ``skipped``, never
    an error. ``deadline`` (``time.monotonic()``) stops new units from starting;
    the sweep resumes the remainder."""
    spans = list(
        Span.objects.filter(trace_id=trace_id, project_id=project_id).select_related(
            "capability", "capability__active_eval_set"
        )
    )
    if not spans:
        return {"status": "skipped", "reason": "no_spans", "trace_id": trace_id}

    # The SDK always emits a boundary for a real run, so a lone function span is
    # an interior fragment, not a run. A lone untyped span stays scorable: an
    # uninstrumented one-call agent's root carries no boundary either.
    if (
        len(spans) == 1
        and (spans[0].span_type or "") == "function"
        and not is_declared_boundary(spans[0])
    ):
        _reconcile_stale_executions(project_id, trace_id, [])
        return {"status": "skipped", "reason": "orphan_fragment", "trace_id": trace_id}

    carved = carve(spans)
    root = carved.root

    # A subprocess-spawned span can carry the capability the root lacks.
    trace_capability = root.capability or next(
        (s.capability for s in spans if s.capability_id), None
    )
    if trace_capability is None:
        return {"status": "skipped", "reason": "no_capability", "trace_id": trace_id}

    # An error root only voids the trace when the root itself is the scoring
    # unit. A multi-entry run whose root errored (e.g. cancelled teardown after
    # delivery) still holds clean units; those score and error units are
    # filtered per unit in the plan.
    if root.status_code == STATUS_ERROR and not carved.multi_entry:
        return {"status": "skipped", "reason": "error_trace", "trace_id": trace_id}

    units: list[ResolvedUnit] = carved.units
    unit_spans_by_id = {u.unit_span.span_id: u.member_spans for u in units}
    unit_capabilities = {
        u.unit_span.span_id: _unit_capability(u.unit_span, u.member_spans) for u in units
    }
    resolutions = {
        u.unit_span.span_id: _resolve_binding_safe(
            unit_span=u.unit_span,
            unit_spans=u.member_spans,
            capability=unit_capabilities[u.unit_span.span_id],
            project_id=project_id,
            ancestor_spans=u.ancestor_spans,
        )
        for u in units
    }

    # Folded turns' spans are already inside the enclosing subtree, so evidence
    # and judging are untouched.
    folded_steps_by_target: dict[str, list[dict[str, str]]] = {}
    if carved.multi_entry:
        folds = fold_interior_steps(spans, [u.unit_span for u in units], resolutions)
        if folds:
            by_id = {s.span_id: s for s in spans}
            for folded_id in sorted(folds, key=lambda sid: by_id[sid].start_time_ns or 0):
                resolved = resolutions[folded_id]
                folded_steps_by_target.setdefault(folds[folded_id], []).append(
                    {
                        "span_id": folded_id,
                        "entry_qualname": resolved.entry_qualname if resolved else "",
                    }
                )
            units = [u for u in units if u.unit_span.span_id not in folds]

    # A surface is kept only when a run-grain behaviour binds it; an unbound
    # boundary stays a pure run boundary.
    run_units: list[ResolvedUnit] = []
    for surface in run_surfaces(spans, carved):
        surface_id = surface.unit_span.span_id
        surface_capability = _unit_capability(surface.unit_span, surface.member_spans)
        resolution = _resolve_binding_safe(
            unit_span=surface.unit_span,
            unit_spans=surface.member_spans,
            capability=surface_capability,
            project_id=project_id,
            ancestor_spans=surface.ancestor_spans,
        )
        if (
            resolution is None
            or resolution.version is None
            or resolution.version.behaviour.grain != Behaviour.Grain.RUN
        ):
            continue
        run_units.append(surface)
        unit_capabilities[surface_id] = surface_capability
        resolutions[surface_id] = resolution

    _reconcile_stale_executions(
        project_id, trace_id, [u.unit_span.span_id for u in [*units, *run_units]]
    )

    # Executions materialize even when nothing is scorable: the deviation
    # surface needs the unbound row.
    executions: dict[str, TaskExecution | None] = {}
    for unit in [*units, *run_units]:
        executions[unit.unit_span.span_id] = _bind_execution_safe(
            unit_span=unit.unit_span,
            unit_spans=unit.member_spans,
            capability=unit_capabilities[unit.unit_span.span_id],
            project_id=project_id,
            interrupted=unit.interrupted,
            ancestor_spans=unit.ancestor_spans,
            resolution=resolutions.get(unit.unit_span.span_id),
            folded_steps=folded_steps_by_target.get(unit.unit_span.span_id),
            carve_source=unit.carve_source,
            degraded_carve=unit.degraded,
        )

    scoring_cache: dict[Any, _CapabilityScoring | None] = {}
    contexts = {
        u.unit_span.span_id: (
            None
            if unit_capabilities[u.unit_span.span_id] is None
            else _capability_scoring(
                unit_capabilities[u.unit_span.span_id], scoring_cache, deadline=deadline
            )
        )
        for u in [*units, *run_units]
    }
    if all(ctx is None for ctx in contexts.values()):
        if all(cap is None for cap in unit_capabilities.values()):
            reason = "units_unattributed"
        else:
            reason = "no_eval_set" if trace_capability.active_eval_set is None else "no_members"
        return {"status": "skipped", "reason": reason, "trace_id": trace_id}

    scoring_pass = ScoringPass.objects.create(
        project_id=project_id,
        capability=trace_capability,
        trace_id=trace_id,
    )

    if carved.multi_entry:
        return _score_multi_entry(
            trace_id=trace_id,
            project_id=project_id,
            root=root,
            spans=spans,
            entry_points=[u.unit_span for u in units],
            interrupted_ids={u.unit_span.span_id for u in units if u.interrupted},
            run_units=run_units,
            contexts=contexts,
            unit_capabilities=unit_capabilities,
            unit_spans_by_id=unit_spans_by_id,
            executions=executions,
            scoring_pass=scoring_pass,
            deadline=deadline,
            turn_slices=carved.turn_slices,
        )

    ctx = contexts[root.span_id]
    execution = executions.get(root.span_id)
    selected = behaviour_scoring.filter_members_for_execution(ctx.members, execution)
    outcome = _score_unit(
        unit_span=root,
        unit_spans=spans,
        capability=ctx.capability,
        members=selected,
        project_id=project_id,
        sample_id=trace_id,
        state=ctx.state,
        is_terminal=True,
        interrupted=units[0].interrupted,
        execution=execution,
        behaviour_skipped=_behaviour_skipped(ctx.members, selected, execution),
    )
    _score_execution_safe(execution, outcome.block)
    _finish_pass(scoring_pass, outcome.counts, outcome.costs)
    if outcome.newly_scored == 0 and not outcome.new_names:
        return {"status": "no_change", "trace_id": trace_id, "members": len(ctx.members)}

    logger.info(
        "Scored trace %s (capability=%s): %d new verdict(s)",
        trace_id,
        ctx.capability.slug,
        len(outcome.new_names),
    )
    return {
        "status": "scored",
        "trace_id": trace_id,
        "scored": outcome.counts["scored"],
        "verdicts": len(outcome.new_names),
    }


def _capability_terminal_ids(
    entry_points: list[Span],
    unit_capabilities: dict[str, Capability | None],
    terminal_id: str | None,
) -> set[str]:
    """Where trajectory-grain claims bind: the trace terminal for the capability
    that delivered, each other capability's last unit otherwise."""
    ids = {terminal_id} if terminal_id else set()
    terminal_capability = unit_capabilities.get(terminal_id or "")
    last_by_capability: dict[Any, str] = {}
    for unit in entry_points:
        capability = unit_capabilities.get(unit.span_id)
        if capability is None:
            continue
        if terminal_capability is not None and capability.pk == terminal_capability.pk:
            continue
        last_by_capability[capability.pk] = unit.span_id
    return ids | set(last_by_capability.values())


def _score_multi_entry(
    *,
    trace_id: str,
    project_id: str,
    root: Span,
    spans: list[Span],
    entry_points: list[Span],
    interrupted_ids: set[str],
    run_units: list[ResolvedUnit],
    contexts: dict[str, _CapabilityScoring | None],
    unit_capabilities: dict[str, Capability | None],
    unit_spans_by_id: dict[str, list[Span]],
    executions: dict[str, TaskExecution | None],
    scoring_pass: ScoringPass,
    deadline: float | None,
    turn_slices: bool,
) -> dict[str, Any]:
    new_verdicts_total = 0
    units_scored = 0
    deferred_units = 0
    n_ok = 0
    n_total = len(entry_points)
    unit_executions: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    costs: list[float | None] = []

    trace_context = _trace_context(spans, root, entry_points, unit_spans_by_id)
    terminal_id = trace_context.get("terminal_span_id")
    capability_terminals = _capability_terminal_ids(entry_points, unit_capabilities, terminal_id)
    trace_env_corpus = dispatch.environment_corpus(spans, build_span_tree(spans))

    # Unattributed and unscorable units already materialized their execution row.
    plan = [
        _UnitPlan(
            index=index,
            unit=ep,
            is_terminal=ep.span_id == terminal_id,
            is_capability_terminal=ep.span_id in capability_terminals,
            interrupted=ep.span_id in interrupted_ids,
            # The ask opens on the first unit and closes on the terminal one;
            # mid-run units are not conversation turns.
            record_ledger=index == 1 or ep.span_id == terminal_id,
            ctx=contexts[ep.span_id],
        )
        for index, ep in enumerate(entry_points, start=1)
        if ep.status_code != STATUS_ERROR and contexts[ep.span_id] is not None
    ]

    def _run_one(item: _UnitPlan) -> _UnitOutcome:
        close_old_connections()
        try:
            execution = executions.get(item.unit.span_id)
            selected = behaviour_scoring.filter_members_for_execution(item.ctx.members, execution)
            outcome = _score_unit(
                unit_span=item.unit,
                unit_spans=unit_spans_by_id[item.unit.span_id],
                capability=item.ctx.capability,
                members=selected,
                project_id=project_id,
                sample_id=f"{trace_id}:{item.unit.span_id}",
                state=item.ctx.state,
                is_terminal=item.is_terminal,
                is_capability_terminal=item.is_capability_terminal,
                turn_slice=turn_slices,
                interrupted=item.interrupted,
                execution=execution,
                behaviour_skipped=_behaviour_skipped(item.ctx.members, selected, execution),
                trace_env_corpus=trace_env_corpus,
                trace_context={
                    **trace_context,
                    "position": {
                        "index": item.index,
                        "of": n_total,
                        "operation": item.unit.operation or item.unit.name,
                        "is_terminal": item.is_terminal,
                    },
                },
                record_ledger=item.record_ledger,
            )
            _score_execution_safe(execution, outcome.block)
            return outcome
        finally:
            connection.close()

    # Ledger events must land in conversation order: opening first, terminal
    # last, the rest fan out. The root summary is the sole shared write.
    opening = [p for p in plan if p.record_ledger and not p.is_terminal]
    closing = [p for p in plan if p.is_terminal]
    middle = [p for p in plan if not p.record_ledger]

    results: dict[str, _UnitOutcome] = {}
    for phase, workers in ((opening, 1), (middle, UNIT_FANOUT_WORKERS), (closing, 1)):
        if not phase:
            continue
        if deadline is not None and time.monotonic() > deadline:
            # The sweep re-enqueues and Verdict-series idempotency resumes here;
            # existing scores still feed the summary.
            deferred_units += len(phase)
            continue
        if workers <= 1 or len(phase) == 1:
            outcomes = []
            for item in phase:
                try:
                    outcomes.append(_run_one(item))
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(exc)
        else:
            with ThreadPoolExecutor(
                max_workers=min(workers, len(phase)), thread_name_prefix="trace-units"
            ) as pool:
                futures = [pool.submit(_run_one, item) for item in phase]
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result())
                    except Exception as exc:  # noqa: BLE001
                        outcomes.append(exc)
        for item, outcome in zip(phase, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                # One failed unit must not sink the pass; the sweep retries it.
                logger.error(
                    "trace scoring unit failed trace=%s unit=%s",
                    trace_id,
                    item.unit.span_id,
                    exc_info=outcome,
                )
            else:
                results[item.unit.span_id] = outcome

    for item in plan:
        outcome = results.get(item.unit.span_id)
        if outcome is not None:
            new_verdicts_total += len(outcome.new_names)
            if outcome.new_names:
                units_scored += 1
            _merge_counts(counts, outcome.counts)
            costs.extend(outcome.costs)
        # The full rebuilt block when the unit ran; the persisted marker block
        # (``_execution``/``_skipped_members`` only) when it errored this pass.
        block = (
            outcome.block if outcome else (item.unit.feedback_score or {}).get(FEEDBACK_KEY) or {}
        )
        if block and _unit_invocation_ok(block):
            n_ok += 1
        unit_execution = block.get("_execution")
        if unit_execution:
            unit_executions.append(
                {"span_id": item.unit.span_id, "score": unit_execution.get("score")}
            )

    # Run-grain surfaces score after every real unit: only the bound
    # behaviour's own members dispatch (the generic and terminal suites
    # already ran on the turn units), no ledger turn and no second grounding
    # verdict are recorded, and the surface never counts as an invocation —
    # so nothing the turn units scored is counted twice.
    for unit in run_units:
        surface_id = unit.unit_span.span_id
        ctx = contexts.get(surface_id)
        execution = executions.get(surface_id)
        if ctx is None or execution is None or execution.behaviour_id is None:
            continue
        if deadline is not None and time.monotonic() > deadline:
            deferred_units += 1
            continue
        bound = [
            m
            for m in ctx.members
            if behaviour_scoring.behaviour_binding(m.evaluator).get("behaviour_key")
            == execution.behaviour.key
        ]
        selected = behaviour_scoring.filter_members_for_execution(bound, execution)
        try:
            outcome = _score_unit(
                unit_span=unit.unit_span,
                unit_spans=unit.member_spans,
                capability=ctx.capability,
                members=selected,
                project_id=project_id,
                sample_id=f"{trace_id}:{surface_id}",
                state=ctx.state,
                # The run boundary contains the delivery, so terminal-grain
                # judges are coherent; the terminal turn already grounded it.
                is_terminal=True,
                mint_grounding=False,
                execution=execution,
                behaviour_skipped=_behaviour_skipped(bound, selected, execution),
                trace_context=trace_context,
                record_ledger=False,
                trace_env_corpus=trace_env_corpus,
            )
        except Exception:  # noqa: BLE001 — a failed surface must not sink the pass
            logger.exception("run surface scoring failed trace=%s unit=%s", trace_id, surface_id)
            continue
        _score_execution_safe(execution, outcome.block)
        new_verdicts_total += len(outcome.new_names)
        if outcome.new_names:
            units_scored += 1
        _merge_counts(counts, outcome.counts)
        costs.extend(outcome.costs)

    # Always refresh the root summary so list chips stay accurate after re-runs.
    _write_invocations_summary(root, n_ok=n_ok, n_total=n_total, unit_executions=unit_executions)

    counts["deferred"] = counts.get("deferred", 0) + deferred_units
    _finish_pass(scoring_pass, counts, costs)

    ctxs = {id(ctx): ctx for ctx in contexts.values() if ctx is not None}.values()
    n_members = len({m.id for ctx in ctxs for m in ctx.members})
    if new_verdicts_total == 0:
        return {
            "status": "no_change",
            "trace_id": trace_id,
            "members": n_members,
            "mode": "multi_entry",
            "invocations": n_total,
            "invocations_ok": n_ok,
            "deferred": deferred_units,
        }

    slugs = sorted({ctx.capability.slug for ctx in ctxs})
    logger.info(
        "Scored trace %s multi-entry (capabilities=%s): %d new verdict(s) across %d/%d "
        "invocations (%d ok, %d units deferred)",
        trace_id,
        ",".join(slugs),
        new_verdicts_total,
        units_scored,
        n_total,
        n_ok,
        deferred_units,
    )
    return {
        "status": "scored",
        "trace_id": trace_id,
        "scored": new_verdicts_total,
        "invocations": n_total,
        "invocations_ok": n_ok,
        "mode": "multi_entry",
        "deferred": deferred_units,
    }
