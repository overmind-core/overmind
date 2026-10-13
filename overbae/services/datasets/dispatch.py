"""Create datasets and resume their imports — the boundary REST and MCP share."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from overbae.models import Dataset, DatasetImport
from overbae.services.datasets import files, imports, selection
from overbae.services.datasets.land import SPLIT_POSITIONS
from overbae.services.datasets.lifecycle import DatasetError, refuse_deleted_capability

_SOURCE_KEYS = ("traces", "rows", "upload_id", "uploads", "llm_calls")


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
    refuse_deleted_capability(capability)
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


@transaction.atomic
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
    imports.enqueue(
        dataset,
        {
            "dataset_id": str(dataset.id),
            "source": source,
            "user_id": _user_id(user),
            "infer_capability": infer_capability,
        },
    )
    return dataset


@transaction.atomic
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
    if source.get("rows") is not None:
        known = len(source["rows"])
    elif source.get("traces") is not None:
        known = selection.TraceSource.parse(source["traces"]).count(project.id)
    elif source.get("llm_calls") is None:
        uploads = source.get("uploads") or [source["upload_id"]]
        # Documents split by document, so one document cannot be split.
        lone = files.upload_filename(uploads[0]) if len(uploads) == 1 else ""
        if lone and not group_by and files.is_document(lone):
            raise DatasetError("Two documents are needed to split.", code="split")
        inspected = [files.inspection(i) for i in uploads]
        counts = [record["rows"] if record else None for record in inspected]
        known = None if None in counts else sum(counts)
    else:
        known = None
    if known is not None and known < 2:
        raise DatasetError("Two rows are needed to split.", code="split")
    name = (name or "").strip()
    with transaction.atomic():
        train = _new(project, user, f"{name} train", source, Dataset.Intent.TRAIN, capability)
        evaluation = _new(project, user, f"{name} eval", source, Dataset.Intent.EVAL, capability)
    imports.enqueue(
        train,
        {
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
        },
    )
    return train, evaluation


@transaction.atomic
def resume_import(dataset) -> Dataset:
    pending = (
        DatasetImport.objects.filter(
            Q(dataset=dataset) | Q(evaluation=dataset), state=DatasetImport.State.BLOCKED
        )
        .order_by("queued_at")
        .first()
    )
    if pending is None:
        raise DatasetError("The source import is not blocked.", code="import_not_blocked")
    imports.resume(pending.pk)
    dataset.refresh_from_db()
    return dataset
