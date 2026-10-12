from __future__ import annotations

import hashlib
import tempfile
import uuid
from pathlib import Path

from django.db import transaction

from overbae.models import Cell, Dataset
from overbae.services.datasets import contract, lifecycle, measure, paths, review, store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.partition import contamination_keys, content_key

MAX_EXAMPLES = 50


def validate_generation(dataset, cell, previous, target_rows):
    report = cell.review
    path = paths.cell_path(dataset.id, cell.id)
    if (
        cell.state != Cell.State.OK
        or cell.frozen
        or not previous.ran
        or str(previous.id) != report.get("source_cell")
        or dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first().pk
        != cell.pk
        or report.get("input_fingerprint") != previous.fingerprint
        or report.get("context_fingerprint") != context_fingerprint(dataset.capability)
        or report.get("intent") != dataset.intent
        or report.get("target_rows") != target_rows
        or not path.exists()
        or store.file_sha256(path) != report.get("output_fingerprint")
    ):
        raise ValueError(
            "The generated version or its source changed or was used. Start a new generation request."
        )


@transaction.atomic
def add(
    dataset,
    previous,
    examples: list[dict],
    *,
    instruction: str,
    generation_id: str,
    target_rows: int,
    user=None,
):
    # Serialise batches without locking the nullable capability outer join.
    dataset = (
        Dataset.objects.select_for_update(of=("self",))
        .select_related("capability")
        .get(pk=dataset.pk)
    )
    previous = dataset.cells.get(pk=previous.pk)
    if not isinstance(examples, list) or not 1 <= len(examples) <= MAX_EXAMPLES:
        raise ValueError(f"Generate between 1 and {MAX_EXAMPLES} examples per batch.")
    if dataset.intent not in {"train", "eval"}:
        raise ValueError("Choose training or evaluation before generating examples.")
    if not instruction.strip():
        raise ValueError("Describe the requested generation and intended coverage.")
    source_path = paths.cell_path(dataset.id, previous.id)
    source_count = store.row_count(source_path)
    if (
        isinstance(target_rows, bool)
        or not isinstance(target_rows, int)
        or target_rows <= source_count
    ):
        raise ValueError("The target must exceed the current row count.")
    source = store.Frame(source_path)
    keys = list(source.values(store.SOURCE_ROW)) if store.SOURCE_ROW in source else None
    if keys is None or len(set(keys)) != len(keys):
        raise ValueError("Generation needs unique source row identities.")
    positions = {int(key): i for i, key in enumerate(keys) if key is not None}
    context = context_fingerprint(dataset.capability)
    cell = dataset.cells.filter(review__generation_id=generation_id).first()
    combined = source_path
    batches = []
    if cell is not None:
        validate_generation(dataset, cell, previous, target_rows)
        combined = paths.cell_path(dataset.id, cell.id)
        batches = cell.review.get("batch_fingerprints", [])
    elif (
        not previous.ran
        or dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first().pk
        != previous.pk
    ):
        raise ValueError("The source changed during generation. Start a new request.")
    batch = hashlib.sha256(store.json_dumps(examples).encode()).hexdigest()
    if batch in batches:
        return cell
    combined_count = store.row_count(combined)
    if combined_count + len(examples) > target_rows:
        raise ValueError(f"Only {target_rows - combined_count} rows remain to reach the target.")
    next_id = max(k for k in store.Frame(combined).values(store.SOURCE_ROW) if k is not None) + 1
    for example in examples:
        if not isinstance(example, dict) or not isinstance(example.get("row"), dict):
            raise ValueError("Each example needs a row object and a seed_row.")
        seed = example.get("seed_row")
        if isinstance(seed, bool) or not isinstance(seed, int) or seed not in positions:
            raise ValueError("Every generated example must reference an existing seed_row.")
    seeds = sorted({example["seed_row"] for example in examples})
    seed_rows = store.frame_rows(source_path, [positions[seed] for seed in seeds])
    source_by_id = dict(zip(seeds, seed_rows.to_dict(orient="records"), strict=True))
    seen = {content_key(row, include_output=True) for row in store.records(combined)}
    generated = []
    group_by = dataset.source_spec.get("split", {}).get("group_by", [])
    for offset, example in enumerate(examples):
        seed = example["seed_row"]
        row = dict(example["row"])
        for key in (
            store.SOURCE_ROW,
            review.PROVENANCE_COLUMN,
            "trace_id",
            "source_trace_id",
            "conversation_id",
        ):
            row.pop(key, None)
        digest = content_key(row, include_output=True)
        if digest in seen:
            raise ValueError(
                "A generated example duplicates the source or another generated example."
            )
        seen.add(digest)
        row[store.SOURCE_ROW] = next_id + offset
        lineage = contamination_keys(source_by_id[seed], group_by)
        row[review.PROVENANCE_COLUMN] = {
            "kind": "synthetic",
            "id": str(uuid.uuid4()),
            "seed_dataset": str(dataset.id),
            "seed_cell": str(previous.id),
            "seed_fingerprint": previous.fingerprint,
            "seed_row": seed,
            "seed_content_keys": sorted(value for kind, value in lineage if kind == "content"),
            "seed_group_keys": sorted(
                [kind, value] for kind, value in lineage if kind != "content"
            ),
            "capability": str(dataset.capability_id) if dataset.capability_id else None,
            "context_fingerprint": context,
            "instruction": instruction[:4000],
        }
        generated.append(row)
    with tempfile.TemporaryDirectory(prefix="synthetic-") as scratch:
        added = Path(scratch) / "generated.parquet"
        store.write_rows(added, generated)
        report = contract.measure(store.Frame(added))[dataset.intent]
        if not report["ok"]:
            raise ValueError(f"Generated examples are not format-valid: {report['reason']}")
        output = Path(scratch) / "combined.parquet"
        store.write_extended(combined, generated, output)
        if cell is None:
            cell = lifecycle.add_cell(
                dataset,
                title="Synthetic examples",
                script="",
                note=instruction,
                user=user,
            )
        review.save_proposal(dataset, cell, previous, output, kind="synthetic", note=instruction)
    path = paths.cell_path(dataset.id, cell.id)
    total = combined_count + len(generated)
    generator = cell.review.get("generator", "workshop_agent")
    cell.review.update(
        status="accepted",
        approval="generation",
        source_cell=str(previous.id),
        instruction=instruction,
        generation_id=generation_id,
        target_rows=target_rows,
        batch_fingerprints=[*batches, batch],
        generated_examples=review.preview_examples(
            store.frame_rows(path, range(source_count, min(source_count + 5, total)))
        ),
        generated_rows=total - source_count,
        generator=generator,
    )
    cell.save(update_fields=["review"])
    measure.frame(
        dataset, cell, paths.cell_path(dataset.id, cell.id), input_fingerprint=previous.fingerprint
    )
    lifecycle.set_active(dataset, cell)
    return cell
