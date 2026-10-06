from django.db import transaction

from overbae.models import Cell, Dataset
from overbae.services.datasets import lifecycle, measure, paths, review, rows, store
from overbae.services.datasets.partition import contamination_keys


@transaction.atomic
def chunk_text(dataset, source, *, text_column, group_by, max_chars, plan, user=None):
    dataset = Dataset.objects.select_for_update(of=("self",)).get(pk=dataset.pk)
    if (
        not isinstance(max_chars, int)
        or isinstance(max_chars, bool)
        or not 1 <= max_chars <= 100_000
    ):
        raise ValueError("max_chars must be between 1 and 100000.")
    if not isinstance(group_by, list) or text_column in group_by:
        raise ValueError("Group by the document or page identities, excluding the text column.")
    tail = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
    if tail.pk != source.pk:
        raise ValueError("Chunk the current tail; derive a separate chain for another source.")
    rows.verify(source)
    path = paths.cell_path(dataset.pk, source.pk)
    with review.indexed_rows(path) as (_, tracked):
        if not tracked:
            raise ValueError(
                "The source has ambiguous row identities. Select an intact version before chunking."
            )
    largest = -1
    for row in store.iter_rows(path):
        identity = row.get(store.SOURCE_ROW)
        if isinstance(identity, bool) or not isinstance(identity, int) or identity < 0:
            raise ValueError("The source needs nonnegative integer row identities.")
        largest = max(largest, identity)
        if not isinstance(row.get(text_column), str) or any(key not in row for key in group_by):
            raise ValueError("Every row must contain the selected text and grouping columns.")
    cell = lifecycle.add_cell(dataset, title="Text chunks", script="", user=user)
    cell.preparation_plan = plan
    cell.save(update_fields=["preparation_plan"])

    def records():
        identity = largest + 1
        text, parents, keys, template, previous_group = "", [], set(), None, None

        def output():
            return {
                **{k: template[k] for k in group_by},
                text_column: text,
                store.SOURCE_ROW: identity,
                "_overmind_provenance": {
                    "kind": "chunk",
                    "parents": parents,
                    "source_content_keys": sorted(v for k, v in keys if k == "content"),
                    "source_group_keys": sorted([k, v] for k, v in keys if k != "content"),
                },
            }

        for row in store.iter_rows(path):
            group = store.json_dumps([row[key] for key in group_by])
            if template is not None and group != previous_group:
                yield output()
                identity += 1
                text, parents, keys, template = "", [], set(), None
            previous_group = group
            offset = 0
            value = row[text_column]
            # Empty evidence rows retain their own identity and parent reference.
            while offset < len(value) or not value and offset == 0:
                template = template or row
                end = min(len(value), offset + max_chars - len(text))
                text += value[offset:end]
                parents.append(
                    {
                        "cell": str(source.pk),
                        "fingerprint": source.fingerprint,
                        "row": row[store.SOURCE_ROW],
                        "column": text_column,
                        "start": offset,
                        "end": end,
                    }
                )
                keys.update(contamination_keys(row, group_by))
                offset = end
                if len(text) == max_chars or not value:
                    yield output()
                    identity += 1
                    text, parents, keys, template = "", [], set(), None
                if not value:
                    break
        if template is not None:
            yield output()

    output_path = paths.cell_path(dataset.pk, cell.pk)
    store.write_rows(output_path, records())
    cell.review = {
        "kind": "mechanical",
        "status": "accepted",
        "approval": "agent",
        "source_cell": str(source.pk),
        "source_fingerprint": source.fingerprint,
        "operation": "chunk_text",
        "parameters": {"text_column": text_column, "group_by": group_by, "max_chars": max_chars},
    }
    cell.save(update_fields=["review"])
    measure.frame(dataset, cell, output_path, input_fingerprint=source.fingerprint)
    lifecycle.set_active(dataset, cell)
    return cell
