import hashlib
import json
import logging
import shutil
import time
import uuid
from datetime import timedelta
from itertools import islice

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from overbae.models import (
    Cell,
    Dataset,
    DatasetPipeline,
    DatasetPipelineBinding,
    DatasetPipelinePackage,
    DatasetPipelineRun,
    DatasetPipelineRunner,
    Project,
)
from overbae.services import operational_progress
from overbae.services.datasets import (
    events,
    lifecycle,
    measure,
    operations,
    paths,
    pipeline_execution,
    pipeline_graph,
    pipeline_packages,
    preparation,
    review,
    rows,
    store,
)
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)


def digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise DatasetError("Provide finite JSON values.", code="invalid_json") from exc
    return hashlib.sha256(encoded).hexdigest()


def require_package(pipeline):
    if not pipeline.package_id:
        raise DatasetError(
            "This revision has no retained script package and is read-only. Author the "
            "transformation stages in Python, upload with overmind dataset pipeline-upload "
            "(overmind://dataset-upload), then save a new revision with its package ID.",
            code="pipeline_package_required",
        )


def save_pipeline(
    project,
    user,
    *,
    name,
    request_key,
    package,
    pipeline=None,
    expected_revision=None,
    derived_from=None,
    dataset=None,
):
    if expected_revision is not None and pipeline is None:
        raise DatasetError("expected_revision requires a pipeline family ID.", code="pipeline")
    if not package:
        raise DatasetError(
            "A retained script package is required. Author staged Python, upload with "
            "overmind dataset pipeline-upload (overmind://dataset-upload), then supply package.",
            code="pipeline_package_required",
        )
    bundle = DatasetPipelinePackage.objects.filter(project=project, pk=package).first()
    if bundle is None:
        raise DatasetError("Package not found in this project.", code="pipeline_package")
    steps = bundle.manifest["steps"]
    if dataset and dataset.project_id != project.pk:
        raise DatasetError("Dataset not found in this project.", code="dataset")
    fields = {
        **({"dataset": str(dataset.pk)} if dataset else {}),
        "name": name,
        "steps": steps,
        "package": str(package or ""),
        "pipeline": str(pipeline or ""),
        "expected_revision": expected_revision,
        "derived_from": str(derived_from or ""),
    }
    fingerprint = digest(fields)
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        existing = DatasetPipeline.objects.filter(project=project, request_key=request_key).first()
        if existing:
            if existing.fingerprint != fingerprint:
                raise DatasetError(
                    "This request key already identifies a different pipeline.", code="conflict"
                )
            return existing
        parent = (
            DatasetPipeline.objects.filter(project=project, family=pipeline)
            .order_by("-revision")
            .first()
            if pipeline
            else None
        )
        if pipeline and (parent is None or parent.revision != expected_revision):
            raise DatasetError(
                "Inspect the current revision before updating this pipeline.",
                code="revision_conflict",
            )
        origin = (
            DatasetPipeline.objects.filter(project=project, pk=derived_from).first()
            if derived_from
            else None
        )
        if derived_from and origin is None:
            raise DatasetError("Parent revision not found in this project.", code="pipeline")
        if pipeline and derived_from:
            raise DatasetError("Choose a new revision or a derived pipeline.", code="pipeline")
        saved = DatasetPipeline.objects.create(
            project=project,
            authoring_dataset_id=dataset.pk if dataset else None,
            name=name,
            request_key=request_key,
            steps=steps,
            package=bundle,
            fingerprint=fingerprint,
            created_by=user,
            family=parent.family if parent else uuid.uuid4(),
            revision=parent.revision + 1 if parent else 1,
            parent=parent,
            derived_from=origin,
        )
        if dataset:
            Dataset.objects.filter(pk=dataset.pk).update(preparation_pipeline=saved)
        return saved


def submit(
    dataset,
    user,
    *,
    source_cell,
    source_fingerprint,
    request_key,
    pipeline=None,
    imported_rows=None,
    artifact_cell=None,
    artifact_fingerprint="",
    name="",
    provenance="",
    mode="publish",
    preview_rows=100,
    parameters=None,
    binding=None,
    binding_checkpoint="",
):
    if (
        mode not in {"publish", "preview"}
        or type(preview_rows) is not int
        or not 1 <= preview_rows <= 1000
    ):
        raise DatasetError("Use publish or preview, with 1–1,000 preview rows.", code="pipeline")
    parameters = parameters or {}
    if not isinstance(parameters, dict) or len(json.dumps(parameters)) > 65536:
        raise DatasetError("Parameters must be a JSON object of at most 64 KiB.", code="parameters")
    if sum([pipeline is not None, imported_rows is not None, artifact_cell is not None]) != 1:
        raise DatasetError(
            "Provide exactly one pipeline, row collection or artifact cell.", code="source"
        )
    specification = {
        "name": name,
        "provenance": provenance,
        "artifact_cell": str(artifact_cell or ""),
        "artifact_fingerprint": artifact_fingerprint,
        "mode": mode,
        "preview_rows": preview_rows,
        "parameters": parameters,
        "binding_checkpoint": binding_checkpoint,
    }
    if not pipeline and not provenance.strip():
        raise DatasetError("Describe how the external output was produced.", code="provenance")
    if imported_rows is not None:
        if (
            not isinstance(imported_rows, list)
            or not 1 <= len(imported_rows) <= 2000
            or not all(isinstance(r, dict) for r in imported_rows)
        ):
            raise DatasetError("Import requires 1–2,000 row objects.", code="rows")
        digest(imported_rows)
        if len(json.dumps(imported_rows).encode()) > 4 * 1024 * 1024:
            raise DatasetError(
                "Import exceeds 4 MiB. Upload a file and supply its artifact cell instead.",
                code="rows",
            )
        specification["rows"] = imported_rows
    fingerprint = digest(
        {
            "source": str(source_cell),
            "fingerprint": source_fingerprint,
            "pipeline": str(pipeline or ""),
            "specification": specification,
        }
    )
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        existing = locked.pipeline_runs.filter(request_key=request_key).first()
        if existing:
            if existing.fingerprint != fingerprint:
                raise DatasetError(
                    "This request key already identifies different work.", code="conflict"
                )
            return existing
        source = (
            Cell.objects.select_related("dataset")
            .filter(dataset__project_id=dataset.project_id, pk=source_cell, state=Cell.State.OK)
            .first()
        )
        if source is None or source.fingerprint != source_fingerprint:
            raise DatasetError(
                "The source version or fingerprint does not match.", code="source_conflict"
            )
        recipe = (
            DatasetPipeline.objects.select_related("package")
            .filter(project_id=dataset.project_id, pk=pipeline)
            .first()
            if pipeline
            else None
        )
        if pipeline and recipe is None:
            raise DatasetError(
                "Select a pipeline revision belonging to this project.", code="pipeline"
            )
        if recipe:
            require_package(recipe)
            contract = recipe.package.manifest.get("parameters", {})
            if set(parameters) != set(contract):
                raise DatasetError(
                    "Supply exactly the package's declared parameters.", code="parameters"
                )
            try:
                list(pipeline_packages.check_rows([parameters], contract))
            except ValueError as exc:
                raise DatasetError(str(exc), code="parameters") from exc
        elif parameters:
            raise DatasetError(
                "External imports do not accept execution parameters.", code="parameters"
            )
        artifact = (
            Cell.objects.filter(
                pk=artifact_cell, dataset__project_id=dataset.project_id, state=Cell.State.OK
            ).first()
            if artifact_cell
            else None
        )
        if artifact_cell and (
            artifact is None
            or not artifact_fingerprint
            or artifact.fingerprint != artifact_fingerprint
        ):
            raise DatasetError(
                "The artifact version or fingerprint does not match this project.", code="artifact"
            )
        if locked.state not in (Dataset.State.IDLE, Dataset.State.ERROR):
            raise DatasetError("The dataset has an operation in progress.", code="busy")
        if recipe:
            Dataset.objects.filter(pk=locked.pk).update(preparation_pipeline=recipe)
        run = DatasetPipelineRun.objects.create(
            dataset=locked,
            pipeline=recipe,
            source=source,
            source_fingerprint=source_fingerprint,
            artifact=artifact,
            artifact_fingerprint=artifact_fingerprint,
            request_key=request_key,
            fingerprint=fingerprint,
            specification=specification,
            created_by=user,
            mode=mode,
            binding=binding,
            lease_until=timezone.now() + timedelta(hours=24),
            result={
                "stage": "waiting_for_runner" if recipe else "queued",
                "source_rows": source.rows,
                "source_columns": source.columns,
                "revision_fingerprint": recipe.fingerprint if recipe else None,
            },
        )
        Dataset.objects.filter(pk=locked.pk).update(
            state=Dataset.State.RUNNING,
            error="",
            active=locked.active_cell,
            updated_at=timezone.now(),
        )
        # Importing the worker here breaks the service/task dependency cycle.
        from overbae.tasks.datasets import execute_pipeline

        if not recipe:
            transaction.on_commit(lambda: _enqueue(run, execute_pipeline))
    run.refresh_from_db()
    return run


def _enqueue(run, task):
    try:
        task.delay(str(run.pk))
    except Exception:
        logger.exception("Could not queue pipeline run %s", run.pk)
        fail(run.pk, "The work could not be queued. Submit a new request key to retry.")


def execute(run_id, *, script_executor=None):
    candidate = (
        DatasetPipelineRun.objects.select_related("pipeline__package").filter(pk=run_id).first()
    )
    if candidate is None or candidate.state != "queued":
        return
    if candidate.pipeline_id:
        try:
            require_package(candidate.pipeline)
        except DatasetError as exc:
            fail(candidate.pk, exc.detail)
            return
        if script_executor is None:
            return
    seconds = 1800
    if candidate.pipeline_id and candidate.pipeline.package_id:
        seconds = (
            len(candidate.pipeline.steps)
            * (candidate.pipeline.package.manifest.get("limits", {}).get("seconds", 300) + 120)
            + 600
        )
    claimed = DatasetPipelineRun.objects.filter(pk=run_id, state="queued").update(
        state="running",
        updated_at=timezone.now(),
        lease_until=timezone.now() + timedelta(seconds=seconds),
    )
    if not claimed:
        return
    run = DatasetPipelineRun.objects.select_related(
        "dataset__capability", "pipeline__package", "source", "artifact", "created_by", "binding"
    ).get(pk=run_id)
    started = checkpoint = time.monotonic()
    started_at = timezone.now().isoformat()
    stage_started_at = started_at
    stage = None
    stage_seconds = {}
    receipts = []

    def progress(next_stage, *, heartbeat=False):
        nonlocal checkpoint, stage, stage_started_at
        now = time.monotonic()
        if stage is not None:
            stage_seconds[stage] = round(stage_seconds.get(stage, 0) + now - checkpoint, 3)
        if next_stage != stage:
            stage_started_at = timezone.now().isoformat()
        checkpoint, stage = now, next_stage
        run.result = {
            "stage": stage,
            "source_rows": run.source.rows,
            "started_at": started_at,
            "stage_started_at": stage_started_at,
            "stage_seconds": dict(stage_seconds),
            "seconds": round(now - started, 3),
            "steps": receipts,
            "mode": run.mode,
            "attempt": str(run.attempt),
            "revision_fingerprint": run.pipeline.fingerprint if run.pipeline_id else None,
            **(
                {"output_rows": receipts[-1]["output_rows"]}
                if receipts and "output_rows" in receipts[-1]
                else {}
            ),
        }
        changed = DatasetPipelineRun.objects.filter(
            pk=run.pk, state="running", attempt=run.attempt
        ).update(
            result=run.result,
            updated_at=timezone.now(),
            lease_until=timezone.now() + timedelta(seconds=seconds),
        )
        if changed:
            operational_progress.record(
                run.dataset.project_id,
                "dataset_pipeline",
                run.pk,
                run.attempt,
                stage=stage,
                status="complete" if stage == "completed" else "running",
                completed=len([r for r in receipts if r["state"] == "completed"]),
                total=len(run.pipeline.steps) if run.pipeline_id else 1,
                unit="steps",
                heartbeat_at=timezone.now() if heartbeat else None,
                facts={
                    "runtime": run.pipeline.package.manifest["runtime"]
                    if run.pipeline_id and run.pipeline.package_id
                    else "platform",
                    "total_rows": run.source.rows,
                    "attempt": str(run.attempt),
                },
            )
        return changed

    try:
        if not progress("verifying_source"):
            return
        if run.source.fingerprint != run.source_fingerprint:
            raise ValueError("The pinned source fingerprint changed before execution.")
        rows.verify(run.source)
        if run.pipeline_id:
            recipe = run.pipeline
            expected = digest(
                {
                    **(
                        {"dataset": str(recipe.authoring_dataset_id)}
                        if recipe.authoring_dataset_id
                        else {}
                    ),
                    "name": recipe.name,
                    "steps": recipe.steps,
                    "package": str(recipe.package_id or ""),
                    "pipeline": str(recipe.family) if recipe.parent_id else "",
                    "expected_revision": recipe.revision - 1 if recipe.parent_id else None,
                    "derived_from": str(recipe.derived_from_id or ""),
                }
            )
            if expected != recipe.fingerprint:
                raise ValueError("The saved revision fingerprint changed before execution.")
        source_path = paths.cell_path(run.source.dataset_id, run.source_id)
        package_files = (
            pipeline_packages.files(run.pipeline.package)
            if run.pipeline_id and run.pipeline.package_id
            else None
        )
        if run.artifact:
            if not progress("verifying_artifact"):
                return
            if run.artifact.fingerprint != run.artifact_fingerprint:
                raise ValueError("The artifact fingerprint changed before execution.")
            rows.verify(run.artifact)
        directory = paths.media_root() / "pipeline-runs" / str(run.pk)
        directory.mkdir(parents=True, exist_ok=True)
        input_path = source_path
        if run.mode == "preview":
            input_path = directory / "preview.parquet"
            store.write_rows(
                input_path,
                islice(store.iter_rows(source_path), run.specification["preview_rows"]),
                store.read_manifest(source_path),
            )
        steps = (
            run.pipeline.steps
            if run.pipeline_id
            else [{"name": run.specification["name"] or "External transformation"}]
        )
        flow = pipeline_graph.describe(steps, files=package_files)
        step_inputs = {"source": input_path}
        outputs = []
        for index, step in enumerate(steps):
            node = flow["nodes"][index]
            parent_paths = [step_inputs[identity] for identity in node["inputs"]]
            input_path = parent_paths[0]
            if len(parent_paths) > 1:
                schemas = [
                    {
                        column["name"]: column["type"]
                        for column in store.read_manifest(path)
                        if not column["name"].startswith("_overmind_")
                    }
                    for path in parent_paths
                    if store.row_count(path)
                ]
                if schemas and any(schema != schemas[0] for schema in schemas[1:]):
                    raise ValueError(
                        f"Step {node['id']} has incompatible columns or types across inputs. "
                        "Normalize each branch explicitly before merging; no values were coerced."
                    )
                input_path = directory / f"{index}-inputs.parquet"
                store.write_rows(
                    input_path,
                    (row for path in parent_paths for row in store.iter_rows(path)),
                    store.read_manifest(parent_paths[0])
                    if not any(store.row_count(path) for path in parent_paths)
                    else None,
                )
                with review.indexed_rows(input_path) as (_, tracked):
                    if not tracked:
                        raise ValueError(
                            f"Step {node['id']} has overlapping source_row identities across inputs. "
                            "Use disjoint branches; no rows were deduplicated or published."
                        )
            raw, output = directory / f"{index}-raw.parquet", directory / f"{uuid.uuid4()}.parquet"
            receipt = {
                "index": index,
                "name": step.get("name", step.get("operation", "Transformation")),
                "state": "running",
                "input_fingerprint": store.file_sha256(input_path),
                "input_rows": store.row_count(input_path),
                "started_at": timezone.now().isoformat(),
                "step_id": node["id"],
                "input_step": node["input"],
                "input_steps": node["inputs"],
                "inputs": [
                    {
                        "step": identity,
                        "fingerprint": store.file_sha256(path),
                        "rows": store.row_count(path),
                    }
                    for identity, path in zip(node["inputs"], parent_paths, strict=True)
                ],
            }
            declared = next(
                edge["condition"] for edge in flow["edges"] if edge["target"] == node["id"]
            )
            if declared:
                receipt["condition"] = declared["expression"]
                receipt["condition_evidence"] = declared
            receipts.append(receipt)
            if not progress(f"step_{index + 1}_executing"):
                return
            step_started = time.monotonic()
            manifest = store.read_manifest(input_path)
            if package_files is not None:
                missing = set(step.get("input_schema", {})) - {
                    column["name"] for column in manifest
                }
                if missing:
                    raise ValueError(
                        f"Missing required input columns: {', '.join(sorted(missing))}."
                    )
                for _ in pipeline_packages.check_rows(
                    store.iter_rows(input_path), step.get("input_schema", {})
                ):
                    pass
                records = pipeline_execution.execute_rows(
                    run,
                    index,
                    input_path,
                    directory,
                    step,
                    package_files,
                    lambda index=index: progress(f"step_{index + 1}_executing", heartbeat=True),
                    script_executor,
                )
                records = pipeline_packages.check_rows(
                    external_rows(records), step.get("output_schema", {})
                )
                script = package_files[step["entrypoint"]]
                manifest = None
            else:
                records = external_rows(
                    store.iter_rows(paths.cell_path(run.artifact.dataset_id, run.artifact_id))
                    if run.artifact_id
                    else run.specification["rows"]
                )
                script, manifest = "", None
            store.write_rows(raw, records, manifest)
            if step.get("consumer"):
                if not progress(f"step_{index + 1}_validating_consumer"):
                    return
                receipt["consumer_check"] = pipeline_execution.check_consumer(raw, step["consumer"])
            count = store.row_count(raw)
            receipt["output_rows"] = count
            checks = step.get("checks", {})
            check_results = {"passed": {}, "failed": {}, "deferred": {}}
            receipt["check_results"] = check_results
            for key, expected in checks.items():
                # Absolute population bounds cannot describe a bounded source preview.
                if run.mode == "preview" and key in {"min_rows", "max_rows"}:
                    check_results["deferred"][key] = expected
                    continue
                valid = (
                    count >= expected
                    if key == "min_rows"
                    else count <= expected
                    if key == "max_rows"
                    else not expected or count == receipt["input_rows"]
                )
                if valid:
                    check_results["passed"][key] = expected
                else:
                    check_results["failed"][key] = {
                        "expected": receipt["input_rows"] if key == "preserve_rows" else expected,
                        "actual": count,
                    }
            if not progress(f"step_{index + 1}_checking_rows"):
                return
            if check_results["failed"]:
                raise ValueError(
                    f"Step {node['id']} row checks failed: {json.dumps(check_results['failed'])}."
                )
            if not progress(f"step_{index + 1}_preserving_lineage"):
                return
            if count:
                review.preserve_file_provenance(
                    input_path,
                    raw,
                    output,
                    group_by=review.group_columns(run.dataset, run.source),
                    parent_paths=parent_paths,
                )
            else:
                empty_manifest = manifest or [
                    {
                        "name": name,
                        "type": kind
                        if kind in {"string", "integer", "number", "boolean"}
                        else "json",
                    }
                    for name, kind in {
                        **step.get("output_schema", {}),
                        store.SOURCE_ROW: "integer",
                    }.items()
                ]
                store.write_rows(output, [], empty_manifest)
            if not progress("measuring_impact"):
                return
            if not run.pipeline_id or package_files is not None:
                stripped = directory / f"{index}-unreviewed.parquet"
                store.write_rows(
                    stripped,
                    (
                        {key: value for key, value in row.items() if key != "human_reviewed"}
                        for row in store.iter_rows(output)
                    ),
                    [
                        column
                        for column in store.read_manifest(output)
                        if column["name"] != "human_reviewed"
                    ],
                )
                stripped.replace(output)
            impact = review.impact_files(input_path, output)
            receipt.update(
                state="completed",
                output_rows=count,
                output_fingerprint=store.file_sha256(output),
                seconds=round(time.monotonic() - step_started, 3),
                impact=impact,
                checks=checks,
                completed_at=timezone.now().isoformat(),
            )
            if run.mode == "preview":
                receipt["sample"] = bounded_sample(output)
            outputs.append((output, script, receipt))
            step_inputs[node["id"]] = output
        if run.mode == "preview":
            with transaction.atomic():
                Dataset.objects.select_for_update().get(pk=run.dataset_id)
                if not progress("completed"):
                    return
                if DatasetPipelineRun.objects.filter(
                    pk=run.pk, state="running", attempt=run.attempt
                ).update(
                    state="completed",
                    lease_until=None,
                    result=run.result,
                    updated_at=timezone.now(),
                ):
                    Dataset.objects.filter(pk=run.dataset_id).update(
                        state=Dataset.State.IDLE, updated_at=timezone.now()
                    )
            return
        if outputs:
            if not progress("publishing"):
                return
            with transaction.atomic():
                binding = None
                if run.binding_id:
                    binding = DatasetPipelineBinding.objects.select_for_update().get(
                        pk=run.binding_id
                    )
                dataset = Dataset.objects.select_for_update().get(pk=run.dataset_id)
                current = DatasetPipelineRun.objects.select_for_update().get(pk=run_id)
                if current.state != "running" or current.attempt != run.attempt:
                    return
                position = (dataset.cells.aggregate(n=Max("position"))["n"] or 0) + 1
                input_cells = {"source": str(run.source_id)}
                for index, (output, script, receipt) in enumerate(outputs):
                    receipt["input_cells"] = [
                        input_cells[identity] for identity in receipt["input_steps"]
                    ]
                    for parent in receipt["inputs"]:
                        parent["cell"] = input_cells[parent["step"]]
                    cell = Cell.objects.create(
                        id=output.stem,
                        dataset=dataset,
                        position=position + index,
                        title=receipt["name"],
                        input_fingerprint=receipt["input_fingerprint"],
                        script=script,
                        note=run.specification["provenance"][:512],
                        created_by=run.created_by,
                    )
                    destination = paths.cell_path(dataset.pk, cell.pk)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(output, destination)
                    report = {
                        "kind": "pipeline" if run.pipeline_id else "external",
                        "status": "accepted",
                        "source_cell": str(run.source_id),
                        "run": str(run.pk),
                        "step": index,
                        "pipeline": str(run.pipeline_id) if run.pipeline_id else None,
                        "execution": "isolated_container"
                        if package_files
                        else "external_attributed",
                        **receipt,
                        **receipt["impact"],
                    }
                    measure.frame(dataset, cell, destination, review=report)
                    receipt["output_cell"] = str(cell.pk)
                    input_cells[receipt["step_id"]] = str(cell.pk)
                progress("completed")
                DatasetPipelineRun.objects.filter(pk=run.pk).update(
                    state="completed",
                    output=cell,
                    result={
                        **run.result,
                        "rows": cell.rows,
                        "impact": receipts[-1]["impact"],
                        "seconds": round(time.monotonic() - started, 3),
                        "semantic_quality": "unmeasured",
                    },
                    lease_until=None,
                    updated_at=timezone.now(),
                )
                Dataset.objects.filter(pk=dataset.pk).update(
                    active=cell, state=Dataset.State.IDLE, error="", updated_at=timezone.now()
                )
                if binding:
                    binding.checkpoint = run.specification["binding_checkpoint"]
                    binding.error = ""
                    binding.save(update_fields=["checkpoint", "error", "updated_at"])
                transaction.on_commit(
                    lambda: events.publish(dataset.pk, {"type": "dataset_changed"})
                )
    except (ValueError, rows.RowStoreError, DatasetError) as exc:
        fail(run_id, str(exc)[:1000])
    except Exception:
        logger.exception("Pipeline run failed: %s", run_id)
        fail(
            run_id,
            "Transformation failed. Inspect the recipe and source columns; no version was published.",
        )


def bounded_sample(path):
    sample, size = [], 0
    for row in islice(store.iter_rows(path), 5):
        encoded = json.dumps(row, default=str)
        size += len(encoded.encode())
        if size > 16000:
            break
        sample.append(row)
    return sample


def external_rows(records):
    for record in records:
        yield {
            key: value
            for key, value in record.items()
            if key not in {"human_reviewed", "_overmind_provenance", "_overmind_document_id"}
        }


def cancel(dataset):
    with transaction.atomic():
        Dataset.objects.select_for_update().get(pk=dataset.pk)
        changed = dataset.pipeline_runs.filter(state__in=["queued", "running"]).update(
            state="cancelled", lease_until=None, updated_at=timezone.now()
        )
        if changed:
            Dataset.objects.filter(pk=dataset.pk).update(
                state=Dataset.State.IDLE, updated_at=timezone.now()
            )
        elif Dataset.objects.filter(pk=dataset.pk, state=Dataset.State.LANDING).exists():
            operations.cancel(dataset.pk)
        transaction.on_commit(lambda: events.publish(dataset.pk, {"type": "dataset_changed"}))
    return changed


def cancel_run(project, run_id):
    run = (
        DatasetPipelineRun.objects.select_related("dataset")
        .filter(dataset__project=project, pk=run_id)
        .first()
    )
    if run is None:
        raise DatasetError("Run not found in this project.", code="run_not_found")
    with transaction.atomic():
        Dataset.objects.select_for_update().get(pk=run.dataset_id)
        changed = DatasetPipelineRun.objects.filter(
            pk=run.pk, state__in=["queued", "running"]
        ).update(state="cancelled", lease_until=None, updated_at=timezone.now())
        if changed:
            Dataset.objects.filter(pk=run.dataset_id).update(
                state=Dataset.State.IDLE, updated_at=timezone.now()
            )
            operational_progress.record(
                project.pk,
                "dataset_pipeline",
                run.pk,
                run.attempt,
                stage="publication_cancelled",
                status="cancelled",
                facts={"retry_safe": False},
            )
    run.refresh_from_db()
    return run


def fail(run_id, error, *, expired_before=None):
    with transaction.atomic():
        run = DatasetPipelineRun.objects.select_related("dataset").get(pk=run_id)
        Dataset.objects.select_for_update().get(pk=run.dataset_id)
        eligible = DatasetPipelineRun.objects.filter(pk=run_id, state__in=["queued", "running"])
        if expired_before is not None:
            eligible = eligible.filter(lease_until__lt=expired_before)
        result = {**run.result, "stage": "failed"}
        for step in result.get("steps", []):
            if step.get("state") == "running":
                step.update(state="failed", error=error, completed_at=timezone.now().isoformat())
        if eligible.update(
            state="failed", error=error, result=result, lease_until=None, updated_at=timezone.now()
        ):
            operational_progress.record(
                run.dataset.project_id,
                "dataset_pipeline",
                run.pk,
                run.attempt,
                stage="failed",
                status="failed",
                facts={"retry_safe": not bool(run.container_id)},
            )
            Dataset.objects.filter(pk=run.dataset_id).update(
                state=Dataset.State.IDLE, error="", updated_at=timezone.now()
            )
            transaction.on_commit(
                lambda: events.publish(run.dataset_id, {"type": "dataset_changed"})
            )


def expire_runs():
    expired_before = timezone.now()
    for run_id in DatasetPipelineRun.objects.filter(
        state__in=["queued", "running"], lease_until__lt=expired_before
    ).values_list("pk", flat=True):
        fail(
            run_id,
            "Execution timed out. No output was published. Submit a new request key to retry.",
            expired_before=expired_before,
        )


@transaction.atomic
def update_dataset(dataset, **fields):
    dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
    if "name" in fields:
        lifecycle.rename(dataset, fields["name"])
    if "intent" in fields:
        lifecycle.set_intent(dataset, fields["intent"])
    if "capability" in fields:
        lifecycle.set_capability(dataset, fields["capability"])
    if "active" in fields:
        active = fields["active"]
        cell = dataset.cells.filter(pk=getattr(active, "pk", active)).first() if active else None
        if active and cell is None:
            raise DatasetError("Version not found in this dataset.", code="no_cell")
        lifecycle.set_active(dataset, cell)
    return dataset


def pipeline_record(pipeline):
    return {
        "id": str(pipeline.pk),
        "pipeline_id": str(pipeline.family),
        "revision": pipeline.revision,
        "parent": str(pipeline.parent_id) if pipeline.parent_id else None,
        "derived_from": str(pipeline.derived_from_id) if pipeline.derived_from_id else None,
        "package": str(pipeline.package_id) if pipeline.package_id else None,
        "executable": bool(pipeline.package_id),
        "runtime": pipeline.package.manifest["runtime"] if pipeline.package_id else "platform",
        "name": pipeline.name,
        "fingerprint": pipeline.fingerprint,
        "steps": pipeline.steps,
        "flow": pipeline_graph.describe(pipeline.steps),
        "created_at": pipeline.created_at.isoformat(),
    }


def run_record(run):
    terminal = run.state in {"completed", "failed", "cancelled"}
    return {
        "id": str(run.pk),
        "pipeline": str(run.pipeline_id) if run.pipeline_id else None,
        "pipeline_name": run.pipeline.name if run.pipeline_id else None,
        "revision": run.pipeline.revision if run.pipeline_id else None,
        "source_dataset": str(run.source.dataset_id),
        "source_cell": str(run.source_id),
        "source_fingerprint": run.source_fingerprint,
        "artifact_cell": str(run.artifact_id) if run.artifact_id else None,
        "artifact_fingerprint": run.artifact_fingerprint,
        "provenance": run.specification.get("provenance", ""),
        "execution": "isolated_container"
        if run.pipeline_id and run.pipeline.package_id
        else "platform"
        if run.pipeline_id
        else "external_attributed",
        "mode": run.mode,
        "parameters": run.specification.get("parameters", {}),
        "binding": str(run.binding_id) if run.binding_id else None,
        "operation": operational_progress.latest(
            run.dataset.project_id, "dataset_pipeline", run.pk
        ),
        "runner": runner_status() if run.pipeline_id and run.pipeline.package_id else None,
        "output_cell": str(run.output_id) if run.output_id else None,
        "state": run.state,
        "error": run.error,
        "result": run.result,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "completed_at": run.updated_at.isoformat() if terminal else None,
        "poll_after_seconds": None if terminal else 1,
        "queue_seconds": round(max(0, (timezone.now() - run.created_at).total_seconds()), 3)
        if run.state == "queued"
        else None,
    }


def describe(
    dataset=None,
    *,
    project=None,
    pipeline=None,
    pipeline_offset=0,
    run_offset=0,
    binding_offset=0,
    limit=20,
):
    # Bindings submit through this module; defer the reciprocal record import.
    from overbae.services.datasets.pipeline_bindings import binding_record

    project = project or dataset.project
    pipelines = project.pipelines.select_related("package").order_by("-created_at", "-pk")
    runs = (
        DatasetPipelineRun.objects.filter(dataset__project=project)
        .select_related("pipeline__package", "dataset", "source")
        .order_by("-created_at", "-pk")
    )
    bindings = project.datasetpipelinebinding_set.select_related("pipeline").order_by(
        "-created_at", "-pk"
    )
    if dataset:
        runs = runs.filter(dataset=dataset)
        bindings = bindings.filter(dataset=dataset)
    selected = None
    if pipeline:
        selected = pipelines.filter(pk=pipeline).first()
        if selected is None:
            raise DatasetError("Pipeline revision not found in this project.", code="pipeline")
        pipelines = pipelines.filter(family=selected.family)
        runs = runs.filter(pipeline=selected)
        bindings = bindings.filter(pipeline=selected)

    def page(query, offset):
        total = query.count()
        next_offset = offset + limit
        return {
            "limit": limit,
            "offset": offset,
            "total": total,
            "has_more": next_offset < total,
            "next_cursor": str(next_offset) if next_offset < total else None,
        }

    return {
        **({"pipeline": pipeline_record(selected)} if selected else {}),
        "current_pipeline": str(dataset.preparation_pipeline_id)
        if dataset and dataset.preparation_pipeline_id
        else None,
        "preparation": preparation.record(preparation.describe(dataset)) if dataset else None,
        "dataset": str(dataset.pk) if dataset else None,
        "pipelines": [
            pipeline_record(p) for p in pipelines[pipeline_offset : pipeline_offset + limit]
        ],
        "runs": [run_record(r) for r in runs[run_offset : run_offset + limit]],
        "pipeline_page": page(pipelines, pipeline_offset),
        "run_page": page(runs, run_offset),
        "bindings": [binding_record(b) for b in bindings[binding_offset : binding_offset + limit]],
        "binding_page": page(bindings, binding_offset),
        "runner": runner_status(),
        "authoring": {
            "package_required": True,
            "upload_resource": "overmind://dataset-upload",
            "execution": "isolated_container",
            "historical_package_free_revisions": "read_only",
        },
        "semantic_quality": "Externally authored reviews are not independently verified.",
    }


def runner_status():
    worker = DatasetPipelineRunner.objects.order_by("-heartbeat_at").first()
    recent = bool(worker and worker.heartbeat_at > timezone.now() - timedelta(seconds=45))
    return {
        "status": worker.status if recent else "unavailable",
        "images": settings.WORKSHOP_RUNTIME_IMAGES,
        "last_heartbeat_at": worker.heartbeat_at.isoformat() if worker else None,
        "network": "disabled",
        "package_max_bytes": pipeline_packages.MAX_BYTES,
        "action": "Run the dedicated workshop_runner with an approved immutable image."
        if not recent
        else None,
    }


def validate_pipeline(project, pipeline, *, source_cell=None, source_fingerprint=None):
    recipe = (
        DatasetPipeline.objects.select_related("package")
        .filter(project=project, pk=pipeline)
        .first()
    )
    if recipe is None:
        raise DatasetError("Pipeline revision not found in this project.", code="pipeline")
    require_package(recipe)
    pipeline_packages.files(recipe.package)
    flow = pipeline_graph.describe(recipe.steps)
    source = None
    if source_cell:
        source = Cell.objects.filter(
            pk=source_cell, dataset__project=project, state=Cell.State.OK
        ).first()
        if source is None or source.fingerprint != source_fingerprint:
            raise DatasetError(
                "The source version or fingerprint does not match.", code="source_conflict"
            )
    available = {c["name"] for c in source.columns} if source else set()
    missing = []
    if source:
        outputs = {"source": available}
        missing_names = set()
        for step, node in zip(recipe.steps, flow["nodes"], strict=True):
            available = set.intersection(*(set(outputs[identity]) for identity in node["inputs"]))
            # Output contracts guarantee named columns, but do not exclude other columns.
            if "source" in node["inputs"]:
                missing_names.update(set(step.get("input_schema", {})) - available)
            outputs[node["id"]] = set(step.get("output_schema", {}))
        missing = sorted(missing_names)
    warnings = []
    if flow["unconsumed_steps"]:
        warnings.append(
            {
                "code": "unconsumed_branches",
                "steps": flow["unconsumed_steps"],
                "message": "These branch outputs do not feed the final output. Merge intended observations or explicitly retain them as separate deliverables.",
            }
        )
    if recipe.package_id:
        for step, node in zip(recipe.steps, flow["nodes"], strict=True):
            if not {"source_row", "_overmind_parent_rows"} & step.get("output_schema", {}).keys():
                warnings.append(
                    {
                        "code": "lineage_contract_missing",
                        "step": node["id"],
                        "message": "Declare and emit source_row or _overmind_parent_rows. Runtime validates every parent.",
                    }
                )
    return {
        "valid": not missing,
        "warnings": warnings,
        "checks": {
            "min_rows": "publish_only",
            "max_rows": "publish_only",
            "preserve_rows": "preview_and_publish",
            "lineage": "preview_and_publish",
        },
        "execution": "not_run",
        "missing_columns": missing,
        "schema_coverage": "source_column_presence_only" if source else "not_checked",
        "semantic_compatibility": "unmeasured",
        "runtime": runner_status(),
        "pipeline": pipeline_record(recipe),
    }
