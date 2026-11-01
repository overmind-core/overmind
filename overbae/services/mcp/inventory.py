from dataclasses import dataclass

from django.conf import settings
from django.db.models import CharField, Count, F, Q, Value
from django.db.models.functions import Cast, Coalesce, Collate, NullIf
from django.utils import timezone

from overbae.models import (
    Capability,
    ConnectorCredential,
    Dataset,
    DatasetPipelineBinding,
    DatasetPipelineRun,
    DatasetTransfer,
    DecisionJob,
    DeployedModel,
    EvalMatrix,
    EvalRun,
    EvalSet,
    FinetuningJob,
    InferenceRequest,
    ModelActivation,
    OptimizerExperiment,
    TrainingCheckpoint,
    TrainingPreparation,
)
from overbae.services import model_workflows
from overbae.services.mcp.contracts.inventory import (
    DependenciesOutput,
    LineageOutput,
    ListObjectsInput,
    ListObjectsOutput,
    ObjectSummary,
    OverviewOutput,
)
from overbae.services.mcp.errors import MCPError


@dataclass(frozen=True)
class Collection:
    model: type
    name: str
    status: str | None
    resource: str
    route: str
    capability: str | None = "capability_id"
    project: str = "project_id"
    created: str = "created_at"


COLLECTIONS = {
    "checkpoint": Collection(
        TrainingCheckpoint,
        "key",
        "state",
        "checkpoints",
        "training",
        "job__capability_id",
        "job__project_id",
    ),
    "dataset_transfer": Collection(
        DatasetTransfer, "request_key", "state", "jobs/dataset_transfer", "datasets", None
    ),
    "inference_request": Collection(
        InferenceRequest, "request_key", "state", "jobs/inference_request", "inference", None
    ),
    "training_preparation": Collection(
        TrainingPreparation,
        "signature",
        "state",
        "jobs/training_preparation",
        "training",
        "cell__dataset__capability_id",
        "cell__dataset__project_id",
    ),
    "dataset_pipeline": Collection(
        DatasetPipelineRun,
        "request_key",
        "state",
        "jobs/dataset_pipeline",
        "datasets",
        "dataset__capability_id",
        "dataset__project_id",
    ),
    "model_activation": Collection(
        ModelActivation,
        "capability__name",
        "stage",
        "jobs/model_activation",
        "inference",
        "capability_id",
        "capability__project_id",
        "started_at",
    ),
    "connector_sync": Collection(
        ConnectorCredential, "name", "sync_status", "connectors", "observability", None
    ),
    "decision_job": Collection(
        DecisionJob,
        "finetuning_job__name",
        "finetuning_job__status",
        "jobs/decision_job",
        "training",
        "finetuning_job__capability_id",
        "finetuning_job__project_id",
    ),
    "trained_model": Collection(FinetuningJob, "name", "status", "finetunes", "training"),
    "finetune": Collection(FinetuningJob, "name", "status", "finetunes", "training"),
    "deployment": Collection(
        DeployedModel,
        "model_id",
        "status",
        "deployments",
        "inference",
        "finetuning_job__capability_id",
    ),
    "capability": Collection(Capability, "name", "status", "capabilities", "capabilities", "id"),
    "dataset": Collection(Dataset, "name", "state", "datasets", "datasets"),
    "eval_set": Collection(EvalSet, "name", None, "eval-sets", "evaluations"),
    "eval_row": Collection(EvalMatrix, "name", None, "eval-rows", "evaluations"),
    "eval_run": Collection(
        EvalRun, "name", "status", "eval-runs", "evaluations/runs", "dataset__capability_id"
    ),
    "optimizer": Collection(
        OptimizerExperiment, "capability__name", "status", "optimizer-runs", "optimiser"
    ),
    **{
        kind: Collection(model, "name", "state", f"jobs/{kind}", "training", None)
        for kind, model in model_workflows.MODELS.items()
    },
}


def queryset(project, kind, payload):
    spec = COLLECTIONS[kind]
    query = (
        spec.model.objects.filter(**{f"{spec.project}__in": [item.pk for item in project]})
        if isinstance(project, list)
        else spec.model.objects.filter(**{spec.project: project.pk})
    )
    if kind == "trained_model":
        query = query.filter(status=FinetuningJob.Status.SUCCEEDED)
    if kind == "capability":
        query = query.exclude(status="deleted")
    if payload.search:
        condition = Q(**{f"{spec.name}__icontains": payload.search})
        if kind in {"finetune", "trained_model"}:
            condition |= Q(base_model__icontains=payload.search) | Q(
                output_model_name__icontains=payload.search
            )
        query = query.filter(condition)
    if payload.status:
        if spec.status is None:
            return query.none()
        query = query.filter(**{spec.status: payload.status})
    if payload.capability:
        if spec.capability is None:
            return query.none()
        query = query.filter(**{spec.capability: payload.capability})
    if payload.request_key:
        if not any(field.name == "request_key" for field in spec.model._meta.fields):
            return query.none()
        query = query.filter(request_key=payload.request_key)
    if payload.since:
        query = query.filter(**{f"{spec.created}__gte": payload.since})
    if payload.before:
        query = query.filter(**{f"{spec.created}__lt": payload.before})
    return query


def deployment_summary(row):
    identifier, project = str(row["id"]), str(row["project_id"])
    name = row["model_id"] or "deployment"
    capability = row["finetuning_job__capability_id"]
    training_job = row["finetuning_job_id"]
    return ObjectSummary(
        kind="deployment",
        id=identifier,
        project_id=project,
        name=name,
        status=row["status"],
        created_at=row["created_at"],
        capability_id=str(capability) if capability else None,
        base_model=row["base_model_id"],
        training_job_id=str(training_job) if training_job else None,
        deployment_id=identifier,
        serving_model_id=row["model_id"],
        resource={
            "uri": f"overmind://deployments/{identifier}?project_id={project}",
            "title": name[:160],
        },
        console_url=f"{settings.FRONTEND_URL.rstrip('/')}/inference?projectId={project}",
    )


def list_objects(project, payload):
    kinds = payload.kinds or [kind for kind in COLLECTIONS if kind != "trained_model"]
    queries = []
    for kind in dict.fromkeys(kinds):
        spec = COLLECTIONS[kind]
        query = queryset(project, kind, payload).order_by()
        training = (
            "id"
            if kind in {"finetune", "trained_model"}
            else "finetuning_job_id"
            if kind in {"deployment", "decision_job"}
            else "job_id"
            if kind == "checkpoint"
            else None
        )
        deployment = (
            "deployed_model__id"
            if kind in {"finetune", "trained_model"}
            else "id"
            if kind == "deployment"
            else None
        )
        base = (
            "base_model"
            if kind in {"finetune", "trained_model"}
            else "base_model_id"
            if kind == "deployment"
            else None
        )

        def text_field(field):
            return Cast(F(field), CharField()) if field else Value(None, output_field=CharField())

        queries.append(
            query.annotate(
                inventory_kind=Value(kind, output_field=CharField()),
                inventory_id=text_field("id"),
                inventory_project=text_field(spec.project),
                inventory_name=Collate(
                    Coalesce(
                        NullIf(F(spec.name), Value("")), Value(kind), output_field=CharField()
                    ),
                    "C",
                ),
                inventory_created=F(spec.created),
                inventory_status=text_field(spec.status),
                inventory_capability=text_field(spec.capability),
                inventory_base=text_field(base),
                inventory_training=text_field(training),
                inventory_deployment=text_field(deployment),
                inventory_serving=text_field(
                    "model_id"
                    if kind == "deployment"
                    else "deployed_model__model_id"
                    if kind in {"finetune", "trained_model"}
                    else None
                ),
            ).values(
                "inventory_kind",
                "inventory_id",
                "inventory_project",
                "inventory_name",
                "inventory_created",
                "inventory_status",
                "inventory_capability",
                "inventory_base",
                "inventory_training",
                "inventory_deployment",
                "inventory_serving",
            )
        )
    combined = queries[0].union(*queries[1:], all=True)
    ordering = (
        ("inventory_name", "inventory_kind", "inventory_id")
        if payload.ordering == "name"
        else ("inventory_created", "inventory_kind", "inventory_id")
    )
    if payload.ordering == "newest":
        ordering = tuple(f"-{field}" for field in ordering)
    total = combined.count()
    end = payload.offset + payload.limit
    items = []
    for row in combined.order_by(*ordering)[payload.offset : end]:
        kind, identifier, project_id = (
            row["inventory_kind"],
            row["inventory_id"],
            row["inventory_project"],
        )
        spec = COLLECTIONS[kind]
        items.append(
            ObjectSummary(
                kind=kind,
                id=identifier,
                project_id=project_id,
                name=row["inventory_name"],
                status=row["inventory_status"],
                created_at=row["inventory_created"],
                capability_id=row["inventory_capability"],
                base_model=row["inventory_base"],
                training_job_id=row["inventory_training"],
                deployment_id=row["inventory_deployment"],
                serving_model_id=row["inventory_serving"],
                resource={
                    "uri": f"overmind://{spec.resource}/{identifier}?project_id={project_id}",
                    "title": row["inventory_name"][:160],
                },
                console_url=f"{settings.FRONTEND_URL.rstrip('/')}/{spec.route}?projectId={project_id}",
            )
        )
    return ListObjectsOutput(
        items=items,
        total=total,
        offset=payload.offset,
        next_offset=end if end < total else None,
        observed_at=timezone.now(),
    )


def overview(project, payload):
    counts = []
    for kind, spec in COLLECTIONS.items():
        query = queryset(project, kind, ListObjectsInput())
        if spec.status:
            counts.extend(
                {"kind": kind, "status": row[spec.status], "count": row["count"]}
                for row in query.values(spec.status)
                .annotate(count=Count("pk"))
                .order_by(spec.status)
            )
        else:
            counts.append({"kind": kind, "status": None, "count": query.count()})
    return OverviewOutput(
        project_id=str(project.pk),
        observed_at=timezone.now(),
        counts=counts,
        recent=list_objects(project, ListObjectsInput(limit=10)),
    )


def lineage(project, payload):
    job = (
        FinetuningJob.objects.filter(project=project, pk=payload.training_job)
        .select_related("cell", "validation_cell", "eval_cell")
        .first()
    )
    if job is None:
        raise MCPError("resource_not_found", "Training job not found in this project.")
    cells = [
        {
            "role": role,
            "id": str(cell.pk),
            "dataset_id": str(cell.dataset_id),
            "fingerprint": cell.fingerprint,
            "rows": cell.rows,
        }
        for role, cell in (
            ("train", job.cell),
            ("validation", job.validation_cell),
            ("evaluation", job.eval_cell),
        )
        if cell
    ]
    deployment = (
        DeployedModel.objects.filter(project=project, finetuning_job=job)
        .values(
            "id",
            "project_id",
            "model_id",
            "status",
            "created_at",
            "base_model_id",
            "finetuning_job_id",
            "finetuning_job__capability_id",
        )
        .first()
    )
    active = (
        Capability.objects.filter(project=project, active_model_id=deployment["id"]).order_by("pk")
        if deployment
        else Capability.objects.none()
    )
    evaluations = job.job_evals.order_by("created_at", "id")
    total = evaluations.count()
    end = payload.offset + payload.limit
    records = [
        {
            "id": str(row.pk),
            "kind": row.kind,
            "status": row.status,
            "eval_run_id": str(row.eval_run_id) if row.eval_run_id else None,
            "resource_uri": f"overmind://eval-runs/{row.eval_run_id}?project_id={project.pk}"
            if row.eval_run_id
            else None,
        }
        for row in evaluations[payload.offset : end]
    ]
    return LineageOutput(
        training_job_id=str(job.pk),
        project_id=str(project.pk),
        observed_at=timezone.now(),
        base_model=job.base_model,
        cells=cells,
        preparation_id=str(job.preparation_id) if job.preparation_id else None,
        deployment=deployment_summary(deployment) if deployment else None,
        evaluations=records,
        evaluation_total=total,
        next_offset=end if end < total else None,
        active_capabilities=[str(pk) for pk in active.values_list("pk", flat=True)[:100]],
        active_capabilities_total=active.count(),
        limitations=[
            "Missing pinned cells are unknown, not the dataset's current active cell.",
            "Deployment readiness and active routing do not establish worker warmth or application traffic.",
            "Active capability IDs are bounded to 100; list_objects(kind capability) and capability resources expose the full routing state.",
        ],
        resource={
            "uri": f"overmind://finetunes/{job.pk}?project_id={project.pk}",
            "title": "Training recipe and checkpoint evidence",
        },
    )


def dependencies(project, payload):
    spec = COLLECTIONS[payload.kind]
    if not spec.model.objects.filter(project=project, pk=payload.id).exists():
        raise MCPError("resource_not_found", "The object was not found in this project.")
    identifier = payload.id
    if payload.kind == "dataset":
        related = [
            (
                "finetune",
                "training/validation/evaluation data",
                FinetuningJob.objects.filter(project=project).filter(
                    Q(dataset_id=identifier)
                    | Q(validation_dataset_id=identifier)
                    | Q(eval_dataset_id=identifier)
                ),
                "finetunes",
            ),
            (
                "eval_run",
                "evaluation data",
                EvalRun.objects.filter(project=project, dataset_id=identifier),
                "eval-runs",
            ),
            (
                "optimizer",
                "optimization data",
                OptimizerExperiment.objects.filter(project=project, dataset_id=identifier),
                "optimizer-runs",
            ),
            (
                "dataset_binding",
                "scheduled preparation target",
                DatasetPipelineBinding.objects.filter(project=project, dataset_id=identifier),
                "dataset-pipeline-bindings",
            ),
        ]
    elif payload.kind == "deployment":
        related = [
            (
                "capability",
                "active or benchmark routing",
                Capability.objects.filter(project=project).filter(
                    Q(active_model_id=identifier) | Q(benchmark_model_id=identifier)
                ),
                "capabilities",
            )
        ]
    elif payload.kind == "eval_set":
        related = [
            (
                "capability",
                "active evaluation set",
                Capability.objects.filter(project=project, active_eval_set_id=identifier),
                "capabilities",
            ),
            (
                "eval_run",
                "evaluation history",
                EvalRun.objects.filter(project=project, eval_set_id=identifier),
                "eval-runs",
            ),
            (
                "finetune",
                "training evaluation set",
                FinetuningJob.objects.filter(project=project, eval_set_id=identifier),
                "finetunes",
            ),
        ]
    else:
        related = [
            (
                "eval_set",
                "set membership",
                EvalSet.objects.filter(project=project, rows__id=identifier),
                "eval-sets",
            )
        ]
    total, items = 0, []
    start, end = payload.offset, payload.offset + payload.limit
    for kind, relationship, query, resource in related:
        count = query.count()
        local_start, local_end = max(0, start - total), min(count, end - total)
        if local_end > local_start:
            items.extend(
                {
                    "kind": kind,
                    "id": str(pk),
                    "relationship": relationship,
                    "resource": {
                        "uri": f"overmind://{resource}/{pk}?project_id={project.pk}",
                        "title": relationship,
                    },
                }
                for pk in query.order_by("pk").values_list("pk", flat=True)[local_start:local_end]
            )
        total += count
    return DependenciesOutput(
        project_id=str(project.pk),
        observed_at=timezone.now(),
        items=items,
        total=total,
        next_offset=end if end < total else None,
        limitations=[
            "Recorded direct consumers and routing only. External application references and indirect pipeline lineage are not inferred.",
            "This inspection does not authorize removal or routing changes; recheck before mutation.",
        ],
    )
