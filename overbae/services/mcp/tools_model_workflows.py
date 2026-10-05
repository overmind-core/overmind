import logging

from asgiref.sync import sync_to_async
from rest_framework.exceptions import ValidationError

from overbae.api.credit_gate import require_credits
from overbae.core.errors import InputValidationError
from overbae.models import Cell
from overbae.services import (
    decision_performance,
    model_workflows,
    native_evaluation,
    training_experiments,
)
from overbae.services.datasets import exploration, partition_plans
from overbae.services.decision_providers import catalog
from overbae.services.mcp.contracts.model_workflows import (
    CreateComparisonInput,
    CreateExperimentInput,
    CreatePartitionInput,
    CreatePerformanceInput,
    CreateProfileInput,
    DecisionCatalogInput,
    DecisionCatalogOutput,
    DeriveDatasetInput,
    ExploreDatasetInput,
    LaunchComparisonInput,
    LaunchExperimentInput,
    ListWorkflowsInput,
    PrepareExperimentInput,
    ResumeComparisonInput,
    ResumePerformanceInput,
    RetryPartitionInput,
    ReusePredictionsInput,
    WorkflowListOutput,
    WorkflowOutput,
)
from overbae.services.mcp.errors import MCPError, serializer_fields
from overbae.services.mcp.resources import resource_link
from overbae.services.plan_limits import require_plan_quota
from overbae.tasks.data_exploration import run as explore
from overbae.tasks.data_partitions import build_plan
from overbae.tasks.decision_performance import measure
from overbae.tasks.native_evaluation import advance_plan

logger = logging.getLogger(__name__)


def cell(context, identifier):
    result = (
        Cell.objects.select_related("dataset")
        .filter(pk=identifier, dataset__project=context.project)
        .first()
    )
    if result is None:
        raise MCPError("cell_not_found", "The data version was not found in this project")
    return result


def workflow(context, kind, identifier):
    result = model_workflows.find(context.project, kind, identifier)
    if result is None:
        raise MCPError("resource_not_found", "The workflow was not found in this project")
    return result


def output(kind, record):
    return WorkflowOutput(
        summary=f"{record.name}: {record.state}",
        workflow={
            "id": str(record.pk),
            "name": record.name,
            "state": record.state,
            "kind": kind,
            "next_actions": model_workflows.next_actions(kind, record),
            **model_workflows.guidance(kind, record),
        },
        resource_links=[resource_link("jobs", f"{kind}/{record.pk}", record.name[:160])],
    )


def explore_data(payload, context):
    op = exploration.request(
        context.project,
        source_cell=cell(context, payload.source_cell),
        name=payload.name,
        request_key=payload.request_key,
        kind="profile",
        sampling_request=payload.sampling.model_dump() if payload.sampling else None,
    )
    if op.state == "queued":
        explore.delay(str(op.pk))
    return output("data_exploration", op)


def derive_data(payload, context):
    op = exploration.request(
        context.project,
        source_cell=cell(context, payload.source_cell),
        name=payload.name,
        request_key=payload.request_key,
        kind="derive",
    )
    if op.state == "queued":
        explore.delay(str(op.pk))
    return output("data_exploration", op)


def create_partition(payload, context):
    values = payload.model_dump(exclude={"source_cell"})
    plan = partition_plans.request_plan(
        context.project, source_cell=cell(context, payload.source_cell), **values
    )
    if plan.state == "queued":
        build_plan.delay(str(plan.pk))
    return output("data_partition", plan)


def create_comparison(payload, context):
    values = payload.model_dump(
        mode="json", exclude_none=True, exclude={"final_cell", "calibration_cell"}
    )
    plan = native_evaluation.create_plan(
        context.project,
        triggered_by=context.user,
        final_cell=cell(context, payload.final_cell),
        calibration_cell=cell(context, payload.calibration_cell)
        if payload.calibration_cell
        else None,
        **values,
    )
    return output("native_evaluation", plan)


def launch_comparison(payload, context):
    require_credits(context.user)
    plan = native_evaluation.launch(
        workflow(context, "native_evaluation", payload.evaluation), user=context.user
    )
    return output("native_evaluation", plan)


def resume_comparison(payload, context):
    plan = native_evaluation.resume(
        workflow(context, "native_evaluation", payload.evaluation),
        stage=payload.stage,
        call_id=payload.call_id,
    )
    advance_plan.delay(str(plan.pk))
    return output("native_evaluation", plan)


def create_experiment(payload, context):
    values = payload.model_dump(mode="json", exclude_none=True, exclude={"evaluation"})
    experiment = training_experiments.create(
        context.project,
        user=context.user,
        evaluation=workflow(context, "native_evaluation", payload.evaluation)
        if payload.evaluation
        else None,
        **values,
    )
    return output("training_experiment", experiment)


def launch_experiment(payload, context):
    require_credits(context.user)
    require_plan_quota(context.user, "training_jobs")
    experiment = training_experiments.launch(
        workflow(context, "training_experiment", payload.experiment),
        user=context.user,
        quote_id=payload.quote_id,
    )
    return output("training_experiment", experiment)


def prepare_experiment(payload, context):
    return output(
        "training_experiment",
        training_experiments.request_preparation(
            workflow(context, "training_experiment", payload.experiment)
        ),
    )


def create_profile(payload, context):
    return output(
        "training_experiment",
        training_experiments.create_profile(
            context.project, user=context.user, **payload.model_dump(mode="json", exclude_none=True)
        ),
    )


def prepare_comparison(payload, context):
    return output(
        "native_evaluation",
        native_evaluation.prepare(workflow(context, "native_evaluation", payload.evaluation)),
    )


def reuse_comparison(payload, context):
    plan = native_evaluation.reuse_predictions(
        workflow(context, "native_evaluation", payload.evaluation),
        source=workflow(context, "native_evaluation", payload.source_evaluation),
        participant=payload.participant,
        source_participant=payload.source_participant,
    )
    return output("native_evaluation", plan)


def pause_comparison(payload, context):
    return output(
        "native_evaluation",
        native_evaluation.pause(workflow(context, "native_evaluation", payload.evaluation)),
    )


def list_workflows(payload, context):
    query = (
        model_workflows.MODELS[payload.kind]
        .objects.filter(project=context.project)
        .order_by("-created_at")
    )
    total = query.count()
    records = list(query[payload.offset : payload.offset + payload.limit])
    return WorkflowListOutput(
        summary=f"{len(records)} of {total} workflows",
        total=total,
        workflows=[
            {"id": str(record.pk), "name": record.name, "state": record.state} for record in records
        ],
        resource_links=[
            resource_link("jobs", f"{payload.kind}/{record.pk}", record.name[:160])
            for record in records
        ],
    )


def create_performance(payload, context):
    require_credits(context.user)
    plan = workflow(context, "native_evaluation", payload.evaluation)
    run = decision_performance.create(plan, **payload.model_dump(exclude={"evaluation"}))
    if run.state == "queued":
        measure.delay(str(run.pk))
    return output("decision_performance", run)


def resume_performance(payload, context):
    run = workflow(context, "decision_performance", payload.performance_run)
    if run.state != "failed":
        raise ValueError("Only stopped measurements can be resumed")
    run.state, run.error = "queued", ""
    run.save()
    measure.delay(str(run.pk))
    return output("decision_performance", run)


def decision_catalog(payload, context):
    return DecisionCatalogOutput(models=catalog(context.project))


def retry_partition(payload, context):
    plan = workflow(context, "data_partition", payload.partition)
    partition_plans.retry(plan)
    build_plan.delay(str(plan.pk))
    plan.refresh_from_db()
    return output("data_partition", plan)


def handler(function):
    async def invoke(payload, context):
        try:
            return await sync_to_async(function, thread_sensitive=True)(payload, context)
        except InputValidationError as exc:
            raise MCPError("invalid_input", exc.detail) from None
        except ValidationError as exc:
            raise MCPError(
                "invalid_input",
                "Correct the indicated workflow fields.",
                fields=serializer_fields(exc.detail),
            ) from None
        except ValueError:
            logger.exception("Workflow validation failed")
            raise MCPError(
                "invalid_input",
                "The workflow configuration could not be validated; inspect the selected inputs and recipe",
            ) from None

    return invoke


def register_model_workflow_tools(catalog):
    # The catalog imports registrars before registering their definitions.
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "prepare_native_evaluation",
            "Prepare comparison inputs",
            "Verify sealed inputs, reference contracts and overlap in a background operation without provider submissions. Launch the prepared protocol separately.",
            LaunchComparisonInput,
            WorkflowOutput,
            prepare_comparison,
            False,
            "compute",
            "overmind:evaluate",
        ),
        (
            "reuse_evaluation_predictions",
            "Reuse verified predictions",
            "Explicitly reuse completed prediction evidence for a draft participant with identical project, sources, model identity, runtime and inference conditions. Records reuse lineage; does not create an independent repetition.",
            ReusePredictionsInput,
            WorkflowOutput,
            reuse_comparison,
            False,
            "compute",
            "overmind:evaluate",
        ),
        (
            "prepare_training_experiment",
            "Forecast saved experiment",
            "Save a recipe-bound planning forecast from measured evidence. Unknown costs stay unknown. Does not submit training, tokenization or profiling; get_job exposes the quote and its evidence.",
            PrepareExperimentInput,
            WorkflowOutput,
            prepare_experiment,
            False,
            "compute",
            "overmind:train",
        ),
        (
            "create_training_profile",
            "Draft runtime qualification",
            "Save one bounded training profile using the selected source and recipe, with explicit optimizer-step and provider-attempt time limits and no automatic provider retry. Launch separately through launch_training_experiment. Time bounds cover the GPU attempt, not preprocessing or total invoiced spend.",
            CreateProfileInput,
            WorkflowOutput,
            create_profile,
            False,
            "compute",
            "overmind:train",
        ),
        (
            "pause_native_evaluation",
            "Pause comparison submission",
            "Pause future stage submissions. An already claimed batch or provider call may finish and its receipt is retained; this does not terminate provider work or stop an external notification monitor. Resume through resume_native_evaluation.",
            LaunchComparisonInput,
            WorkflowOutput,
            pause_comparison,
            False,
            "compute",
            "overmind:evaluate",
        ),
        (
            "explore_dataset",
            "Explore source data",
            "Profile a selected immutable source across all rows with bounded examples and optional sampling feasibility. Does not interpret labels, modify rows or submit provider work; get_job retains evidence across reconnects.",
            ExploreDatasetInput,
            WorkflowOutput,
            explore_data,
            False,
            "compute",
            "overmind:data:write",
        ),
        (
            "derive_dataset",
            "Derive dataset",
            "Create an independent chain from the exact selected source cell, preserving rows, targets and lineage. Does not change the parent chain or infer a new intent.",
            DeriveDatasetInput,
            WorkflowOutput,
            derive_data,
            False,
            "compute",
            "overmind:data:write",
        ),
        (
            "launch_native_evaluation",
            "Launch saved comparison",
            "Explicitly authorize and launch the saved comparison once. Creating a comparison is free of provider execution; launch can incur provider costs.",
            LaunchComparisonInput,
            WorkflowOutput,
            launch_comparison,
            False,
            "gpu",
            "overmind:evaluate",
        ),
        (
            "list_decision_models",
            "Decision model catalog",
            "List foundation, trained and external decision participants. Catalog eligibility is not measured hardware or quality qualification.",
            DecisionCatalogInput,
            DecisionCatalogOutput,
            decision_catalog,
            True,
            "free",
            "overmind:read",
        ),
        (
            "retry_data_partition",
            "Retry data partition",
            "Resume failed construction of the saved partition recipe without changing its source or assignments.",
            RetryPartitionInput,
            WorkflowOutput,
            retry_partition,
            False,
            "compute",
            "overmind:data:write",
        ),
        (
            "measure_decision_performance",
            "Measure decision performance",
            "Run a paid saved workload against all qualified comparison participants. Measure client latency, valid throughput and recorded costs; distinguish native first-request startup from warm requests and uncontrolled provider caching.",
            CreatePerformanceInput,
            WorkflowOutput,
            create_performance,
            False,
            "gpu",
            "overmind:evaluate",
        ),
        (
            "resume_decision_performance",
            "Resume performance measurement",
            "Continue a stopped workload from saved request receipts. Completed requests and unresolved acknowledgements are never replayed.",
            ResumePerformanceInput,
            WorkflowOutput,
            resume_performance,
            False,
            "gpu",
            "overmind:evaluate",
        ),
        (
            "create_data_partition",
            "Create data partitions",
            "Save seeded, group-preserving partitions and explicit holdouts. Preserve duplicate observations and lineage; inspect actual coverage through get_job.",
            CreatePartitionInput,
            WorkflowOutput,
            create_partition,
            False,
            "compute",
            "overmind:data:write",
        ),
        (
            "create_native_evaluation",
            "Compare decision models",
            "Save an immutable comparison draft without submitting provider work. Use launch_native_evaluation to run: frozen input-only suites, foundations, trained artifacts or external probability models, optional calibration, paired scoring and durable receipts.",
            CreateComparisonInput,
            WorkflowOutput,
            create_comparison,
            False,
            "compute",
            "overmind:evaluate",
        ),
        (
            "resume_native_evaluation",
            "Resume decision comparison",
            "Resume unfinished collection or saved-response analysis. Reconcile unresolved native submissions with their existing call ID; never replay unknown external submissions.",
            ResumeComparisonInput,
            WorkflowOutput,
            resume_comparison,
            False,
            "gpu",
            "overmind:evaluate",
        ),
        (
            "create_training_experiment",
            "Save training experiment",
            "Save explicit candidate recipes and frozen data versions under one purpose and optional evaluation protocol. Does not launch training.",
            CreateExperimentInput,
            WorkflowOutput,
            create_experiment,
            False,
            "compute",
            "overmind:train",
        ),
        (
            "launch_training_experiment",
            "Launch training experiment",
            "Launch the saved candidate set once using existing training preparation and qualification. Records requested/effective differences; never activates a model.",
            LaunchExperimentInput,
            WorkflowOutput,
            launch_experiment,
            False,
            "gpu",
            "overmind:train",
        ),
        (
            "list_model_workflows",
            "List model workflows",
            "Discover this project's data partitions, native comparisons and saved training experiments; use get_job for status, result summaries and complete report downloads.",
            ListWorkflowsInput,
            WorkflowListOutput,
            list_workflows,
            True,
            "free",
            "overmind:read",
        ),
    ]
    for (
        name,
        title,
        description,
        input_model,
        output_model,
        function,
        read_only,
        cost,
        scope,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=name != "resume_native_evaluation",
                open_world=cost == "gpu",
                required_scopes=frozenset({scope}),
                cost_class=cost,
                async_mode="sync" if read_only else "job",
            ),
            handler(function),
        )
