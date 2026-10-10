import json
import logging
from datetime import timedelta
from itertools import islice

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from overbae.models import Cell, Dataset, DatasetPipeline, DatasetPipelineBinding, Project
from overbae.services.datasets import land, measure, paths, selection, store, workbench
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)


def binding_record(binding):
    return {
        "id": str(binding.pk),
        "dataset": str(binding.dataset_id),
        "pipeline": str(binding.pipeline_id),
        "source_dataset": str(binding.source_dataset_id) if binding.source_dataset_id else None,
        "trace_source": binding.trace_source,
        "parameters": binding.parameters,
        "trigger": binding.trigger,
        "interval_seconds": binding.interval_seconds,
        "enabled": binding.enabled,
        "version": binding.version,
        "checkpoint": binding.checkpoint,
        "max_runs": binding.max_runs,
        "runs_started": binding.runs_started,
        "max_source_rows": binding.max_source_rows,
        "error": binding.error,
        "last_checked_at": binding.last_checked_at.isoformat() if binding.last_checked_at else None,
        "next_check_at": binding.next_check_at.isoformat() if binding.next_check_at else None,
        "execution_semantics": "full_snapshot_rebuild",
        "revision_adoption": "explicit_rebuild",
    }


def save(
    project,
    user,
    *,
    dataset,
    pipeline,
    request_key,
    source_dataset=None,
    trace_source=None,
    parameters=None,
    trigger="manual",
    interval_seconds=60,
    max_runs=1000,
    max_source_rows=1000000,
    binding=None,
    expected_version=None,
):
    if bool(source_dataset) == bool(trace_source):
        raise DatasetError("Choose a source dataset or a trace selection.", code="binding_source")
    if str(dataset) == str(source_dataset):
        raise DatasetError(
            "Source and destination must differ to prevent feedback loops.", code="binding_source"
        )
    if (
        trigger not in {"manual", "scheduled", "ingestion"}
        or not 10 <= interval_seconds <= 86400
        or not 1 <= max_runs <= 100000
        or not 1 <= max_source_rows <= 10000000
    ):
        raise DatasetError(
            "Use supported triggers and bounded intervals, runs and source rows.",
            code="binding_limits",
        )
    target = Dataset.objects.filter(project=project, pk=dataset).first()
    source = (
        Dataset.objects.filter(project=project, pk=source_dataset).first()
        if source_dataset
        else None
    )
    recipe = DatasetPipeline.objects.filter(project=project, pk=pipeline).first()
    if target is None or recipe is None or (source_dataset and source is None):
        raise DatasetError("Binding references must belong to this project.", code="binding_source")
    workbench.require_package(recipe)
    if trace_source:
        try:
            trace_source = selection.TraceSource.parse(trace_source).spec()
        except selection.TraceSourceError as exc:
            raise DatasetError(str(exc), code="binding_source") from exc
    specification = {
        "dataset": str(dataset),
        "pipeline": str(pipeline),
        "source_dataset": str(source_dataset or ""),
        "trace_source": trace_source or {},
        "parameters": parameters or {},
        "trigger": trigger,
        "interval_seconds": interval_seconds,
        "max_runs": max_runs,
        "max_source_rows": max_source_rows,
    }
    fingerprint = workbench.digest(specification)
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        current = (
            DatasetPipelineBinding.objects.select_for_update()
            .select_related("pipeline")
            .filter(project=project, pk=binding)
            .first()
            if binding
            else None
        )
        if current and current.request_key == request_key and current.fingerprint == fingerprint:
            return current
        if binding and (current is None or current.version != expected_version):
            raise DatasetError(
                "The binding changed. Inspect it before revising.", code="binding_conflict"
            )
        if current and current.runs.filter(state__in=["queued", "running"]).exists():
            raise DatasetError(
                "Finish or cancel the binding's current run before revising.", code="busy"
            )
        existing = DatasetPipelineBinding.objects.filter(
            project=project, request_key=request_key
        ).first()
        if existing and not current:
            if existing.fingerprint != fingerprint:
                raise DatasetError("This request key identifies another binding.", code="conflict")
            return existing
        if existing and current and existing.pk != current.pk:
            raise DatasetError("This request key identifies another binding.", code="conflict")
        if source:
            visited, pending = {str(target.pk)}, [str(source.pk)]
            while pending:
                identity = pending.pop()
                if identity == str(target.pk):
                    raise DatasetError(
                        "This binding would create a dataset feedback loop.", code="binding_source"
                    )
                if identity in visited:
                    continue
                visited.add(identity)
                pending.extend(
                    str(value)
                    for value in DatasetPipelineBinding.objects.filter(
                        project=project, dataset_id=identity, source_dataset__isnull=False
                    )
                    .exclude(pk=binding)
                    .values_list("source_dataset_id", flat=True)
                )
        result = current or DatasetPipelineBinding(project=project, created_by=user)
        for key, value in specification.items():
            if key not in {"dataset", "pipeline", "source_dataset"}:
                setattr(result, key, value)
        result.dataset, result.pipeline, result.source_dataset = target, recipe, source
        result.request_key, result.fingerprint = request_key, fingerprint
        if current:
            result.version += 1
        result.enabled, result.checkpoint, result.next_check_at = False, "", None
        result.error = ""
        result.save()
        return result


def set_state(project, *, binding, expected_version, enabled):
    with transaction.atomic():
        current = (
            DatasetPipelineBinding.objects.select_for_update()
            .select_related("pipeline")
            .filter(project=project, pk=binding)
            .first()
        )
        if current is None:
            raise DatasetError("Binding not found in this project.", code="binding_not_found")
        if current.version != expected_version:
            raise DatasetError(
                "The binding changed. Inspect it before changing state.", code="binding_conflict"
            )
        if enabled:
            workbench.require_package(current.pipeline)
        current.enabled, current.next_check_at = enabled, timezone.now() if enabled else None
        current.version += 1
        current.save(update_fields=["enabled", "next_check_at", "version", "updated_at"])
        return current


def advance(project, binding_id, *, manual=False):
    with transaction.atomic():
        binding = (
            DatasetPipelineBinding.objects.select_for_update(of=("self",))
            .select_related("project", "pipeline", "dataset", "source_dataset", "created_by")
            .filter(project=project, pk=binding_id)
            .first()
        )
        if binding is None:
            raise DatasetError("Binding not found in this project.", code="binding_not_found")
        if (not manual and not binding.enabled) or binding.runs.filter(
            state__in=["queued", "running"]
        ).exists():
            return None
        if binding.runs_started >= binding.max_runs:
            raise DatasetError(
                "The binding reached its authorized run limit.", code="binding_limit"
            )
        workbench.require_package(binding.pipeline)
        binding.last_checked_at = timezone.now()
        binding.next_check_at = timezone.now() + timedelta(seconds=binding.interval_seconds)
        binding.save(update_fields=["last_checked_at", "next_check_at"])
        target = Dataset.objects.select_for_update().get(pk=binding.dataset_id)
        if target.state not in {Dataset.State.IDLE, Dataset.State.ERROR}:
            return None
        if binding.source_dataset_id:
            source = binding.source_dataset.active_cell
            if source is None or source.state != Cell.State.OK:
                raise DatasetError(
                    "The source has no readable active version.", code="binding_source"
                )
            checkpoint = source.fingerprint
            if source.rows > binding.max_source_rows:
                raise DatasetError(
                    "The source exceeds the binding's row limit; no rows were dropped.",
                    code="binding_limit",
                )
        else:
            source_spec = selection.TraceSource.parse(binding.trace_source)
            ids = list(
                islice(
                    source_spec.iter_trace_ids(binding.project_id),
                    min(binding.max_source_rows, 10000) + 1,
                )
            )
            if len(ids) > min(binding.max_source_rows, 10000):
                raise DatasetError(
                    "Trace snapshot exceeds the binding limit; narrow the selection.",
                    code="binding_limit",
                )
            try:
                records = list(
                    land.iter_trace_rows(
                        binding.project_id,
                        sorted(ids),
                        preferred_capability_id=source_spec.preferred_capability_id,
                    )
                )
            except land.LandError as exc:
                raise DatasetError(str(exc), code="binding_source") from exc
            if len(records) != len(ids):
                raise DatasetError(
                    "Some selected traces could not be captured; the checkpoint is unchanged.",
                    code="binding_source",
                )
            checkpoint = workbench.digest(json.loads(json.dumps(records, cls=DjangoJSONEncoder)))
            source = None
        if checkpoint == binding.checkpoint:
            return None
        request_key = f"binding:{binding.pk}:{binding.version}:{checkpoint[:32]}"
        previous = binding.runs.filter(request_key=request_key).first()
        if previous:
            if previous.state in {"failed", "cancelled"}:
                raise DatasetError(
                    f"Run {previous.pk} {previous.state}. Inspect its receipt, then explicitly "
                    "re-enable or revise the binding to authorize another attempt.",
                    code="binding_run_failed",
                )
            return previous
        if source is None:
            position = (target.cells.aggregate(n=Max("position"))["n"] or 0) + 1
            source = Cell.objects.create(
                dataset=target,
                position=position,
                title="Trace snapshot",
                created_by=binding.created_by,
            )
            path = paths.cell_path(target.pk, source.pk)
            store.write_rows(
                path,
                ({**row, store.SOURCE_ROW: index} for index, row in enumerate(records)),
                list(land.TRACE_MANIFEST),
            )
            measure.frame(
                target,
                source,
                path,
                review={
                    "kind": "source_snapshot",
                    "binding": str(binding.pk),
                    "selection": binding.trace_source,
                },
            )
        run = workbench.submit(
            target,
            binding.created_by,
            pipeline=binding.pipeline_id,
            source_cell=source.pk,
            source_fingerprint=source.fingerprint,
            request_key=request_key,
            parameters=binding.parameters,
            binding=binding,
            binding_checkpoint=checkpoint,
        )
        binding.runs_started += 1
        binding.save(update_fields=["runs_started", "updated_at"])
        return run


def tick():
    now = timezone.now()
    for binding in (
        DatasetPipelineBinding.objects.filter(enabled=True)
        .exclude(trigger="manual")
        .filter(Q(next_check_at__isnull=True) | Q(next_check_at__lte=now))
        .select_related("project")[:100]
    ):
        try:
            advance(binding.project, binding.pk)
        except DatasetError as exc:
            DatasetPipelineBinding.objects.filter(pk=binding.pk, version=binding.version).update(
                error=exc.detail, enabled=False
            )
        except Exception:
            logger.exception("Pipeline binding %s could not advance", binding.pk)
            DatasetPipelineBinding.objects.filter(pk=binding.pk, version=binding.version).update(
                error="Source inspection failed. Inspect the source and re-enable the binding.",
                enabled=False,
            )
