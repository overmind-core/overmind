"""Create, message, and run — the public boundary REST and MCP share."""

from __future__ import annotations

from django.db import transaction

from overbae.models import Dataset
from overbae.services.datasets.lifecycle import DatasetError, accept_proposal

_SOURCE_KEYS = ("traces", "rows", "upload_id")
_BUSY = (Dataset.State.LANDING, Dataset.State.DIAGNOSING, Dataset.State.RUNNING)


def _user_id(user) -> str | None:
    pk = getattr(user, "pk", None)
    return str(pk) if pk else None


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
    keys = [k for k in _SOURCE_KEYS if source.get(k) not in (None, "", [])]
    if len(keys) != 1:
        raise DatasetError(
            "Give exactly one source: traces, rows or upload_id.",
            code="source",
        )
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
    from overbae.tasks.datasets import land

    land.apply_async(
        kwargs={
            "dataset_id": str(dataset.id),
            "source": source,
            "user_id": _user_id(user),
        }
    )
    return dataset


def message_agent(dataset, user, message: str) -> Dataset:
    """Require idle. Atomically idle → diagnosing before queueing; DatasetError if busy."""
    n = Dataset.objects.filter(pk=dataset.pk, state=Dataset.State.IDLE).update(
        state=Dataset.State.DIAGNOSING
    )
    if n != 1:
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
        Dataset.objects.filter(pk=locked.pk).update(state=Dataset.State.RUNNING, error="")
        locked.state = Dataset.State.RUNNING
        locked.error = ""
    from overbae.tasks.datasets import run

    run.apply_async(kwargs={"dataset_id": str(dataset.id), "user_id": _user_id(user)})
    dataset.state = locked.state
    dataset.error = locked.error
    return dataset
