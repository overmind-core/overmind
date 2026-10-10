from __future__ import annotations

import uuid

from django.db import transaction
from django.utils import timezone

from overbae.models import Dataset
from overbae.services.datasets.land import SPLIT_POSITIONS
from overbae.services.datasets.lifecycle import (
    DatasetError,
)

_SOURCE_KEYS = ("traces", "rows", "upload_id", "uploads", "llm_calls")
_BUSY = (Dataset.State.LANDING, Dataset.State.RUNNING)


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


def stage_dataset(
    project, user, name: str, source: dict, intent: str | None, capability, brief=""
) -> Dataset:
    dataset = Dataset.objects.create(
        project=project,
        capability=capability,
        name=(name or "").strip(),
        brief=brief.strip(),
        intent=intent or Dataset.Intent.PENDING,
        source_kind=(
            Dataset.SourceKind.LLM_CALLS
            if source.get("llm_calls") is not None
            else Dataset.SourceKind.TRACES
            if source.get("traces") is not None
            else Dataset.SourceKind.FILE
            if source
            else Dataset.SourceKind.PENDING
        ),
        state=Dataset.State.LANDING if source else Dataset.State.IDLE,
        created_by=user if getattr(user, "pk", None) else None,
    )
    dataset.refresh_from_db()
    return dataset


def create_dataset(
    *,
    project,
    user,
    name: str,
    source: dict | None = None,
    brief: str = "",
    intent: str | None = None,
    capability=None,
    infer_capability: bool = True,
) -> Dataset:
    """Land one trace selection, row collection, upload or ordered upload collection."""
    source = source or {}
    if not source and not brief.strip():
        raise DatasetError("Describe what you want to do or add source data.", code="source")
    if source:
        _check_source(source)
    if source.get("llm_calls") is not None and intent not in (
        Dataset.Intent.TRAIN,
        Dataset.Intent.EVAL,
    ):
        raise DatasetError("Choose train or eval.", code="intent")
    dataset = stage_dataset(project, user, name, source, intent, capability, brief)
    if not source:
        return dataset
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


def stage_attachment(dataset, source: dict) -> str:
    _check_source(source)
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if locked.state in _BUSY:
            raise DatasetError("The dataset is busy. Wait for it.", code=locked.state)
        request_id = str(uuid.uuid4())
        source_spec = {**locked.source_spec, "attachment_request": request_id}
        source_spec.pop("landing_progress", None)
        Dataset.objects.filter(pk=locked.pk).update(
            state=Dataset.State.LANDING,
            operation={},
            error="",
            source_spec=source_spec,
            updated_at=timezone.now(),
        )
    return request_id


def attach_source(dataset, user, source: dict) -> Dataset:
    with transaction.atomic():
        request_id = stage_attachment(dataset, source)
        from overbae.tasks.datasets import land

        transaction.on_commit(
            lambda: land.apply_async(
                kwargs={
                    "dataset_id": str(dataset.pk),
                    "source": source,
                    "user_id": _user_id(user),
                    "infer_capability": False,
                    "attachment_request": request_id,
                }
            )
        )
    dataset.refresh_from_db()
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
    brief: str = "",
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
    if len(name) > 249:
        raise DatasetError("Split names must be at most 249 characters.", code="split")
    with transaction.atomic():
        train = stage_dataset(
            project, user, f"{name} train", source, Dataset.Intent.TRAIN, capability, brief
        )
        evaluation = stage_dataset(
            project, user, f"{name} eval", source, Dataset.Intent.EVAL, capability, brief
        )
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
