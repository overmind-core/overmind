"""Create, message, and run — the public boundary REST and MCP share."""

from __future__ import annotations

from django.db import transaction

from overbae.models import Dataset
from overbae.services.datasets.land import SPLIT_POSITIONS
from overbae.services.datasets.lifecycle import DatasetError, accept_proposal, enter_busy

_SOURCE_KEYS = ("traces", "rows", "upload_id")
_BUSY = (Dataset.State.LANDING, Dataset.State.DIAGNOSING, Dataset.State.RUNNING)


def _user_id(user) -> str | None:
    pk = getattr(user, "pk", None)
    return str(pk) if pk else None


def _check_source(source: dict) -> None:
    keys = [k for k in _SOURCE_KEYS if source.get(k) not in (None, "", [])]
    if len(keys) != 1:
        raise DatasetError(
            "Give exactly one source: traces, rows or upload_id.",
            code="source",
        )


def _new(project, user, name: str, source: dict, intent: str | None, capability) -> Dataset:
    dataset = Dataset.objects.create(
        project=project,
        capability=capability,
        name=(name or "").strip(),
        intent=intent or Dataset.Intent.PENDING,
        source_kind=Dataset.SourceKind.TRACES
        if source.get("traces") is not None
        else Dataset.SourceKind.FILE,
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
) -> Dataset:
    """Exactly one of source['traces'], source['rows'], source['upload_id']."""
    _check_source(source)
    dataset = _new(project, user, name, source, intent, capability)
    from overbae.tasks.datasets import land

    land.apply_async(
        kwargs={
            "dataset_id": str(dataset.id),
            "source": source,
            "user_id": _user_id(user),
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
    capability=None,
) -> tuple[Dataset, Dataset]:
    """One source, read once, landed as a train dataset and an eval dataset."""
    _check_source(source)
    if not 1 <= int(eval_percent) <= 99:
        raise DatasetError("eval_percent must be between 1 and 99.", code="split")
    if position not in SPLIT_POSITIONS:
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
            "split": {
                "eval_dataset_id": str(evaluation.id),
                "eval_percent": int(eval_percent),
                "position": position,
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
        if locked.state in _BUSY:
            raise DatasetError("The dataset is busy.", code=locked.state)
        if proposal is not None:
            if proposal.dataset_id != locked.id:
                raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
            accept_proposal(locked, proposal)
        enter_busy(locked.pk, Dataset.State.RUNNING, from_states=[locked.state])
        locked.state = Dataset.State.RUNNING
        locked.error = ""
    from overbae.tasks.datasets import run

    run.apply_async(kwargs={"dataset_id": str(dataset.id), "user_id": _user_id(user)})
    dataset.state = locked.state
    dataset.error = locked.error
    return dataset
