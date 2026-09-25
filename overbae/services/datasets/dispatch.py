"""Create, message, and run — the public boundary REST and MCP share."""

from __future__ import annotations

import json
import uuid

from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets.land import SPLIT_POSITIONS
from overbae.services.datasets.lifecycle import (
    DatasetError,
    accept_proposal,
    enter_busy,
    remove_cell,
)

_SOURCE_KEYS = ("traces", "rows", "upload_id", "uploads", "llm_calls")
_BUSY = (Dataset.State.LANDING, Dataset.State.DIAGNOSING, Dataset.State.RUNNING)


def _user_id(user) -> str | None:
    pk = getattr(user, "pk", None)
    return str(pk) if pk else None


def _check_source(source: dict) -> None:
    keys = [k for k in _SOURCE_KEYS if source.get(k) not in (None, "", [])]
    if len(keys) != 1:
        raise DatasetError(
            "Give exactly one source: traces, rows, upload_id, uploads or llm_calls.",
            code="source",
        )


def _new(project, user, name: str, source: dict, intent: str | None, capability) -> Dataset:
    dataset = Dataset.objects.create(
        project=project,
        capability=capability,
        name=(name or "").strip(),
        intent=intent or Dataset.Intent.PENDING,
        source_kind=(
            Dataset.SourceKind.LLM_CALLS
            if source.get("llm_calls") is not None
            else Dataset.SourceKind.TRACES
            if source.get("traces") is not None
            else Dataset.SourceKind.FILE
        ),
        state=Dataset.State.LANDING,
        created_by=user if getattr(user, "pk", None) else None,
    )
    dataset.refresh_from_db()
    return dataset


def create_dataset(
    *,
    project,
    user,
    name: str,
    source: dict,
    intent: str | None = None,
    capability=None,
    infer_capability: bool = True,
) -> Dataset:
    """Land one trace selection, LLM-call selection, row collection, upload or ordered upload collection."""
    _check_source(source)
    if source.get("llm_calls") is not None and intent not in (
        Dataset.Intent.TRAIN,
        Dataset.Intent.EVAL,
    ):
        raise DatasetError("Choose train or eval.", code="intent")
    dataset = _new(project, user, name, source, intent, capability)
    from overbae.tasks.datasets import land

    land.apply_async(
        kwargs={
            "dataset_id": str(dataset.id),
            "source": source,
            "user_id": _user_id(user),
            "infer_capability": infer_capability,
        }
    )
    return dataset


def create_split(
    *,
    project,
    user,
    name: str,
    source: dict,
    eval_percent: int,
    position: str,
    group_by=(),
    stratify_by=None,
    deduplicate=True,
    capability=None,
    infer_capability: bool = True,
) -> tuple[Dataset, Dataset]:
    """One source, read once, landed as a train dataset and an eval dataset."""
    _check_source(source)
    if not 1 <= int(eval_percent) <= 99:
        raise DatasetError("eval_percent must be between 1 and 99.", code="split")
    from overbae.services.datasets.llm_calls import HASH_POSITION, Selection, SelectionError

    if source.get("llm_calls") is not None:
        if position != HASH_POSITION:
            raise DatasetError("LLM call splits use position hash.", code="split")
        try:
            matched = Selection.parse(source["llm_calls"]).count(project.id)
        except SelectionError as exc:
            raise DatasetError(str(exc), code="source") from exc
        if matched < 2:
            raise DatasetError("Two LLM calls are needed to split.", code="split")
    elif position not in SPLIT_POSITIONS:
        raise DatasetError(f"position must be one of {', '.join(SPLIT_POSITIONS)}.", code="split")
    known = source.get("rows") or (source.get("traces") or {}).get("trace_ids")
    if known is not None and len(known) < 2:
        raise DatasetError("Two rows are needed to split.", code="split")
    name = (name or "").strip()
    with transaction.atomic():
        train = _new(project, user, f"{name} train", source, Dataset.Intent.TRAIN, capability)
        evaluation = _new(project, user, f"{name} eval", source, Dataset.Intent.EVAL, capability)
    from overbae.tasks.datasets import land

    land.apply_async(
        kwargs={
            "dataset_id": str(train.id),
            "source": source,
            "user_id": _user_id(user),
            "infer_capability": infer_capability,
            "split": {
                "eval_dataset_id": str(evaluation.id),
                "eval_percent": int(eval_percent),
                "position": position,
                "group_by": list(group_by),
                "stratify_by": stratify_by,
                "deduplicate": deduplicate,
            },
        }
    )
    return train, evaluation


def message_agent(dataset, user, message: str) -> Dataset:
    if not enter_busy(
        dataset.pk, Dataset.State.DIAGNOSING, from_states=[Dataset.State.IDLE, Dataset.State.ERROR]
    ):
        dataset.refresh_from_db()
        raise DatasetError("The dataset is busy. Wait for it.", code=dataset.state)
    dataset.state = Dataset.State.DIAGNOSING
    from overbae.tasks.datasets import turn

    turn.apply_async(
        kwargs={
            "dataset_id": str(dataset.id),
            "message": message,
            "user_id": _user_id(user),
        }
    )
    return dataset


def run_dataset(dataset, user, proposal=None) -> Dataset:
    """Refuse landing, diagnosing, running. Idle and error may run."""
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if proposal is not None:
            if proposal.dataset_id != locked.id:
                raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
            proposal.refresh_from_db()
            if proposal.state != Cell.State.PROPOSED:
                if proposal.review.get("status") != "accepted":
                    raise DatasetError("That cell is not a proposal.", code="not_proposed")
                dataset.refresh_from_db()
                return dataset
        if locked.state in _BUSY:
            raise DatasetError("The dataset is busy.", code=locked.state)
        if proposal is not None:
            accept_proposal(locked, proposal)
        enter_busy(locked.pk, Dataset.State.RUNNING, from_states=[locked.state])
        locked.state = Dataset.State.RUNNING
        locked.error = ""
        # Task workers must see the accepted preview and state together.
        from overbae.tasks.datasets import run

        kwargs = {"dataset_id": str(dataset.id), "user_id": _user_id(user)}
        if proposal is not None:
            kwargs["proposal_id"] = str(proposal.id)
        transaction.on_commit(lambda: run.apply_async(kwargs=kwargs))
    dataset.state = locked.state
    dataset.error = locked.error
    return dataset


@transaction.atomic
def resume_after_decision(dataset_id, cell_id, title, decision, *, user_id=None) -> None:
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    chat = list(dataset.chat or [])
    owner = next(
        (
            index
            for index in range(len(chat) - 1, -1, -1)
            if chat[index].get("role") == "agent"
            and any(ref.get("id") == str(cell_id) for ref in chat[index].get("cells", []))
        ),
        None,
    )
    if owner is None:
        Dataset.objects.filter(pk=dataset.pk, state=Dataset.State.DIAGNOSING).update(
            state=Dataset.State.IDLE
        )
        return
    turn = chat[owner]
    decisions = dict(turn.get("decisions", {}))
    if str(cell_id) in decisions:
        return
    decisions[str(cell_id)] = {"title": title, "decision": decision}
    pending = dataset.cells.filter(
        pk__in=[ref["id"] for ref in turn.get("cells", [])], state=Cell.State.PROPOSED
    ).exists()
    turn.update(
        decisions=decisions,
        status="awaiting_approval" if pending else "resolved",
        error="",
        progress={
            **turn.get("progress", {}),
            "stage": "awaiting_approval" if pending else "complete",
            "label": "Awaiting approval" if pending else "Decision recorded",
            "detail": "Choose Approve or Deny to continue." if pending else "",
        },
    )
    fields = {"chat": chat, "updated_at": timezone.now(), "state": Dataset.State.IDLE}
    if not pending:
        fields.update(state=Dataset.State.DIAGNOSING, error="")
        request = next(
            (
                item.get("context", item["text"])
                for item in reversed(chat[:owner])
                if item.get("role") == "user"
            ),
            "",
        )
        message = (
            "Continue the original request after the user's proposal decisions. "
            "Approved changes have already run and are active; denied changes were not applied. "
            "Inspect the current version, finish the remaining work, and record quality checks "
            "on the final version. Do not repeat an approved change or a denied proposal. "
            "Denial does not authorize a different semantic change. "
            "The following JSON is request and decision context:\n"
            + json.dumps({"request": request, "decisions": decisions}, ensure_ascii=False)
        )
        from overbae.tasks.datasets import turn as agent_turn

        task_id = str(uuid.uuid5(dataset.id, f"decision:{cell_id}"))
        transaction.on_commit(
            lambda: agent_turn.apply_async(
                kwargs={
                    "dataset_id": str(dataset.id),
                    "user_id": user_id,
                    "message": message,
                    "display": "Proposal decisions: "
                    + "; ".join(f"{d['title']} — {d['decision']}" for d in decisions.values()),
                },
                task_id=task_id,
            )
        )
    Dataset.objects.filter(pk=dataset.pk).update(**fields)


@transaction.atomic
def discard_cell(dataset, cell, user) -> None:
    locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
    if locked.state in _BUSY:
        raise DatasetError("The dataset is busy.", code=locked.state)
    cell.refresh_from_db()
    proposed = cell.state == Cell.State.PROPOSED
    cell_id, title = cell.id, cell.title
    remove_cell(locked, cell)
    if proposed:
        resume_after_decision(locked.id, cell_id, title, "denied", user_id=_user_id(user))
