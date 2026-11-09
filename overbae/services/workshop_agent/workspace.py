import base64
import hashlib
import json
import os
import shlex
import shutil
import tempfile
import time
import zipfile
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath

from daytona import Daytona, DaytonaConfig, FileDownloadRequest, FileUpload

from overbae.models import Cell, WorkshopAction, WorkshopWorkspace
from overbae.services.datasets import context, paths, review, rows
from overbae.services.datasets.context import CONSUMERS
from overbae.services.workshop_agent import briefing, conversation, lifecycle, reference, transfer
from workshop_runtime import skills
from workshop_runtime.environment import identity
from workshop_runtime.presentation import CATALOG

MAX_FILE_BYTES = 10 * 1024 * 1024

ARCHIVE_SCRIPT = """import base64, hashlib, json, os, sys, tempfile, zipfile
names = json.loads(base64.b64decode(sys.argv[1]))
fd, path = tempfile.mkstemp(prefix='workshop-transfer-', suffix='.zip')
with os.fdopen(fd, 'w+b') as stream:
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for index, name in enumerate(names):
            archive.write(name, str(index))
    stream.seek(0)
    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
print(json.dumps({'path': path, 'sha256': checksum}))
"""


class RuntimeUnavailableError(ValueError):
    pass


@contextmanager
def download_replay_files(sandbox, names, *, timings=None):
    timings = timings if timings is not None else {}
    compression = time.monotonic()
    payload = base64.b64encode(json.dumps(names).encode()).decode()
    result = sandbox.process.exec(
        "/opt/overmind-runtime/venv/bin/python -c "
        + shlex.quote(ARCHIVE_SCRIPT)
        + " "
        + shlex.quote(payload),
        timeout=120,
    )
    if result.exit_code:
        raise ValueError("Replay artifact compression failed.")
    receipt = json.loads(result.result)
    timings["compress_seconds"] = time.monotonic() - compression
    path = receipt["path"]
    if not path.startswith("/tmp/workshop-transfer-") or PurePosixPath(
        path
    ).parent != PurePosixPath("/tmp"):
        raise ValueError("Unexpected replay transfer path.")
    try:
        with tempfile.TemporaryDirectory() as temporary:
            archive_path = Path(temporary) / "transfer.zip"
            downloading = time.monotonic()
            sandbox.fs.download_file(path, str(archive_path))
            timings["download_seconds"] = time.monotonic() - downloading
            timings["downloaded_bytes"] = archive_path.stat().st_size
            unpacking = time.monotonic()
            with archive_path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != receipt["sha256"]:
                    raise ValueError("Replay transfer checksum mismatch.")
            files = {}
            with zipfile.ZipFile(archive_path) as archive:
                if archive.namelist() != [str(i) for i in range(len(names))]:
                    raise ValueError("Replay transfer members do not match the requested files.")
                timings["expanded_bytes"] = sum(item.file_size for item in archive.infolist())
                for index, name in enumerate(names):
                    target = Path(temporary) / str(index)
                    with archive.open(str(index)) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    files[name] = target
            timings["verify_and_unpack_seconds"] = time.monotonic() - unpacking
            yield files
    finally:
        with suppress(Exception):
            sandbox.fs.delete_file(path)


def download_files(sandbox, names):
    files = sandbox.fs.download_files([FileDownloadRequest(source=name) for name in names])
    if (
        len(files) != len(names)
        or {item.source for item in files} != set(names)
        or any(item.error or item.result is None for item in files)
    ):
        raise ValueError("Workspace file batch is incomplete.")
    return {item.source: item.result for item in files}


def upload_references(sandbox, files):
    sandbox.fs.upload_files(
        [FileUpload(source=content, destination=name) for name, content in files.items()]
    )
    if download_files(sandbox, files) != files:
        raise ValueError("Runtime bootstrap checksum mismatch.")


def task_root(task):
    return f"/home/daytona/overmind/{task.pk}"


def runtime_call(task, payload=None, *, operation="request", reconcile=False, sandbox=None):
    sandbox = sandbox or attach(task, reconcile=reconcile)
    command = (
        "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.session --root "
        + shlex.quote(task_root(task))
        + " "
        + operation
    )
    if payload is not None:
        command += " --payload " + shlex.quote(
            base64.b64encode(json.dumps(payload).encode()).decode()
        )
    response = sandbox.process.exec(command, timeout=60)
    if response.exit_code != 0:
        raise ValueError(f"Workspace session request failed: {response.result[-2000:]}")
    return json.loads(response.result)


def path(task, value):
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise ValueError("Workspace paths must be relative to the task directory.")
    return f"/home/daytona/overmind/{task.pk}/work/{relative}"


def attach(task, *, reconcile=False):
    if not reconcile:
        lifecycle.authorise(task)
    workspace = WorkshopWorkspace.objects.get(project_id=task.project_id, enabled=True)
    client = Daytona(
        DaytonaConfig(
            api_key=os.environ["DAYTONA_API_KEY"],
            api_url="https://app.daytona.io/api",
            otel_enabled=False,
        )
    )
    sandbox = client.get(workspace.sandbox_id, request_timeout=30)
    if (
        sandbox.state != "started"
        or sandbox.auto_stop_interval != 0
        or sandbox.auto_pause_interval not in (None, 0)
        or sandbox.auto_delete_interval != -1
    ):
        raise ValueError("The persistent workspace is not ready.")
    if sandbox.public or not sandbox.network_block_all or sandbox.auto_destroy_at:
        raise ValueError("Workspace network or retention policy does not match.")
    return sandbox


def prepare(task, action):
    pinned = action.receipt.get("input")
    if pinned is None:
        prior = (
            task.actions.filter(name="prepare_workshop_environment", receipt__has_key="input")
            .order_by("sequence")
            .first()
        )
        pinned = prior.receipt["input"] if prior else None
    if pinned is None:
        cell = task.dataset.active_cell
        if cell is None or cell.state != "ok" or not cell.fingerprint:
            raise ValueError("A landed, fingerprinted source cell is required before reasoning.")
        pinned = {
            "dataset": str(task.dataset_id),
            "cell": str(cell.pk),
            "fingerprint": cell.fingerprint,
            "path": "data/input.parquet",
            "group_columns": review.group_columns(task.dataset, cell),
            "originals": [
                {
                    "source": item["id"],
                    "filename": item.get("filename"),
                    "path": f"data/originals/{item['id']}",
                }
                for item in task.dataset.source_spec.get("sources", [])
            ],
            "context": {
                "id": str(task.dataset_id),
                "name": task.dataset.name,
                "brief": task.dataset.brief,
                "intent": task.dataset.intent,
                "source_spec": task.dataset.source_spec,
                "source": {
                    "cell": str(cell.pk),
                    "rows": cell.rows,
                    "columns": cell.columns,
                    "stats": {
                        key: value
                        for key, value in cell.stats.items()
                        if key != "preparation_profile"
                    },
                },
                "capability_context": context.preparation_context(task.dataset.capability),
                "workshop": {
                    key: value
                    for key, value in context.workshop_context(
                        task.dataset, measure_missing=False
                    ).items()
                    if key != "consumers"
                },
            },
        }
        # Pin identities before network I/O; resuming bootstrap cannot select a newer active cell.
        pinned = json.loads(json.dumps(pinned, default=str))
    WorkshopAction.objects.filter(pk=action.pk).update(receipt={"input": pinned})
    cell = Cell.objects.get(pk=pinned["cell"], dataset_id=task.dataset_id, state="ok")
    if cell.fingerprint != pinned["fingerprint"]:
        raise ValueError("The pinned source fingerprint changed before workspace staging.")
    sandbox = attach(task)
    probe = sandbox.process.exec(
        "/opt/overmind-runtime/venv/bin/python -m workshop_runtime.environment", timeout=60
    )
    if probe.exit_code != 0:
        raise RuntimeUnavailableError(
            "The workspace needs the qualified Data Workshop runtime image."
        )
    report = json.loads(probe.result)
    expected = identity()
    if not report.get("ready") or report.get("identity") != expected:
        raise RuntimeUnavailableError(
            "The workspace runtime is out of date. Deploy the matching runtime before retrying."
        )
    guide = sandbox.fs.download_file("/opt/overmind-runtime/lib/workshop_runtime/GUIDE.md").decode()
    transfers = []
    staged = transfer.copy_verified(
        sandbox,
        paths.cell_path(cell.dataset_id, cell.pk),
        task_root(task) + "/inputs/input.parquet",
    )
    transfers.append(staged.pop("transfer"))
    input_manifest = {**pinned, **staged, "path": pinned["path"], "format": "parquet"}
    input_manifest.pop("context")
    input_manifest["originals"] = []
    for original in pinned["originals"]:
        source = paths.source_path(task.dataset_id, original["source"])
        receipt = transfer.copy_verified(
            sandbox, source, task_root(task) + "/inputs/originals/" + original["source"]
        )
        transfers.append(receipt.pop("transfer"))
        if receipt["sha256"] != original["source"]:
            raise ValueError("Retained source bytes do not match their identity.")
        input_manifest["originals"].append({**original, **receipt})
    input_manifest["context_datasets"] = []
    for source in task.context_sources:
        reference_cell = Cell.objects.get(
            pk=source["cell"],
            dataset_id=source["dataset"],
            dataset__project_id=task.project_id,
            state="ok",
        )
        if reference_cell.fingerprint != source["fingerprint"]:
            raise ValueError("A context dataset's pinned fingerprint changed.")
        rows.verify(reference_cell)
        directory = f"context-datasets/{source['dataset']}"
        receipt = transfer.copy_verified(
            sandbox,
            paths.cell_path(source["dataset"], source["cell"]),
            task_root(task) + f"/inputs/{directory}/input.parquet",
        )
        transfers.append(receipt.pop("transfer"))
        originals = []
        for original in source["source_spec"].get("sources", []):
            original_path = f"{directory}/originals/{original['id']}"
            retained = transfer.copy_verified(
                sandbox,
                paths.source_path(source["dataset"], original["id"]),
                task_root(task) + "/inputs/" + original_path,
            )
            if retained["sha256"] != original["id"]:
                raise ValueError("Context source bytes do not match their identity.")
            transfers.append(retained.pop("transfer"))
            originals.append(
                {
                    **retained,
                    "path": "data/" + original_path,
                    "filename": original.get("filename"),
                }
            )
        input_manifest["context_datasets"].append(
            {
                **source,
                **receipt,
                "path": f"data/{directory}/input.parquet",
                "originals": originals,
            }
        )
    measured, briefing_metrics = briefing.prepare(
        cell, project_id=task.project_id, runtime=expected
    )
    contract = reference.publication(expected)
    guide = (
        "# Task workspace\n\n"
        "The source is already staged at data/input.parquet. Retained original bytes and hashes "
        "are listed in input.json. Read context.json for the source and capability snapshot. "
        "input.json also lists any attached context_datasets, with their pinned versions, names, "
        "schemas and local read-only Parquet and original-file paths. Explore these directly "
        "alongside the working source. These are reference inputs, not an implicit concatenation "
        "or permission to edit those datasets. If a transformation uses reference values, retain "
        "the exact values it needs and their dataset/cell/fingerprint attribution in the package "
        "so clean replay remains self-contained. Output lineage still refers to the working source. "
        "Use these local files directly; no initial export or platform discovery is required. "
        "Load the staged Parquet with workshop_runtime.data.read_frame to decode platform JSON "
        "columns and preserve nullable integer precision. "
        "Replay scripts receive source_row and ordinary data columns; platform-managed keys "
        "beginning _overmind_ are omitted, except explicit _overmind_parent_rows declarations. "
        "If a transformation needs source metadata, retain an ordinary field explicitly bound "
        "to the inspected source evidence; never author platform provenance. "
        "In the runtime examples below, use data/input.parquet wherever source.parquet appears.\n\n"
        + guide
    )
    files = {
        "briefing.json": json.dumps(measured, ensure_ascii=False),
        **skills.files(CONSUMERS),
        "task.json": json.dumps({"task": str(task.pk), "objective": task.objective}),
        "native.json": json.dumps(
            {
                "runtime": expected,
                "package": {**contract["manifest"], "runtime": expected},
                "script_arguments": ["input.jsonl", "output.jsonl", "parameters.json"],
                "data_access": {
                    "load": "from workshop_runtime.data import read_frame, records; frame = read_frame('data/input.parquet')",
                    "records": "records(frame) returns Python records with decoded platform JSON columns and missing values as None, preserving nullable integer precision. Use it for strict JSON and local row validation; plain pandas conversions can introduce NaN or coerce nullable integers. For computed reports, use from workshop_runtime.data import json_value; json.dumps(json_value(report), allow_nan=False) converts NumPy counts/booleans, timestamps and missing values into JSON-safe primitives without changing their meaning.",
                    "file_search": "Use pathlib.Path.rglob, read_text and Python re to search the local workspace. ripgrep is not installed. Check shutil.which before using an optional command-line utility; Python file search needs no installation.",
                    "preflight_input": "from workshop_runtime.data import stage; stage('data/input.parquet', 'preflight.jsonl') produces the exact clean replay input. All _overmind_* metadata except _overmind_parent_rows is excluded. Package any needed original metadata as an ordinary source_row-bound audit mapping explicitly.",
                    "profile": "workshop_runtime.data.profile(path, target=None) measures columns, nulls and exact duplicates. A target must be evidence-derived. Inspect actual values and blank strings too; the profile does not establish semantics or near-duplicate separation.",
                    "verification_execution": "Verify the full population efficiently: build source_row-to-record and group-to-members/representative lookups once, then use constant-time lookups per record. Normalize/tokenize each text once and precompute token sets and lengths before pair comparisons, including in the independent verifier. Compute pairwise masks, reductions and similarity edges once outside row/pair loops; never rebuild a full pairwise matrix inside an individual pair check. For large data use bounded blocks or indexed candidate pairs instead of an unbounded all-pairs tensor. Preserve independent source-to-output and cross-role checks; do not replace them with sampling, shared transformation code or fewer assertions to save time.",
                    "numeric_boundaries": "Define numeric similarity thresholds, missing-value semantics and floating-point boundary tolerance before exploration and packaging. Use the same explicit boundary convention in independently implemented validation. For decimal measurements, account for representation error at inclusive boundaries with a documented small absolute tolerance or exact decimal comparison. Do not silently widen substantive thresholds or change matching semantics to make checks pass.",
                    "normalization": "Treat normalization as part of the matching definition, not a performance detail. Before grouping text, assess applicable Unicode/case, punctuation, unit/affix and numeric-template variants. Masking numeric substrings while retaining surrounding letters and collapsing whole digit-containing tokens can yield different neighbors; compare both when numeric templates matter. Reconcile plausible matching views by unioning their relationships before partitioning. More total edges do not prove that an earlier view is covered: independently check actual cross-role pairs for every adopted view. Preserve accepted match relationships when optimizing or repairing a recipe unless source evidence justifies a semantic change. Use exact candidate indexes with verified threshold/length bounds to accelerate these checks; do not substitute a cheaper normalization or sample for the same check.",
                    "references": "The requested skill returns the authoritative consumer contracts with this Python call. Do not also print consumers.json or taxonomy.json in that call; inspect additional local references only for a specific unresolved contract question. Full files remain available, and all source semantics still require examination.",
                },
                "receipts": {
                    "replay": "The replay result identifies itself with id; publication refers to it with replay. outputs lists retained JSONL paths; steps[].output_sha256 is a canonical record fingerprint, not the file-byte hash. workshop_runtime.replay.canonical(records) returns the SHA-256 hex digest string directly. Verification: from workshop_runtime.replay import canonical, read_rows; assert canonical(read_rows(path)) == step['output_sha256']. Do not hash or encode that returned digest again. This helper uses sorted-key, compact, UTF-8 JSON for the whole record array, ensure_ascii=False and allow_nan=False. assignments_sha256 and package_sha256 are file-byte SHA-256 digests. Do not guess serialization or compare the two different hash types.",
                    "saved": "Compacted observations explicitly report omissions; omitted data is not absent or verified. Recover needed evidence using the read tool on full_receipt, with pagination. Explicit reads preserve the requested page. The tool observation's full_receipt path contains {turn, tool, arguments, result}. Read json.load(open(full_receipt))['result'] for the actual result. The tool observation already contains those result fields directly.",
                    "publication": "The publisher independently verifies pinned source, output hashes, lineage, row counts and retained split assignments. verified_against_replay=true confirms publication matches the retained replay, including partition counts and assignment digest. After independently checking transformed values before publish, inspect this returned verification and finish directly; no additional Python call is needed to recheck the same receipt. This verifies publication fidelity, not semantic suitability or predictive quality. partition.members entries contain role and cell; cell contains id, dataset_id, rows and fingerprint.",
                },
                "workflow": "Write scripts and manifest.json in a ZIP; replay(package=path, parameters={}, partition=recipe) executes all declared steps in an isolated fresh process here. Inspect its actual output, then publish(replay=id, name=...). No manual transfers, pipeline IDs or remote jobs are needed. Do not use batch_rows; stream within a script for large data. Output paths are retained by the supervisor; the replay result includes sample rows and fingerprints.",
                "package_files": [".py", ".json", ".txt", ".md", ".lock"],
                "package_preflight": {
                    "imports": "from pathlib import Path\nfrom overbae.services.datasets.package_contract import inspect_bytes, check_rows\nfrom workshop_runtime.environment import identity",
                    "archive": "manifest, inventory, _ = inspect_bytes(Path(package_path).read_bytes(), runtime=identity())\nprint({'validated': True, 'files': [entry['path'] for entry in inventory]})",
                    "rows": "for _ in check_rows(records_to_check, column_contract):\n    pass",
                    "returns": "inspect_bytes returns (manifest, inventory, files), where files contains complete source code. check_rows lazily yields every validated record. Consume it without displaying the yielded records. Report only a compact check result, counts or filenames; do not print the archive tuple, full records or library source to confirm successful validation.",
                    "types": "Column contracts are flat name-to-type mappings, not JSON Schema. number/integer/string reject None; use any for nullable columns and preserve missingness. These checks establish structure, not semantic correctness.",
                },
                "column_types": [
                    "string",
                    "integer",
                    "number",
                    "boolean",
                    "object",
                    "array",
                    "null",
                    "any",
                ],
                "partition": {
                    "seed": 42,
                    "fractions": {"train": 0.7, "development": 0.15, "final": 0.15},
                    "group_by": [],
                    "stratify_by": None,
                    "holdouts": [],
                },
                "partition_scope": "Optional: omit partition for an unsplit dataset. A supplied recipe requires two to four roles with strictly positive fractions summing to one. Do not fabricate independent holdouts when the source contains only one indivisible group.",
                "partition_grouping": "group_by links rows sharing ANY listed field, transitively. For a composite tuple group, create one column representing that tuple and group_by that single column. Inherited source content and group identities are also preserved. Before packaging, measure label-blind near matches with tolerances justified by the data and intended use. Exact hashes and rounded buckets miss neighbors across bin boundaries; test distances and missing-value variants where applicable. Assess sensitivity to plausible nearby tolerances and missing-feature variants before fixing the grouping; one arbitrary threshold is not evidence of robust separation. Include plausible additional matching edges or explicitly qualify unresolved cross-role overlap. Form connected components for measured matches, then independently check the actual split against the same similarity definition. Report the definition and limits; no single similarity rule establishes universal independence.",
                "partition_holdout": {
                    "field": "column_name",
                    "values": ["observed value"],
                    "role": "final",
                },
                "replay_assignments": "assignments_path contains JSONL objects with row (zero-based position in the final output) and role (train/development/calibration/final). Read output[row] to recover its source_row identity; row is not a source identity. Inspect actual output and assignment records before writing validation code.",
                "lineage": "Keep source_row, or declare all _overmind_parent_rows from the immediate step input. Parent declarations are consumed; the next step receives assigned source_row identities. Keep original ancestry in a separate audit field if needed. Never author _overmind_provenance. The publisher validates and reconstructs lineage from pinned source rows.",
                "consumer": "Declare decision_train, decision_eval, chat_train or model_eval on the final step, as appropriate for the inferred intent. See consumers.json and taxonomy.json for schemas.",
            },
            indent=2,
        ),
        "input.json": json.dumps(input_manifest, indent=2),
        "presentation.json": json.dumps(
            {
                "instructions": "Use present to show real charts, tables, metrics, plots, forms and choices. Write a local JSON document with root, data and images. Nodes are {component: name, props: {...}}. Root is Stack. Nested children use the same node shape. data maps keys to {path: local JSON array file, cells: [exact source cell UUIDs], scope: explicit description of the measured population/sample/filter}. images uses the same reference shape for PNG/JPEG/WebP files. Compute values in Python and retain these files; never draw ASCII charts or invent measured values. Question/Choices IDs are unique; submissions arrive as user messages. They do not directly execute transformations. Ask only for consequential ambiguity, using evidence to set sensible options. Every source must be a frozen cell available to this task. Max 64 components, 2000 rows per table, 2 MiB total. Aggregate larger data explicitly. present validates before publication and returns errors for repair. Finish after presenting, without repeating the entire content in your conclusion.",
                "components": CATALOG,
                "example": {
                    "root": {
                        "component": "Stack",
                        "props": {
                            "children": [
                                {
                                    "component": "Chart",
                                    "props": {
                                        "data": "distribution",
                                        "kind": "horizontal-bar",
                                        "x": "label",
                                        "y": ["count"],
                                        "title": "Label distribution",
                                    },
                                }
                            ]
                        },
                    },
                    "data": {
                        "distribution": {
                            "path": "distribution.json",
                            "cells": [str(input_manifest["cell"])],
                            "scope": "Describe the exact rows used",
                        }
                    },
                },
            },
            indent=2,
        ),
        "context.json": json.dumps(pinned["context"], indent=2),
        "workflow.json": json.dumps(reference.section("workflow"), indent=2),
        "runtime.json": json.dumps(report),
        "contract.json": json.dumps(contract),
        "consumers.json": json.dumps(CONSUMERS, indent=2),
        "GUIDE.md": guide,
        "platform.json": json.dumps(reference.briefing(task), indent=2),
        "taxonomy.json": json.dumps(reference.section("taxonomy"), indent=2),
        "research.json": json.dumps(reference.section("research"), indent=2),
    }
    directory = path(task, "runtime.json").rsplit("/", 1)[0]
    setup = sandbox.process.exec(
        "mkdir -p "
        + " ".join(
            shlex.quote(str(PurePosixPath(task_root(task) + "/context/" + name).parent))
            for name in files
        )
        + " "
        + shlex.quote(directory),
        timeout=30,
    )
    if setup.exit_code != 0:
        raise ValueError("Workspace directory creation failed.")
    upload_references(
        sandbox,
        {task_root(task) + "/context/" + name: text.encode() for name, text in files.items()},
    )
    session = runtime_call(task, operation="ensure", sandbox=sandbox)
    return {
        "runtime": report,
        "runtime_contracts": {
            "native.json": json.loads(files["native.json"]),
            "skills": skills.catalog(),
        },
        "briefing_metrics": briefing_metrics,
        "staging_metrics": transfers,
        "source_briefing": briefing.initial_context(
            input_manifest, pinned["context"], json.loads(files["platform.json"]), measured
        ),
        "session": session,
        "input": input_manifest,
        "input_manifest_path": "input.json",
        "context_path": "context.json",
        "workflow_path": "workflow.json",
        "guide_path": "GUIDE.md",
        "contract_path": "contract.json",
        "consumer_contracts_path": "consumers.json",
        "platform_reference_path": "platform.json",
        "taxonomy_path": "taxonomy.json",
        "research_path": "research.json",
    }


def stage_chat_cells(task):
    # A follow-up's cell version is frozen before acceptance, then copied before delivery.
    references = {
        cell["id"]: cell
        for event in task.chat_events.filter(key__startswith="user:")
        for cell in event.message.get("cells", [])
    }
    pending = [
        cell
        for identity, cell in references.items()
        if not task.chat_events.filter(key=f"staged:{identity}").exists()
    ]
    if not pending:
        return
    sandbox = attach(task)
    for attached in pending:
        cell = Cell.objects.get(
            pk=attached["id"],
            dataset__project_id=task.project_id,
            fingerprint=attached["fingerprint"],
            state="ok",
        )
        destination = task_root(task) + f"/inputs/chat-cells/{cell.pk}.parquet"
        receipt = transfer.copy_verified(
            sandbox, paths.cell_path(cell.dataset_id, cell.pk), destination
        )
        result = sandbox.process.exec(
            f"chmod 444 {shlex.quote(destination)} && chmod 555 {shlex.quote(str(PurePosixPath(destination).parent))}",
            timeout=30,
        )
        if result.exit_code:
            raise ValueError("Chat cell could not be made read-only")
        conversation.emit(
            task,
            f"staged:{cell.pk}",
            {
                "id": f"staged:{cell.pk}",
                "role": "context",
                "text": "",
                "status": "completed",
                **receipt,
            },
        )
