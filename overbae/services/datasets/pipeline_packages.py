import hashlib

from django.core.files.base import ContentFile
from django.db import transaction

from overbae.models import DatasetPipelinePackage, Project
from overbae.services.datasets import package_contract
from overbae.services.datasets.package_contract import MAX_BYTES, MAX_FILES, TYPES, error
from overbae.services.datasets.package_contract import check_rows as check_rows
from overbae.services.datasets.package_contract import schema as schema
from sandbox.packages import runtime_id


def validate_manifest(manifest, files, *, require_runtime=True):
    return package_contract.validate_manifest(
        manifest, files, runtime=runtime_id() if require_runtime else None
    )


def inspect_bytes(data, *, require_runtime=True):
    return package_contract.inspect_bytes(data, runtime=runtime_id() if require_runtime else None)


def save(project, user, upload, *, runtime=None):
    data = upload.read(MAX_BYTES + 1)
    manifest, inventory, _ = package_contract.inspect_bytes(data, runtime=runtime or runtime_id())
    checksum = hashlib.sha256(data).hexdigest()
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        existing = DatasetPipelinePackage.objects.filter(project=project, sha256=checksum).first()
        if existing:
            return existing
        package = DatasetPipelinePackage(
            project=project,
            sha256=checksum,
            size=len(data),
            manifest=manifest,
            inventory=inventory,
            created_by=user,
        )
        package.bundle.save(f"{package.pk}.zip", ContentFile(data), save=False)
        package.save()
    return package


def files(package):
    with package.bundle.open("rb") as source:
        data = source.read(MAX_BYTES + 1)
    if hashlib.sha256(data).hexdigest() != package.sha256:
        raise error("The retained package fingerprint does not match.")
    manifest, inventory, retained = inspect_bytes(data, require_runtime=False)
    if manifest != package.manifest or inventory != package.inventory:
        raise error("The retained package manifest or inventory does not match its bytes.")
    return retained


def record(package):
    return {
        "id": str(package.pk),
        "project": str(package.project_id),
        "sha256": package.sha256,
        "size": package.size,
        "manifest": package.manifest,
        "inventory": package.inventory,
        "created_at": package.created_at.isoformat(),
    }


def source_file(package, filename, *, offset=0, limit=8000):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 16000:
        raise error("Use a nonnegative character offset and limit 1–16000.")
    retained = files(package)
    if filename not in retained:
        raise error("That file is not in the retained package.")
    content = retained[filename]
    return {
        "path": filename,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
        "content": content[offset : offset + limit],
        "offset": offset,
        "total": len(content),
        "next_offset": offset + limit if offset + limit < len(content) else None,
    }


def authoring_contract():
    return {
        "required_for_transformations": True,
        "upload": "overmind dataset pipeline-upload DIRECTORY_OR_ZIP --project-id PROJECT --json",
        "download": "overmind dataset pipeline-download PACKAGE --project-id PROJECT --output pipeline.zip --json",
        "max_bytes": MAX_BYTES,
        "max_files": MAX_FILES,
        "manifest": {
            "version": 1,
            "runtime": "sha256:<approved immutable image ID>",
            "parameters": {},
            "steps": [
                {
                    "id": "transform",
                    "input": "source",
                    "name": "Transform",
                    "entrypoint": "transform.py",
                    "input_schema": {"text": "string", "source_row": "integer"},
                    "output_schema": {"text": "string", "source_row": "integer"},
                    "checks": {"preserve_rows": True},
                }
            ],
        },
        "column_types": sorted(TYPES),
        "analytics": "Published cells carry fingerprint-bound server analytics from a full-row scan. inspect_dataset and REST expose the same counts, baselines, warnings and not_checked risks; completed pipeline get_job includes final-output analytics. Read the exact selected consumer/partition cell before training handoff. Historical or stale reports are unmeasured: use explore_dataset to refresh without changing rows. Class imbalance and rare-class thresholds are advisory; soft targets are not converted to labels and a majority baseline is not model performance.",
        "consumer": {
            "values": ["decision_train", "decision_eval", "chat_train", "model_eval"],
            "scope": "Declare on the final step. Validates the complete output, including nested decision targets, in both preview and publish. Technical compatibility does not establish target meaning.",
        },
        "batch_rows": "Optional per-step integer 1–100000. The script runs as a separate bounded sandbox run for each consecutive row batch; outputs concatenate in source order and all steps publish atomically. Declare only for independent row transformations, never global sorting, grouping, splitting or deduplication. Choose a batch whose expanded JSONL input and output each fit half the declared scratch_mb. Each batch gets the package time limit; progress reports completed batches and rows. Global row checks run after concatenation.",
        "corrections": "Edit the same recipe family and run the corrected package from its original source cell. Successful publication replaces the displayed process; no repair-history view or disconnected prepared-v2 sources. Pinned training and evaluation inputs remain reproducible internally. Recipe ownership is project-scoped, not tied to the dataset or capability; reuse only when source and task semantics are compatible.",
        "source_boundary": "Upload original source bytes once with pinned upstream revision metadata. Register retained transformation code before full-data processing. Locally prepared derivatives must use lineage-bound external import, never masquerade as an independent source upload.",
        "script_arguments": ["input.jsonl", "output.jsonl", "parameters.json"],
        "inspectability": "Keep each step's meaningful logic in its entrypoint; reserve helpers for reusable functions. Cell transformation metadata binds exact run/revision/package/entrypoint and distinguishes platform execution from external imports. Inspect every retained file via the package resource (file, offset, limit in characters; next_offset until exhausted). Source reads verify the package and file checksums without executing code.",
        "starter_script": "import json, sys\n\nwith open(sys.argv[1], encoding='utf-8') as source, open(sys.argv[2], 'w', encoding='utf-8') as output:\n    for line in source:\n        row = json.loads(line)\n        # Author the requested transformation here; retain source_row.\n        output.write(json.dumps(row, ensure_ascii=False) + '\\n')\n",
        "checks": "preserve_rows and lineage apply in preview and publish. min_rows/max_rows are full-population assertions: preview records them as deferred, publication enforces them. Do not hardcode the first source's size in a reusable recipe unless that size is a real requirement.",
        "lineage": "The platform owns _overmind_provenance and removes it from executable rows; do not declare it in input_schema/output_schema or generate it. The publisher reconstructs provenance. Retain source_row or declare all contributing _overmind_parent_rows. Preserve duplicate observations and unknown meaning. Use input=source/earlier step or inputs=[distinct earlier IDs] to concatenate disjoint branches in declared order. Overlapping source_row identities fail rather than being deduplicated. Omitted input means the previous step.",
        "flow": "Declare stable step IDs. Script conditions cite expression and entrypoint line; scripts implement selection. Every step executes, including empty outputs. Flow exposes nodes, edges, terminal_steps and unconsumed_steps. Converge training branches into the last step's trainable output, retaining unresolved review metadata. A retained script with inputs consumes combined branches. Cycles, overlapping identities and job-level conditional skipping are unsupported.",
        "runtime": "Inspect inspect_dataset_workbench.runner for approved images and worker freshness. Dependencies are installed in the pinned operator-approved runtime; network and package installation are unavailable during execution. No credentials or host mounts enter script containers.",
        "next": "Retain with save_dataset_pipeline(dataset=..., package=...), validate, preview and inspect; publish explicitly. Reuse revisions on compatible sources, or save a derived_from variant. Save bindings paused and enable explicitly.",
    }
