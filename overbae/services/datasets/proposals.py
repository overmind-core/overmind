from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import paths, store
from overbae.services.datasets.context import context_fingerprint


def is_current(dataset: Dataset, cell: Cell, previous: Cell | None) -> bool:
    report = cell.review
    if (
        previous is None
        or not previous.ran
        or previous.fingerprint != report.get("input_fingerprint")
        or dataset.intent != report.get("intent")
        or context_fingerprint(dataset.capability) != report.get("context_fingerprint")
    ):
        return False
    path = paths.cell_path(dataset.id, cell.id)
    return path.exists() and store.file_sha256(path) == report.get("output_fingerprint")


@transaction.atomic
def retire_outdated(dataset: Dataset) -> None:
    locked = (
        Dataset.objects.select_related("capability")
        .select_for_update(of=("self",))
        .get(pk=dataset.pk)
    )
    chain = locked.chain
    previous = next((c for c in reversed(chain) if c.state != Cell.State.PROPOSED), None)
    # The draft cell exists briefly before its preview is saved.
    outdated = {
        str(cell.id): cell
        for cell in chain
        if cell.state == Cell.State.PROPOSED
        and cell.review
        and not is_current(locked, cell, previous)
    }
    if not outdated:
        return
    locked.cells.filter(pk__in=outdated).delete()
    remaining = [cell for cell in chain if str(cell.id) not in outdated]
    for position, cell in enumerate(remaining):
        if cell.position != position:
            Cell.objects.filter(pk=cell.pk).update(position=position)
    for cell in outdated.values():
        path = paths.cell_path(locked.id, cell.id)
        transaction.on_commit(lambda path=path: path.unlink(missing_ok=True))
    pending = {str(cell.id) for cell in remaining if cell.state == Cell.State.PROPOSED}
    chat = locked.chat or []
    for turn in chat:
        replaced = {ref["id"] for ref in turn.get("cells", [])} & outdated.keys()
        if not replaced:
            continue
        decisions = turn.setdefault("decisions", {})
        for cell_id in replaced:
            decisions[cell_id] = {"title": outdated[cell_id].title, "decision": "superseded"}
        if turn.get("status") == "awaiting_approval" and not any(
            ref["id"] in pending for ref in turn.get("cells", [])
        ):
            turn.update(
                status="resolved",
                progress={
                    **turn.get("progress", {}),
                    "stage": "complete",
                    "label": "Suggestions updated",
                    "detail": "",
                },
            )
    Dataset.objects.filter(pk=locked.pk).update(chat=chat, updated_at=timezone.now())
    dataset.chat = chat
