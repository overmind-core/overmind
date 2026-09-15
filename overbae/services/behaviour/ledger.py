"""Conversation ask ledger.

The LLM turn classifier is the only writer of ``ConversationEvent`` rows and
recorded transitions are never re-run, which is what keeps rescores
deterministic. A failed classification leaves the turn unrecorded (retried on
the next pass) — deliberately no lexical fallback.
"""

from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache
from pydantic import BaseModel, Field

from overbae.models import ConversationEvent, TaskExecution
from overbae.services.eval import funnel as judging

logger = logging.getLogger(__name__)

# Concurrent scoring passes on different traces of one conversation must not
# interleave ledger writes: each writer reads max(order)+1, so a race lands a
# terminal delivery before the ask that opened it and the fold reads garbage.
_LOCK_TIMEOUT_S = 300
_LOCK_WAIT_S = 120


@contextmanager
def _conversation_write_lock(project_id, conversation_id: str):
    key = f"ledger_write:{project_id}:{conversation_id}"
    deadline = time.monotonic() + _LOCK_WAIT_S
    acquired = False
    while time.monotonic() < deadline:
        acquired = cache.add(key, "1", timeout=_LOCK_TIMEOUT_S)
        if acquired:
            break
        time.sleep(0.25)
    if not acquired:
        # Proceeding unlocked risks an ordering race but never sinks scoring.
        logger.warning("ledger write lock timed out for conversation %s", conversation_id)
    try:
        yield
    finally:
        if acquired:
            cache.delete(key)


ASK_KINDS = ("produce", "inspect", "instruct")
_EVENT_TYPES = frozenset(ConversationEvent.EventType.values)
_RATIONALE_CAP = 500
_ASK_TEXT_CAP = 2000


@dataclass
class AskState:
    ask_id: str
    text: str
    kind: str
    status: str = "open"  # open | delivered | refused | superseded
    delivered_wrong: bool = False
    parks: int = 0
    reprompts: int = 0

    @property
    def is_open(self) -> bool:
        return self.status == "open"


@dataclass
class LedgerState:
    asks: dict[str, AskState] = field(default_factory=dict)

    def open_asks(self) -> list[AskState]:
        return [a for a in self.asks.values() if a.is_open]

    def current_ask(self) -> AskState | None:
        open_asks = self.open_asks()
        if open_asks:
            return open_asks[-1]
        return next(reversed(self.asks.values()), None)

    def outstanding_produce(self) -> list[AskState]:
        """Open produce asks, plus produce asks delivered wrong and never
        subsequently delivered — the session-zeroing set."""
        out = [a for a in self.asks.values() if a.kind == "produce" and a.is_open]
        out += [
            a
            for a in self.asks.values()
            if a.kind == "produce" and a.delivered_wrong and not a.is_open and a not in out
        ]
        return out

    def running_intent(self) -> str:
        current = self.current_ask()
        return current.text if current else ""


def fold(events: list[Any]) -> LedgerState:
    """Pure; accepts any objects carrying ``event_type / ask_id / ask_text / ask_kind``."""
    state = LedgerState()
    for event in events:
        event_type = str(getattr(event, "event_type", "") or "")
        ask_id = str(getattr(event, "ask_id", "") or "")
        text = str(getattr(event, "ask_text", "") or "")
        kind = str(getattr(event, "ask_kind", "") or "")
        ask = state.asks.get(ask_id)
        if event_type == ConversationEvent.EventType.ASK_OPENED:
            if ask is None:
                state.asks[ask_id] = AskState(ask_id, text, kind or "instruct")
            else:
                ask.status = "open"
                ask.text = text or ask.text
                ask.kind = kind or ask.kind
            continue
        if ask is None:
            # A transition for an ask the fold never saw opened (manual edit,
            # partial wipe): materialize it so the event still counts.
            ask = AskState(ask_id, text, kind or "instruct")
            state.asks[ask_id] = ask
        if event_type == ConversationEvent.EventType.ASK_SUPERSEDED:
            ask.status = "superseded"
        elif event_type == ConversationEvent.EventType.ASK_REPROMPTED:
            # A re-prompt reopens even a refused/superseded ask.
            ask.reprompts += 1
            ask.status = "open"
        elif event_type == ConversationEvent.EventType.DELIVERED:
            ask.status = "delivered"
            ask.delivered_wrong = False
        elif event_type == ConversationEvent.EventType.DELIVERED_WRONG:
            ask.delivered_wrong = True
            ask.status = "open"
        elif event_type == ConversationEvent.EventType.REFUSED:
            ask.status = "refused"
        elif event_type == ConversationEvent.EventType.PARKED:
            ask.parks += 1
    return state


def task_state_dict(state: LedgerState) -> dict[str, Any]:
    """The ``observed_route["task_state"]`` payload the console reads."""
    outstanding = state.outstanding_produce()
    delivered_wrong = any(a.delivered_wrong for a in outstanding)
    if delivered_wrong:
        reason = "produced artifact of the wrong kind, type, or intent; running produce still open"
    elif any(a.reprompts for a in outstanding):
        reason = "same produce/accomplish ask still not delivered across the conversation"
    elif outstanding:
        reason = "produce ask still open; not delivered"
    else:
        reason = ""
    return {
        "running_intent": state.running_intent(),
        "asked": [a.text for a in state.asks.values() if a.text],
        "outstanding_asks": [a.text for a in outstanding],
        "delivered_wrong": delivered_wrong,
        "status": "outstanding" if outstanding else "delivered",
        "reason": reason,
    }


class TurnTransition(BaseModel):
    event: str = Field(
        description=(
            "one of: ask_opened | ask_superseded | ask_reprompted | delivered | "
            "delivered_wrong | refused | parked"
        )
    )
    ask_id: str = Field(
        default="",
        description="existing ledger ask id; empty ONLY on ask_opened (a new id is assigned)",
    )
    ask_text: str = Field(default="", description="the ask, verbatim-ish; required on ask_opened")
    ask_kind: str = Field(default="", description="ask_opened only: produce | inspect | instruct")
    rationale: str = Field(default="", description="one sentence: why this transition")


class TurnTransitions(BaseModel):
    transitions: list[TurnTransition] = Field(default_factory=list)


_CLASSIFIER_SYSTEM = (
    "You maintain the ask ledger of one user↔agent conversation. The ledger "
    "records what the user asked for and what happened to each ask. Given the "
    "ledger so far, this turn's user message, and a summary of what the agent "
    "delivered this turn, return the transitions for THIS TURN only, as valid "
    "JSON matching the schema. Reference asks by their ledger ask_id."
)

_CLASSIFIER_RUBRIC = """Event vocabulary:
- ask_opened: the user message states an ask of its own. New ask: leave ask_id empty, set ask_text and ask_kind (produce = create/build/generate/change something; inspect = look up/list/show/compare; instruct = anything else). If the same turn also resolves that new ask (delivered / refused / parked), give the ask_opened a placeholder id (e.g. "new") and reference the SAME placeholder on the follow-up event.
- ask_superseded: the user abandoned an open ask by stating a new explicit ask of their own; emit this for the old ask_id alongside the ask_opened of the new one.
- ask_reprompted: the user restates an already-recorded ask (same ask_id). A re-prompt is evidence the ask is still unfinished, not a new ask; it reopens a refused ask.
- delivered: this turn the agent delivered the asked result of the asked kind for that ask_id.
- delivered_wrong: this turn the agent produced something for that ask_id, but of the wrong kind, type, or intent — a fail even when the agent reports the mismatch itself.
- refused: the agent correctly refused that ask as out-of-scope or infeasible, or surfaced a real blocker of it; this closes the ask unless the user re-prompts it later.
- parked: the agent parked to confirm before a gated write of that ask; nothing was delivered this turn.

Rules — apply exactly:
- The user owns the running intent: a message that states an explicit ask of its own supersedes the open ask.
- A bare confirmation or continuation ("yes", "go ahead", "do it with X", a parameter answer) is NOT a new ask. It continues the open ask and inherits that ask's kind/type/intent constraints. Never open a new ask for a confirmation, even one that names parameters or repeats a verb.
- When a turn delivers on a confirmed ask, compare the produced object's declared identity (intent / kind / purpose in the delivered-result summary) against the ORIGINAL ask's constraints: a mismatch is delivered_wrong, never delivered.
- A user message that corrects the kind/type/intent of a prior delivery means that prior delivery was wrong: emit delivered_wrong for the prior ask (if not already recorded) and ask_opened for the corrective ask.
- A correct refusal closes the ask. Do not carry a refused ask forward unless the user re-prompts it.
- When the evidence includes independent judge verdicts, they outweigh the agent's own claims: a delivery whose grounding or verification gate failed — asserted facts with no supporting observation in the run — is delivered_wrong, not delivered.
- If the user message contains no ask and the agent delivered nothing attributable to an open ask, return an empty transitions list."""


def _render_ledger(state: LedgerState, events: list[Any]) -> str:
    if not state.asks:
        return "(empty — no asks recorded yet)"
    lines = ["Asks:"]
    for ask in state.asks.values():
        bits = [f"{ask.ask_id} [{ask.kind}] status={ask.status}"]
        if ask.delivered_wrong:
            bits.append("delivered_wrong")
        if ask.parks:
            bits.append(f"parks={ask.parks}")
        if ask.reprompts:
            bits.append(f"reprompts={ask.reprompts}")
        lines.append(f"- {' '.join(bits)}: {ask.text[:300]}")
    lines.append("Event log:")
    for event in events[-40:]:
        lines.append(
            f"- {getattr(event, 'event_type', '')} {getattr(event, 'ask_id', '')}"
            f" — {str(getattr(event, 'rationale', '') or '')[:120]}"
        )
    return "\n".join(lines)


def _classifier_prompt(
    state: LedgerState, events: list[Any], user_message: str, result_summary: str
) -> str:
    return "\n\n".join(
        [
            _CLASSIFIER_RUBRIC,
            f"Ledger so far:\n{_render_ledger(state, events)}",
            f"This turn's user message:\n{(user_message or '(none)')[:_ASK_TEXT_CAP]}",
            f"Delivered result summary (evidence):\n{(result_summary or '(none)')[:4000]}",
            "Return the transitions for this turn.",
        ]
    )


def _next_ask_id(state: LedgerState) -> str:
    n = len(state.asks) + 1
    while f"a{n}" in state.asks:
        n += 1
    return f"a{n}"


def _validated_transitions(parsed: TurnTransitions, state: LedgerState) -> list[dict[str, Any]]:
    """Structured-output validation; raises ``ValueError`` so the caller
    leaves the turn unrecorded rather than persisting a malformed chain."""
    known = set(state.asks)
    out: list[dict[str, Any]] = []
    shadow = LedgerState(asks=dict(state.asks))
    # The model may invent a placeholder id when it opens and resolves an ask
    # in the same turn; remember placeholder → assigned so the chain holds.
    alias: dict[str, str] = {}
    opened_this_turn: list[str] = []
    for t in parsed.transitions:
        event = (t.event or "").strip().lower()
        if event not in _EVENT_TYPES:
            raise ValueError(f"unknown event {t.event!r}")
        ask_id = (t.ask_id or "").strip()
        if event == ConversationEvent.EventType.ASK_OPENED:
            text = (t.ask_text or "").strip()
            if not text:
                raise ValueError("ask_opened without ask_text")
            kind = (t.ask_kind or "").strip().lower()
            if kind not in ASK_KINDS:
                raise ValueError(f"ask_opened with unknown kind {t.ask_kind!r}")
            placeholder = ask_id
            if not ask_id or ask_id in known or not re.fullmatch(r"a\d+", ask_id):
                ask_id = _next_ask_id(shadow)
            if placeholder and placeholder != ask_id:
                alias[placeholder] = ask_id
            shadow.asks[ask_id] = AskState(ask_id, text, kind)
            known.add(ask_id)
            opened_this_turn.append(ask_id)
        else:
            ask_id = alias.get(ask_id, ask_id)
            if ask_id not in known:
                # An unknown reference (empty, or a placeholder the model
                # invented mid-turn) on a turn that opened exactly one ask can
                # only mean that ask.
                if len(opened_this_turn) == 1:
                    ask_id = opened_this_turn[0]
                else:
                    raise ValueError(f"{event} references unknown ask {ask_id!r}")
        out.append(
            {
                "event_type": event,
                "ask_id": ask_id,
                "ask_text": (t.ask_text or "").strip()[:_ASK_TEXT_CAP],
                "ask_kind": (t.ask_kind or "").strip().lower(),
                "rationale": (t.rationale or "").strip()[:_RATIONALE_CAP],
            }
        )
    return out


def classify_turn(
    state: LedgerState,
    events: list[Any],
    *,
    user_message: str,
    result_summary: str,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Raises on parse/validation failure; the caller decides the fallback."""
    outcome = judging.invoke_judge(
        _classifier_prompt(state, events, user_message, result_summary),
        response_format=TurnTransitions,
        project_id=project_id,
        system_prompt=_CLASSIFIER_SYSTEM,
    )
    if outcome.parsed is None:
        raise ValueError("ledger classifier output failed to parse")
    return _validated_transitions(outcome.parsed, state)


def conversation_rows(project_id, conversation_id: str) -> list[TaskExecution]:
    return list(
        TaskExecution.objects.filter(
            project_id=project_id, conversation_id=conversation_id
        ).order_by("started_at", "created_at")
    )


def events_for_conversation(project_id, conversation_id: str) -> list[ConversationEvent]:
    return list(
        ConversationEvent.objects.filter(
            project_id=project_id, conversation_id=conversation_id
        ).order_by("order", "created_at")
    )


def _persist(
    execution: TaskExecution,
    transitions: list[dict[str, Any]],
    *,
    source: str,
    start_order: int,
) -> list[ConversationEvent]:
    rows = [
        ConversationEvent(
            project_id=execution.project_id,
            capability_id=execution.capability_id,
            conversation_id=execution.conversation_id,
            execution=execution,
            trace_id=execution.trace_id,
            event_type=t["event_type"],
            ask_id=t["ask_id"],
            ask_text=t.get("ask_text", ""),
            ask_kind=t.get("ask_kind", ""),
            source=source,
            rationale=t.get("rationale", ""),
            order=start_order + i,
        )
        for i, t in enumerate(transitions)
    ]
    return ConversationEvent.objects.bulk_create(rows)


def _turn_ask_text(execution: TaskExecution) -> str:
    return str((execution.user_intent or {}).get("text") or "")


def _ledger_opt_out(execution: TaskExecution) -> bool:
    """Mid-run units (``ledger_turn=False``) and ``scaffold`` intents (a
    prompt-embedded transcript dump) are harness context — classifying them
    mints phantom asks. A plain first user message stays classifiable."""
    if (execution.observed_route or {}).get("ledger_turn") is False:
        return True
    return (execution.user_intent or {}).get("source") == "scaffold"


def _turn_outcome(execution: TaskExecution) -> dict[str, Any]:
    for entry in execution.step_results or []:
        if (
            isinstance(entry, dict)
            and entry.get("role") == "outcome"
            and entry.get("outcome") == "scored"
        ):
            return entry
    return {}


def _row_result_summary(execution: TaskExecution) -> str:
    """Backfill evidence for a turn scored before the ledger existed: its
    persisted outcome verdict stands in for the live delivered-result summary."""
    outcome = _turn_outcome(execution)
    if not outcome:
        return ""
    bits = [
        f"{key}={outcome[key]}"
        for key in ("score", "passed", "delivery")
        if outcome.get(key) is not None
    ]
    rationale = str(outcome.get("rationale") or "").strip()
    if rationale:
        bits.append(f"outcome rationale: {rationale[:600]}")
    return "\n".join(bits)


def record_turn(
    execution: TaskExecution,
    *,
    user_message: str,
    result_summary: str,
) -> tuple[LedgerState, list[ConversationEvent]]:
    """Recorded events win; unrecorded prior turns are classified from their
    persisted row (``source=backfill``). A classifier failure leaves that turn
    unrecorded and must never sink scoring. Returns ``(entering_state, this_turn_events)``."""
    cid = (execution.conversation_id or "").strip()
    if not cid:
        return LedgerState(), []
    with _conversation_write_lock(execution.project_id, cid):
        return _record_turn_locked(
            execution, cid, user_message=user_message, result_summary=result_summary
        )


def _record_turn_locked(
    execution: TaskExecution,
    cid: str,
    *,
    user_message: str,
    result_summary: str,
) -> tuple[LedgerState, list[ConversationEvent]]:
    events = events_for_conversation(execution.project_id, cid)
    by_execution: dict[Any, list[ConversationEvent]] = {}
    for event in events:
        by_execution.setdefault(event.execution_id, []).append(event)
    order = max((e.order for e in events), default=-1) + 1

    applied: list[ConversationEvent] = []
    entering = LedgerState()
    turn_events: list[ConversationEvent] = []
    for row in conversation_rows(execution.project_id, cid):
        is_current = row.pk == execution.pk
        if is_current:
            entering = fold(applied)
        recorded = by_execution.get(row.pk)
        if recorded:
            applied.extend(recorded)
            if is_current:
                turn_events = recorded
                break
            continue
        if not is_current and _ledger_opt_out(row):
            continue
        if not is_current and not _turn_ask_text(row) and not _turn_outcome(row):
            continue
        try:
            transitions = classify_turn(
                fold(applied),
                applied,
                # An explicitly empty current-turn message means "no user turn"
                # (a run's terminal unit) — falling back to the persisted ask
                # would manufacture a phantom re-prompt.
                user_message=user_message if is_current else _turn_ask_text(row),
                result_summary=result_summary if is_current else _row_result_summary(row),
                project_id=str(execution.project_id),
            )
        except Exception:  # noqa: BLE001 — classifier failure must not sink scoring
            logger.exception(
                "ledger classifier failed for execution %s (turn stays unrecorded)", row.id
            )
            if is_current:
                break
            continue
        persisted = _persist(
            row,
            transitions,
            source=ConversationEvent.Source.CLASSIFIER
            if is_current
            else ConversationEvent.Source.BACKFILL,
            start_order=order,
        )
        order += len(persisted)
        applied.extend(persisted)
        if is_current:
            turn_events = persisted
            break
    return entering, turn_events


def ensure_backfill(rows: list[TaskExecution]) -> list[ConversationEvent]:
    """Bootstrap pre-ledger conversations through the one classifier
    (``source=backfill``); a failed turn is skipped and retried on the next pass."""
    if not rows:
        return []
    cid = rows[0].conversation_id
    with _conversation_write_lock(rows[0].project_id, cid):
        return _ensure_backfill_locked(rows, cid)


def _ensure_backfill_locked(rows: list[TaskExecution], cid: str) -> list[ConversationEvent]:
    events = events_for_conversation(rows[0].project_id, cid)
    by_execution: dict[Any, list[ConversationEvent]] = {}
    for event in events:
        by_execution.setdefault(event.execution_id, []).append(event)
    order = max((e.order for e in events), default=-1) + 1
    applied: list[ConversationEvent] = []
    for row in rows:
        recorded = by_execution.get(row.pk)
        if recorded:
            applied.extend(recorded)
            continue
        if _ledger_opt_out(row) or (not _turn_ask_text(row) and not _turn_outcome(row)):
            continue
        try:
            transitions = classify_turn(
                fold(applied),
                applied,
                user_message=_turn_ask_text(row),
                result_summary=_row_result_summary(row),
                project_id=str(row.project_id),
            )
        except Exception:  # noqa: BLE001 — backfill failure must not sink composition
            logger.exception(
                "ledger backfill classification failed for execution %s (skipped)", row.id
            )
            continue
        persisted = _persist(
            row, transitions, source=ConversationEvent.Source.BACKFILL, start_order=order
        )
        order += len(persisted)
        applied.extend(persisted)
    return applied


def outcome_ledger_verdict(execution: TaskExecution) -> dict[str, str]:
    """A wrong-kind delivery cannot pass, and only the FIRST park of an open ask
    passes — computed from ``parked`` events, not verdict rewrites."""
    events = events_for_conversation(execution.project_id, execution.conversation_id or "")
    parks_before: dict[str, int] = {}
    label = ""
    note = ""
    for event in events:
        mine = event.execution_id == execution.pk
        if event.event_type == ConversationEvent.EventType.PARKED:
            if mine:
                if parks_before.get(event.ask_id, 0) >= 1:
                    note = note or (
                        "Later confirm-park of an outstanding create; not the first gated write."
                    )
                    label = "outstanding"
                elif not label:
                    label = "first_park"
            parks_before[event.ask_id] = parks_before.get(event.ask_id, 0) + 1
        elif mine:
            if event.event_type == ConversationEvent.EventType.DELIVERED_WRONG:
                label = "delivered_wrong"
                note = "Wrong kind/intent of artifact cannot pass."
            elif event.event_type == ConversationEvent.EventType.DELIVERED:
                label = label or "delivered"
            elif event.event_type == ConversationEvent.EventType.REFUSED:
                label = label or "blocked"
    return {"delivery": label, "gate_note": note}
