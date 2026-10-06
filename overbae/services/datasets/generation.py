import hashlib
import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import timedelta
from itertools import islice

import pandas as pd
from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset, WorkshopRecord, WorkshopRun, WorkshopWorkItem
from overbae.services.datasets import contract, lifecycle, measure, paths, review, rows, store
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.examples import field_value
from overbae.services.datasets.partition import contamination_keys, content_key

MAX_EXAMPLES = 50
BATCH_ROWS = 8
LEASE_SECONDS = 900


class EvidenceValidationError(ValueError):
    def __init__(self, errors):
        self.errors = errors
        locations = ", ".join(
            f"examples[{issue['path'][1]}] seed {issue['seed_row']} column {issue['column']}"
            for issue in errors
        )
        super().__init__(
            f"Evidence quote not found: {locations}. No rows from this batch were saved."
        )

    def result(self):
        return {
            "ok": False,
            "error": str(self),
            "validation_errors": self.errors,
            "failure": {
                "code": "evidence_mismatch",
                "category": "input",
                "action": "Read the named seeds and columns. Correct the listed evidence bindings, preserve valid examples, then resubmit the entire batch.",
            },
        }


def request_context(run):
    return {
        "user_request": run.specification.get("user_request", run.dataset.brief),
        "preparation_request": run.plan.get("user_request", ""),
        "recipe": run.request,
        "plan": run.plan.get("specification", {}),
    }


def example_fingerprint(row):
    return content_key(
        {
            key: value
            for key, value in row.items()
            if key not in {"human_reviewed", "_overmind_document_id", "source_name"}
        },
        include_output=True,
    )


def directory(run):
    return paths.dataset_dir(run.dataset_id) / "workshop" / str(run.pk)


@contextmanager
def source_index(run):
    connection = sqlite3.connect(f"{(directory(run) / 'source.sqlite').as_uri()}?mode=ro", uri=True)
    try:
        yield connection
    finally:
        connection.close()


def build_index(run):
    root = directory(run)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "source.sqlite"
    with closing(sqlite3.connect(path)) as con, con:
        con.execute(
            "CREATE TABLE seeds (position INTEGER PRIMARY KEY, identity INTEGER UNIQUE NOT NULL, content TEXT, body TEXT, stratum TEXT, priority TEXT)"
        )
        con.execute("CREATE INDEX seed_content ON seeds(content)")
        columns = sorted(
            {
                column
                for family in run.plan.get("specification", {}).get("families", [])
                for column in family.get("coverage_columns", [])
            }
        )
        for index, row in enumerate(
            store.iter_rows(paths.cell_path(run.dataset_id, run.source_id))
        ):
            identity = row.get(store.SOURCE_ROW)
            if isinstance(identity, bool) or not isinstance(identity, int) or identity < 0:
                raise ValueError(
                    "Source records need nonnegative integer identities. Prepare the source before generation."
                )
            try:
                con.execute(
                    "INSERT INTO seeds VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        index,
                        identity,
                        example_fingerprint(row),
                        store.json_dumps(row),
                        store.json_dumps([field_value(row, column) for column in columns]),
                        hashlib.sha256(
                            f"73491:{run.source_fingerprint}:{identity}".encode()
                        ).hexdigest(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "Source identities repeat. Use chunk_text or an evidence-preserving transformation before generation."
                ) from exc
        count, largest = con.execute("SELECT count(*), max(identity) FROM seeds").fetchone()
        con.execute(
            "CREATE TABLE selection (position INTEGER PRIMARY KEY, identity INTEGER UNIQUE)"
        )
        con.execute(
            "INSERT INTO selection SELECT row_number() OVER (ORDER BY depth, stratum, priority)-1, identity FROM (SELECT identity, stratum, priority, row_number() OVER (PARTITION BY stratum ORDER BY priority) AS depth FROM seeds)"
        )
    if not count:
        raise ValueError("The source has no rows to derive examples from.")
    return count, largest


def target_count(run):
    return run.target_rows - (
        run.specification["source_rows"] if run.specification["mode"] == "augment" else 0
    )


def validate_source(run):
    source = run.source
    dataset = run.dataset
    if (
        source is None
        or source.dataset_id != dataset.pk
        or not source.ran
        or source.fingerprint != run.source_fingerprint
        or dataset.intent != run.specification["intent"]
        or context_fingerprint(dataset.capability) != run.specification["context_fingerprint"]
    ):
        raise ValueError(
            "The pinned source, intent or task context changed. Inspect and prepare a new generation request."
        )
    tail = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
    if tail.pk not in {run.source_id, run.output_id}:
        raise ValueError(
            "The dataset changed after generation started. Finish or cancel the saved run before changing its source."
        )


def get(run_id):
    return WorkshopRun.objects.select_related("dataset__capability", "source", "output").get(
        pk=run_id, kind="generation"
    )


@transaction.atomic
def start(
    dataset, source, *, target_rows, instruction, mode, plan, user=None, parent=None, run_id=None
):
    Dataset.objects.select_for_update(of=("self",)).get(pk=dataset.pk)
    if dataset.intent not in {"train", "eval"}:
        raise ValueError("Choose training or evaluation before generating examples.")
    if mode not in {"derive", "augment"} or mode == "derive" and not plan:
        raise ValueError("Derivation requires a source-bound preparation plan.")
    if not instruction.strip() or isinstance(target_rows, bool) or not isinstance(target_rows, int):
        raise ValueError("Set a generation instruction and an integer target_rows.")
    if target_rows <= (source.rows if mode == "augment" else 0):
        raise ValueError(
            "The target must exceed the source for augmentation, or be positive for derivation."
        )
    previous = (
        WorkshopRun.objects.filter(pk=run_id, dataset=dataset, kind="generation").first()
        if run_id
        else WorkshopRun.objects.filter(dataset=dataset, kind="generation")
        .order_by("-created_at")
        .first()
    )
    if run_id and previous is None:
        raise ValueError("No saved generation with that run id.")
    if previous and (run_id or previous.state not in {"complete", "cancelled"}):
        previous = get(previous.pk)
        same = (
            previous.target_rows == target_rows
            and previous.specification["mode"] == mode
            and previous.request == instruction
            and previous.plan == plan
            and (previous.source_id == source.pk or previous.output_id == source.pk)
        )
        if same:
            validate_source(previous)
            return previous
        if (
            previous.generated_rows or previous.items.exclude(state="pending").exists()
        ) and previous.failure.get("code") != "recipe_qualification":
            raise ValueError(
                "Saved generation has completed or unresolved work. Resume its exact source and target, or cancel it explicitly."
            )
        WorkshopRun.objects.filter(pk=previous.pk).update(
            state="cancelled", updated_at=timezone.now()
        )
    rows.verify(source)
    run = WorkshopRun.objects.create(
        dataset=dataset,
        parent=parent,
        kind="generation",
        source=source,
        source_fingerprint=source.fingerprint,
        request=instruction,
        target_rows=target_rows,
        plan=plan,
        created_by=user,
        specification={
            "mode": mode,
            "intent": dataset.intent,
            "context_fingerprint": context_fingerprint(dataset.capability),
            "user_request": dataset.brief or plan.get("user_request", ""),
        },
    )
    try:
        count, largest = build_index(run)
    except Exception:
        run.delete()
        raise
    run.specification.update(source_rows=count, maximum_identity=largest)
    run.save(update_fields=["specification"])
    return run


def seeds(run, *, positions=None, limit=10):
    with source_index(run) as con:
        if positions is None:
            selected = con.execute(
                "SELECT s.identity, s.body FROM selection a JOIN seeds s ON a.identity=s.identity ORDER BY a.position LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            selected = [
                con.execute(
                    "SELECT s.identity, s.body FROM selection a JOIN seeds s ON a.identity=s.identity WHERE a.position=?",
                    (p,),
                ).fetchone()
                for p in positions
            ]
    return [
        {
            "seed_row": identity,
            "row": {
                k: v
                for k, v in json.loads(body).items()
                if k not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}
            },
        }
        for identity, body in selected
    ]


def accepted_examples(run):
    batches = run.items.filter(kind="generation", state="complete").order_by("created_at", "pk")
    first, last = batches.first(), batches.last()
    if first is None:
        return []
    selected = [first] if first.pk == last.pk else [first, last]
    examples, size = [], 0
    for batch in selected:
        path = directory(run) / batch.artifact
        if store.file_sha256(path) != batch.fingerprint:
            raise ValueError("An accepted generation batch changed.")
        for row in islice(store.iter_rows(path), 8 // len(selected)):
            example = {
                key: value
                for key, value in row.items()
                if key not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}
            }
            encoded = len(store.json_dumps(example).encode())
            if size + encoded <= 20000:
                examples.append(example)
                size += encoded
    return examples


def describe(run_id):
    run = get(run_id)
    return {
        "id": str(run.pk),
        "state": run.state,
        "revision": run.revision,
        "mode": run.specification["mode"],
        "source_cell": str(run.source_id),
        "source_fingerprint": run.source_fingerprint,
        "source_rows": run.specification["source_rows"],
        "target_rows": run.target_rows,
        "generated_rows": run.generated_rows,
        "remaining_rows": max(0, target_count(run) - run.generated_rows),
        "batches": run.items.filter(kind="generation", state="complete").count(),
        "source_rows_without_examples": run.specification["source_rows"]
        - run.records.values("seed_row").distinct().count(),
        "published_cell": str(run.output_id) if run.output_id else None,
        "failure": run.failure,
        "usage": run.result.get("usage", {}),
        "qualification": run.result.get("qualification", {}),
        "recipe": run.request,
        "updated_at": run.updated_at.isoformat(),
    }


def validate_examples(run, examples, *, next_id):
    if not isinstance(examples, list) or not 1 <= len(examples) <= MAX_EXAMPLES:
        raise ValueError(f"Generate between 1 and {MAX_EXAMPLES} examples per batch.")
    group_by = [
        column
        for family in run.plan.get("specification", {}).get("families", [])
        for column in family.get("group_columns", [])
    ]
    output, digests, seed_ids, evidence_errors = [], [], [], []
    with source_index(run) as con:
        for offset, example in enumerate(examples):
            seed = example.get("seed_row") if isinstance(example, dict) else None
            if (
                isinstance(seed, bool)
                or not isinstance(seed, int)
                or not isinstance(example.get("row"), dict)
            ):
                raise ValueError("Each example needs a row object and an integer seed_row.")
            found = con.execute("SELECT body FROM seeds WHERE identity=?", (seed,)).fetchone()
            if found is None:
                raise ValueError("Every generated example must reference an existing seed_row.")
            source = json.loads(found[0])
            evidence = example.get("evidence", [])
            if run.specification["mode"] == "derive":
                if not isinstance(evidence, list) or not 1 <= len(evidence) <= 20:
                    raise ValueError(
                        "Derived examples need source evidence with a column and exact quote."
                    )
                for evidence_index, ref in enumerate(evidence):
                    if not isinstance(ref, dict) or set(ref) != {"column", "quote"}:
                        raise ValueError("Each evidence reference needs a column and exact quote.")
                    column, quote = ref["column"], ref["quote"]
                    if (
                        not isinstance(column, str)
                        or not isinstance(quote, str)
                        or not quote.strip()
                    ):
                        raise ValueError("Evidence columns and quotes must be nonempty strings.")
                    try:
                        value = field_value(source, column, decode_result=False)
                    except ValueError:
                        value = None
                    if not isinstance(value, str) or quote not in value:
                        evidence_errors.append(
                            {
                                "path": ["examples", offset, "evidence", evidence_index, "quote"],
                                "code": "quote_not_in_seed",
                                "seed_row": seed,
                                "column": column,
                                "source_cell": str(run.source_id),
                                "source_fingerprint": run.source_fingerprint,
                            }
                        )
            row = {
                k: v
                for k, v in example["row"].items()
                if k
                not in {
                    store.SOURCE_ROW,
                    review.PROVENANCE_COLUMN,
                    "trace_id",
                    "source_trace_id",
                    "conversation_id",
                }
            }
            row["human_reviewed"] = False
            if source.get("_overmind_document_id"):
                row["_overmind_document_id"] = source["_overmind_document_id"]
                if "source_name" in source:
                    row["source_name"] = source["source_name"]
            digest = example_fingerprint(row)
            if digest in digests or (
                run.specification["mode"] == "augment"
                and con.execute("SELECT 1 FROM seeds WHERE content=? LIMIT 1", (digest,)).fetchone()
            ):
                raise ValueError(
                    "A generated example duplicates the source or another generated example."
                )
            lineage = contamination_keys(source, group_by)
            row[store.SOURCE_ROW] = next_id + offset
            row[review.PROVENANCE_COLUMN] = {
                "kind": "synthetic",
                "capability": str(run.dataset.capability_id) if run.dataset.capability_id else None,
                "mode": run.specification["mode"],
                "evidence": evidence,
                "id": str(uuid.uuid5(run.pk, digest)),
                "seed_dataset": str(run.dataset_id),
                "seed_cell": str(run.source_id),
                "seed_fingerprint": run.source_fingerprint,
                "seed_row": seed,
                "seed_content_keys": sorted(v for k, v in lineage if k == "content"),
                "seed_group_keys": sorted([k, v] for k, v in lineage if k != "content"),
                "parents": [
                    {"cell": str(run.source_id), "fingerprint": run.source_fingerprint, "row": seed}
                ],
                "instruction": run.request,
                "context_fingerprint": run.specification["context_fingerprint"],
            }
            output.append(row)
            digests.append(digest)
            seed_ids.append(seed)
    if evidence_errors:
        raise EvidenceValidationError(evidence_errors)
    report = contract.measure(pd.DataFrame(output))[run.specification["intent"]]
    if not report["ok"]:
        raise ValueError(f"Generated examples are not format-valid: {report['reason']}")
    return output, digests, seed_ids


@transaction.atomic
def accept_batch(run_id, key, examples, *, owner=None, usage=None):
    run = get(run_id)
    Dataset.objects.select_for_update(of=("self",)).get(pk=run.dataset_id)
    run = (
        WorkshopRun.objects.select_for_update(of=("self",))
        .select_related("dataset__capability", "source", "output")
        .get(pk=run_id)
    )
    batch_hash = hashlib.sha256(store.json_dumps(examples).encode()).hexdigest()
    unit = run.items.filter(key=key).first()
    if unit and unit.state == "complete":
        if unit.request_fingerprint != batch_hash:
            raise ValueError("This batch key already contains different examples.")
        return unit.result
    if (
        run.output_id
        or run.state in {"complete", "cancelled"}
        or run.state == "paused"
        and owner is None
    ):
        raise ValueError("This generation is published, complete, cancelled or paused.")
    validate_source(run)
    if owner is not None and (
        unit is None
        or unit.owner != owner
        or not unit.lease_until
        or unit.lease_until < timezone.now()
    ):
        raise ValueError("Generation batch ownership changed or expired.")
    output, digests, seed_ids = validate_examples(
        run, examples, next_id=run.specification["maximum_identity"] + run.generated_rows + 1
    )
    if run.generated_rows + len(output) > target_count(run):
        raise ValueError("This batch exceeds the remaining generation target.")
    if run.records.filter(content_fingerprint__in=digests).exists():
        raise ValueError("A generated example duplicates another generated example.")
    unit = unit or WorkshopWorkItem.objects.create(run=run, key=key)
    relative = f"batches/{batch_hash}.parquet"
    artifact = directory(run) / relative
    store.write_rows(artifact, output)
    # The file is immutable; only a committed batch record makes it part of the result.
    run.generated_rows += len(output)
    run.revision += 1
    if unit.inputs.get("positions"):
        run.result = {
            **run.result,
            "seed_cursor": unit.inputs.get("seed_cursor", 0) + len(unit.inputs["positions"]),
            "unsupported_seeds": 0,
        }
    result = {
        "run_id": str(run.pk),
        "generated_rows": run.generated_rows,
        "remaining_rows": target_count(run) - run.generated_rows,
        "batch_id": str(unit.pk),
    }
    WorkshopRecord.objects.bulk_create(
        [
            WorkshopRecord(run=run, batch=unit, content_fingerprint=digest, seed_row=seed)
            for digest, seed in zip(digests, seed_ids, strict=True)
        ]
    )
    WorkshopWorkItem.objects.filter(pk=unit.pk).update(
        state="complete",
        artifact=relative,
        fingerprint=store.file_sha256(artifact),
        request_fingerprint=batch_hash,
        rows=len(output),
        result=result,
        usage=usage or unit.usage,
        updated_at=timezone.now(),
    )
    run.save(update_fields=["generated_rows", "revision", "result", "updated_at"])
    return result


@transaction.atomic
def publish(run_id, *, partial=False):
    run = get(run_id)
    dataset = (
        Dataset.objects.select_for_update(of=("self",))
        .select_related("capability")
        .get(pk=run.dataset_id)
    )
    run = (
        WorkshopRun.objects.select_for_update(of=("self",))
        .select_related("dataset__capability", "source", "output")
        .get(pk=run_id)
    )
    if run.output_id:
        return run.output
    if run.state == "cancelled" or run.state in {"paused", "blocked"} and not partial:
        raise ValueError("Resume a supported workflow before publishing its result.")
    if run.items.filter(state__in=["running", "submitting", "received", "unknown"]).exists():
        raise ValueError("Wait for the current batch to settle before publishing a partial result.")
    validate_source(run)
    if run.generated_rows != target_count(run) and not partial:
        raise ValueError(
            "Generation is incomplete; keep the saved batches or publish an explicit partial result."
        )
    if not run.generated_rows:
        raise ValueError("No generated examples are available to publish.")
    rows.verify(run.source)
    cell = lifecycle.add_cell(
        dataset,
        title="Derived examples" if run.specification["mode"] == "derive" else "Synthetic examples",
        script="",
        note=run.request[:512],
        user=run.created_by,
    )
    cell.preparation_plan = run.plan
    cell.save(update_fields=["preparation_plan"])

    def records():
        if run.specification["mode"] == "augment":
            yield from store.iter_rows(paths.cell_path(dataset.pk, run.source_id))
        for batch in run.items.filter(kind="generation", state="complete").order_by(
            "created_at", "id"
        ):
            path = directory(run) / batch.artifact
            if store.file_sha256(path) != batch.fingerprint:
                raise ValueError("A saved generation batch changed. Publication stopped.")
            yield from store.iter_rows(path)

    output = paths.cell_path(dataset.pk, cell.pk)
    store.write_rows(output, records())
    review.save_proposal(dataset, cell, run.source, output, kind="synthetic", note=run.request)
    cell.review.update(
        status="accepted",
        approval="generation",
        source_cell=str(run.source_id),
        generation_id=str(run.pk),
        mode=run.specification["mode"],
        source_rows=run.specification["source_rows"],
        generated_rows=run.generated_rows,
        target_rows=run.target_rows,
        source_rows_without_examples=run.specification["source_rows"]
        - run.records.values("seed_row").distinct().count(),
        instruction=run.request,
    )
    cell.save(update_fields=["review"])
    measure.frame(dataset, cell, output, input_fingerprint=run.source_fingerprint)
    lifecycle.set_active(dataset, cell)
    run.output = cell
    run.state = "partial" if run.generated_rows < target_count(run) else "complete"
    run.revision += 1
    run.save(update_fields=["output", "state", "revision", "updated_at"])
    return cell


@transaction.atomic
def claim(run_id, *, owner):
    run = (
        WorkshopRun.objects.select_for_update(of=("self",))
        .select_related("dataset__capability", "source", "output")
        .get(pk=run_id)
    )
    if run.state in {"cancelled", "paused", "complete", "blocked"} or run.output_id:
        return None
    if run.owner and run.owner != owner and run.lease_until and run.lease_until > timezone.now():
        return None
    validate_source(run)
    active = run.items.filter(
        kind="generation", state__in=["running", "submitting", "unknown", "received"]
    ).first()
    if active:
        if active.state != "unknown" and active.lease_until and active.lease_until > timezone.now():
            return None
        if active.state in {"submitting", "unknown"}:
            WorkshopRun.objects.filter(pk=run.pk).update(
                state="blocked",
                failure={
                    "code": "provider_outcome_unknown",
                    "detail": "Recover the saved provider receipt before resubmitting.",
                    "retryable": False,
                },
            )
            return None
        if active.state == "received":
            return None
        active.state = "pending"
        active.save(update_fields=["state"])
    remaining = target_count(run) - run.generated_rows
    if remaining <= 0:
        return None
    cursor = run.result.get("seed_cursor", run.generated_rows)
    unit, _ = run.items.get_or_create(
        key=f"batch:{run.generated_rows}:{cursor}",
        defaults={
            "inputs": {
                "rows": min(BATCH_ROWS, remaining),
                "offset": run.generated_rows,
                "seed_cursor": cursor,
                "accepted_examples": accepted_examples(run),
                "positions": [
                    (cursor + i) % run.specification["source_rows"]
                    for i in range(min(BATCH_ROWS, remaining))
                ],
            }
        },
    )
    if unit.state in {"complete", "rejected", "exhausted"} or unit.attempts >= 3:
        WorkshopRun.objects.filter(pk=run.pk).update(
            state="blocked",
            failure={
                "code": "retry_limit",
                "retryable": False,
                "detail": "This batch reached its retry limit. Inspect its receipts and revise the recipe.",
            },
        )
        return None
    unit.owner, unit.lease_until, unit.state = (
        owner,
        timezone.now() + timedelta(seconds=LEASE_SECONDS),
        "running",
    )
    unit.attempts += 1
    unit.save(update_fields=["owner", "lease_until", "state", "attempts", "updated_at"])
    WorkshopRun.objects.filter(pk=run.pk).update(
        state="running", owner=owner, lease_until=unit.lease_until
    )
    return unit


@transaction.atomic
def submitting(unit_id, *, owner, provider):
    unit = WorkshopWorkItem.objects.select_for_update().get(pk=unit_id)
    if unit.owner != owner or unit.state != "running":
        raise ValueError("Generation batch ownership changed.")
    attempts = dict(unit.provider.get("attempts", {}))
    attempt = attempts.get(str(unit.attempts), {})
    attempts[str(unit.attempts)] = {**attempt, "provider": provider, "state": "submitting"}
    unit.provider = {**unit.provider, "name": provider, "state": "submitting", "attempts": attempts}
    unit.state = "submitting"
    unit.save(update_fields=["provider", "state", "updated_at"])


def interrupted(unit_id, *, owner):
    unit = WorkshopWorkItem.objects.get(pk=unit_id)
    if unit.owner != owner or unit.state in {"complete", "exhausted", "skipped"}:
        return
    if WorkshopRun.objects.filter(pk=unit.run_id, state__in=["paused", "cancelled"]).exists():
        return
    if unit.state == "received":
        WorkshopRun.objects.filter(pk=unit.run_id).update(
            state="partial", failure={"code": "response_saved", "retryable": True}
        )
        return
    unknown = unit.state == "submitting"
    failure = {
        "code": "provider_outcome_unknown" if unknown else "interrupted",
        "detail": "Recover the provider receipt before resubmitting."
        if unknown
        else "Resume the saved batch.",
        "retryable": not unknown,
    }
    WorkshopWorkItem.objects.filter(pk=unit.pk, owner=owner).update(
        state="unknown" if unknown else "pending", failure=failure
    )
    WorkshopRun.objects.filter(pk=unit.run_id).update(
        state="blocked" if unknown else "partial", failure=failure
    )


@transaction.atomic
def skip_seeds(unit_id, *, owner, reason):
    unit = WorkshopWorkItem.objects.get(pk=unit_id)
    run = WorkshopRun.objects.select_for_update().get(pk=unit.run_id)
    unit = WorkshopWorkItem.objects.select_for_update().get(pk=unit_id, owner=owner)
    if unit.state in {"skipped", "exhausted"}:
        return describe(run.pk)
    if run.state in {"cancelled", "paused", "complete", "blocked"}:
        raise ValueError("This generation has stopped. Its completed work is preserved.")
    if not reason:
        raise ValueError("Record the source limitation.")
    skipped = len(unit.inputs["positions"])
    unsupported = run.result.get("unsupported_seeds", 0) + skipped
    exhausted = unsupported >= run.specification["source_rows"]
    unit.state = "exhausted" if exhausted else "skipped"
    unit.failure = {"code": "insufficient_source", "detail": reason}
    unit.save(update_fields=["state", "failure", "updated_at"])
    run.state = "partial" if exhausted else "queued"
    run.failure = {**unit.failure, "retryable": not exhausted}
    run.result = {
        **run.result,
        "seed_cursor": unit.inputs.get("seed_cursor", 0) + skipped,
        "unsupported_seeds": unsupported,
    }
    run.save(update_fields=["state", "failure", "result", "updated_at"])
    return describe(run.pk)


@transaction.atomic
def control(dataset, run_id, *, action, revision):
    Dataset.objects.select_for_update(of=("self",)).get(pk=dataset.pk)
    run = WorkshopRun.objects.select_for_update(of=("self",)).get(
        pk=run_id, dataset=dataset, kind="generation"
    )
    if run.revision != revision:
        raise ValueError("The workflow revision changed. Read its current state before continuing.")
    if action not in {"pause", "resume", "cancel", "publish_partial"}:
        raise ValueError("Choose pause, resume, cancel or publish_partial.")
    if action == "publish_partial":
        publish(run.pk, partial=True)
        return describe(run.pk)
    if run.output_id or run.state in {"complete", "cancelled"}:
        raise ValueError("This generation is already published or cancelled.")
    if action == "resume":
        if run.items.filter(state__in=["rejected", "exhausted"]).exists():
            raise ValueError(
                "The retry limit or source limit was reached. Inspect the saved result and revise the recipe."
            )
        if run.items.filter(state="unknown").exists():
            raise ValueError(
                "The provider outcome is unknown. Recover its receipt before resuming."
            )
        validate_source(get(run.pk))
        run.failure = {}
    run.state = {"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action]
    run.revision += 1
    run.save(update_fields=["state", "revision", "failure", "updated_at"])
    return describe(run.pk)


@transaction.atomic
def received(unit_id, *, owner, response, usage=None):
    unit = (
        WorkshopWorkItem.objects.select_for_update(of=("self",))
        .select_related("run")
        .get(pk=unit_id)
    )
    if unit.owner != owner:
        raise ValueError("Generation batch ownership changed.")
    attempts = dict(unit.provider.get("attempts", {}))
    attempt = attempts.get(str(unit.attempts), {})
    receipt = {"response": response, "received_at": timezone.now().isoformat(), "state": "received"}
    attempts[str(unit.attempts)] = {
        **attempt,
        **receipt,
        "usage": usage or attempt.get("usage", {}),
    }
    unit.provider = {
        **unit.provider,
        **receipt,
        "attempts": attempts,
        "meter_pending": True,
    }
    unit.usage = usage or unit.usage
    if unit.state not in {"complete", "exhausted", "rejected", "skipped"}:
        unit.state = "received"
    unit.save(update_fields=["provider", "usage", "state", "updated_at"])


@transaction.atomic
def rejected(unit_id, *, owner, detail):
    run_id = WorkshopWorkItem.objects.values_list("run_id", flat=True).get(pk=unit_id)
    WorkshopRun.objects.select_for_update().get(pk=run_id)
    unit = WorkshopWorkItem.objects.select_for_update(of=("self",)).get(pk=unit_id, owner=owner)
    if unit.state == "complete":
        return
    unit.failure = {
        "code": "invalid_examples",
        "detail": str(detail)[:600],
        "attempts": unit.attempts,
        "retryable": unit.attempts < 3,
    }
    unit.state = "pending" if unit.attempts < 3 else "rejected"
    unit.save(update_fields=["failure", "state", "updated_at"])
    WorkshopRun.objects.filter(pk=unit.run_id).exclude(state__in=["paused", "cancelled"]).update(
        state="queued" if unit.attempts < 3 else "blocked",
        failure=unit.failure,
        updated_at=timezone.now(),
    )


def record_usage(run_id):
    run = get(run_id)
    totals = {}
    unknown = 0
    for item in run.items.filter(kind__in=["generation", "qualification"]):
        receipts = item.provider.get("attempts", {})
        usages = (
            [receipt.get("usage", {}) for receipt in receipts.values()]
            if receipts
            else [item.usage]
        )
        for usage in usages:
            if not usage and item.attempts:
                unknown += 1
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    totals[key] = totals.get(key, 0) + value
    totals["unmeasured_attempts"] = unknown
    WorkshopRun.objects.filter(pk=run.pk).update(result={**run.result, "usage": totals})
    return totals
