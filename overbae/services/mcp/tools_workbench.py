from asgiref.sync import sync_to_async

from overbae.models import Capability, DatasetPreparation
from overbae.services.datasets import lifecycle, pipeline_bindings, preparation_workflow, workbench
from overbae.services.mcp.contracts.common import ResourceLinkContract
from overbae.services.mcp.contracts.workbench import (
    BindingInput,
    BindingStateInput,
    CancelRunInput,
    ImportInput,
    InspectInput,
    Output,
    RunInput,
    SaveBindingInput,
    SaveInput,
    StartPreparationInput,
    UpdateInput,
    UpdatePreparationInput,
    ValidateInput,
    compact_run,
)
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset
from overbae.services.mcp.resources import resource_link


def invoke(name, payload, context):
    dataset = mcp_dataset(context, payload.dataset) if getattr(payload, "dataset", None) else None
    args = payload.model_dump(exclude={"dataset"}, exclude_unset=True)
    result = {}
    try:
        if name == "inspect_dataset_workbench":
            result = workbench.describe(dataset, project=context.project, **args)
            latest = (
                DatasetPreparation.objects.filter(dataset=dataset).order_by("-created_at").first()
                if dataset
                else None
            )
            result["preparation_workflow"] = (
                preparation_workflow.describe(latest) if latest else None
            )
        elif name == "start_dataset_preparation":
            result["preparation_workflow"] = preparation_workflow.describe(
                preparation_workflow.start(dataset, context, **args)
            )
        elif name == "update_dataset_preparation":
            result["preparation_workflow"] = preparation_workflow.describe(
                preparation_workflow.update(dataset, outputs=args.pop("outputs", []), **args)
            )
        elif name == "save_dataset_pipeline":
            result["pipeline"] = workbench.pipeline_record(
                workbench.save_pipeline(context.project, context.user, dataset=dataset, **args)
            )
        elif name == "validate_dataset_pipeline":
            result["validation"] = workbench.validate_pipeline(context.project, **args)
        elif name == "cancel_dataset_pipeline_run":
            result["run"] = workbench.run_record(workbench.cancel_run(context.project, args["run"]))
        elif name == "save_dataset_pipeline_binding":
            result["binding"] = pipeline_bindings.binding_record(
                pipeline_bindings.save(context.project, context.user, dataset=dataset.pk, **args)
            )
        elif name == "set_dataset_pipeline_binding_state":
            result["binding"] = pipeline_bindings.binding_record(
                pipeline_bindings.set_state(context.project, **args)
            )
        elif name == "run_dataset_pipeline_binding":
            run = pipeline_bindings.advance(context.project, args["binding"], manual=True)
            if run:
                result["run"] = workbench.run_record(run)
        elif name in {"run_dataset_pipeline", "import_dataset_version"}:
            result["run"] = workbench.run_record(workbench.submit(dataset, context.user, **args))
        elif name == "update_dataset":
            if args.get("capability"):
                capability = Capability.objects.filter(
                    project=context.project, pk=args["capability"]
                ).first()
                if capability is None:
                    raise MCPError(
                        "capability_not_found", "The capability was not found in this project."
                    )
                args["capability"] = capability
            dataset = workbench.update_dataset(dataset, **args)
    except lifecycle.DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    for receipt in [result["run"]] if "run" in result else result.get("runs", []):
        receipt.update(
            compact_run(
                receipt,
                resource_link(
                    "jobs", f"dataset_pipeline/{receipt['id']}", "Transformation evidence"
                ),
            )
        )
    if "run" in result:
        receipt = result["run"]
        result["job"] = {
            "kind": "dataset_pipeline",
            "id": receipt["id"],
            "status": receipt["state"],
            "resource": resource_link(
                "jobs", f"dataset_pipeline/{receipt['id']}", "Dataset transformation"
            ),
        }
    next_actions = []
    if "run" in result:
        next_actions.append(
            {
                "tool": "get_job",
                "reason": "Read this exact transformation receipt; follow its polling interval.",
                "arguments": {
                    "project_id": str(context.project.pk),
                    "kind": "dataset_pipeline",
                    "id": result["run"]["id"],
                },
            }
        )
    elif name == "save_dataset_pipeline":
        next_actions.append(
            {
                "tool": "validate_dataset_pipeline",
                "reason": "Validate the retained revision; add the inspected source cell and fingerprint to check source compatibility.",
                "arguments": {
                    "project_id": str(context.project.pk),
                    "pipeline": result["pipeline"]["id"],
                },
            }
        )
    return Output(
        project_id=str(context.project.pk),
        next_actions=next_actions,
        summary=(
            "Source unchanged or a binding run is already pending."
            if name == "run_dataset_pipeline_binding" and "run" not in result
            else "Pipeline validation."
            if name == "validate_dataset_pipeline"
            else "Dataset workbench."
            if name == "inspect_dataset_workbench"
            else "Dataset workbench updated."
        ),
        dataset=str(dataset.pk) if dataset else None,
        **{k: v for k, v in result.items() if k != "dataset"},
        resource_links=(
            [
                ResourceLinkContract(
                    **resource_link("datasets", str(dataset.pk), (dataset.name or "Dataset")[:160])
                )
            ]
            if dataset
            else []
        )
        + (
            [
                ResourceLinkContract(
                    **resource_link(
                        "dataset-pipelines", result["pipeline"]["id"], "Transformation revision"
                    )
                )
            ]
            if "pipeline" in result
            else []
        )
        + (
            [
                ResourceLinkContract(
                    **resource_link(
                        "dataset-pipeline-bindings",
                        result["binding"]["id"],
                        "Transformation binding",
                    )
                )
            ]
            if "binding" in result
            else []
        )
        + ([ResourceLinkContract(**result["job"]["resource"])] if "job" in result else []),
    )


def register_workbench_tools(catalog):
    # Catalog imports tool modules after defining ToolDefinition.
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "start_dataset_preparation",
            StartPreparationInput,
            False,
            "Begin agent work; retry request_key unchanged. Keeps Preparing between jobs. Inspect to resume; explicitly finish.",
        ),
        (
            "update_dataset_preparation",
            UpdatePreparationInput,
            False,
            "Overall state: preparing resumes, paused waits, cancelled abandons. Complete after all work/jobs/uploads finish with published output UUIDs. Terminal states immutable.",
        ),
        (
            "inspect_dataset_workbench",
            InspectInput,
            True,
            "Read recipes/runs/bindings/preparation_workflow. pipeline selects revision/family history; dataset filters runs/bindings. Code in resources; no execution.",
        ),
        (
            "save_dataset_pipeline",
            SaveInput,
            False,
            "Save package from pipeline-upload (overmind://dataset-upload), without execution. dataset attaches/replaces script. Steps: id + input (source/prior step) or inputs (fan-in); source_row overlap fails. Last step outputs; inspect flow.unconsumed_steps. Conditions cite expression/entrypoint line. Revise pipeline + expected_revision or derive derived_from. Changed request-key payload conflicts.",
        ),
        (
            "run_dataset_pipeline",
            RunInput,
            False,
            "Run pinned revision/source in isolation. Preview bounds input, defers population limits; lineage/preserve_rows apply. Publish appends cells atomically. Semantics unmeasured. Poll after poll_after_seconds; retry same request_key.",
        ),
        (
            "validate_dataset_pipeline",
            ValidateInput,
            True,
            "Check syntax, requirements, optional source columns. No execution or semantic/full-row validation.",
        ),
        (
            "cancel_dataset_pipeline_run",
            CancelRunInput,
            False,
            "Cancel exact run publication; container termination awaits runner observation. Retains source and consumer versions.",
        ),
        (
            "save_dataset_pipeline_binding",
            SaveBindingInput,
            False,
            "Save paused binding to active dataset version or traces; rebuild snapshots with late edits/removals. Pin revision/parameters/interval/run/row limits. Revise with expected_version; pauses until enabled.",
        ),
        (
            "set_dataset_pipeline_binding_state",
            BindingStateInput,
            False,
            "Enable/pause with expected_version. Pause stops new batches only. Registration never enables execution.",
        ),
        (
            "run_dataset_pipeline_binding",
            BindingInput,
            False,
            "Run bound snapshot with saved limits; unchanged source is a no-op. No agent or paid provider.",
        ),
        (
            "import_dataset_version",
            ImportInput,
            False,
            "Import pinned source: validate source_row/_overmind_parent_rows. Supply imported_rows (2000 rows/4 MiB) or project artifact_cell + artifact_fingerprint. provenance names producer; execution/semantics unverified.",
        ),
        (
            "update_dataset",
            UpdateInput,
            False,
            "Set name, user intent, capability, active cell or future Tesseract OCR codes ([] English). Intent/capability freeze after use; null clears capability/default. Existing cells/consumers unchanged.",
        ),
    ]
    for name, model, read_only, description in definitions:

        async def handler(payload, context, name=name):
            return await sync_to_async(invoke, thread_sensitive=True)(name, payload, context)

        catalog.register(
            ToolDefinition(
                name=name,
                title=name.replace("_", " ").capitalize(),
                description=description,
                input_model=model,
                output_model=Output,
                read_only=read_only,
                idempotent=name
                not in {"set_dataset_pipeline_binding_state", "run_dataset_pipeline_binding"},
                open_world=False,
                required_scopes=frozenset(
                    {"overmind:read" if read_only else "overmind:data:write"}
                ),
                cost_class="compute"
                if name
                in {
                    "run_dataset_pipeline",
                    "import_dataset_version",
                    "run_dataset_pipeline_binding",
                }
                else "free",
                async_mode="job"
                if name
                in {
                    "run_dataset_pipeline",
                    "import_dataset_version",
                    "run_dataset_pipeline_binding",
                }
                else "sync",
            ),
            handler,
        )
