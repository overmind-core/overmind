"""Session score is a fold over recorded ``ConversationEvent`` rows: any open
produce ask, or a wrong-kind delivery never subsequently delivered, zeroes the
session. Latest-pass never clears the slate.
"""

from __future__ import annotations

from typing import Any

from overbae.models import TaskExecution
from overbae.services.behaviour import ledger

_OUTSTANDING = 0.0


def compose_session_score(rows: list[TaskExecution], events: list[Any]) -> tuple[float | None, str]:
    """Ledger events decide open/closed; the execution score only supplies the
    magnitude once nothing is outstanding."""
    if not rows:
        return None, ""
    state = ledger.fold(events)
    if state.outstanding_produce():
        return _OUTSTANDING, ledger.task_state_dict(state)["reason"]

    delivered_produce_asks = {
        a.ask_id for a in state.asks.values() if a.kind == "produce" and a.status == "delivered"
    }
    landed_execution_id = None
    for event in events:
        if (
            str(getattr(event, "event_type", "")) == "delivered"
            and str(getattr(event, "ask_id", "")) in delivered_produce_asks
        ):
            landed_execution_id = getattr(event, "execution_id", None)
    if landed_execution_id is not None:
        landed_row = next((r for r in rows if r.pk == landed_execution_id), None)
        if landed_row is not None and landed_row.success_score is not None:
            return float(landed_row.success_score), "task landed; later local misses are turn-local"

    for row in reversed(rows):
        if row.success_score is not None:
            return (
                float(row.success_score),
                "latest composed execution score; running intent not outstanding",
            )
    return None, ""


def refresh_session_score(execution: TaskExecution) -> None:
    """Pre-ledger turns are backfilled through the ledger classifier — recorded
    once, so rescores are LLM-free."""
    cid = (execution.conversation_id or "").strip()
    if not cid:
        return
    rows = ledger.conversation_rows(execution.project_id, cid)
    events = ledger.ensure_backfill(rows)
    score, rationale = compose_session_score(rows, events)
    state_dict = ledger.task_state_dict(ledger.fold(events))
    for row in rows:
        route = dict(row.observed_route or {})
        route["task_state"] = state_dict
        TaskExecution.objects.filter(pk=row.pk).update(
            session_score=score, session_rationale=rationale, observed_route=route
        )
    execution.session_score = score
    execution.session_rationale = rationale
    route = dict(execution.observed_route or {})
    route["task_state"] = state_dict
    execution.observed_route = route
