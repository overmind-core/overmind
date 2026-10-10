from asgiref.sync import sync_to_async

from overbae.models import Capability
from overbae.services.datasets import lifecycle, pipeline_bindings, workbench
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
    UpdateInput,
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
        elif name == "save_dataset_pipeline":
            result["pipeline"] = workbench.pipeline_record(
                workbench.save_pipeline(context.project, context.user, **args)
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
            "inspect_dataset_workbench",
            InspectInput,
            True,
            "Discover reusable transformations. Optional pipeline is an exact revision ID: returns that recipe, paged family revisions newest first, and its runs/bindings. Optional dataset filters runs/bindings, not recipes. Page with pipeline_offset, run_offset, binding_offset and limit. Follow revision/package resources for retained code. Reads never execute scripts.",
        ),
        (
            "save_dataset_pipeline",
            SaveInput,
            False,
            "Register an immutable reusable revision without execution. A retained Python package is mandatory: author meaningful staged scripts, upload with overmind dataset pipeline-upload (see overmind://dataset-upload), and supply its package UUID. Package-free step definitions are rejected. Historical package-free revisions are read-only. Give steps id and input (source or earlier step), or inputs=[earlier IDs] for fan-in. Inputs concatenate in order; overlapping source_row identities fail. Last step is output; flow.unconsumed_steps identifies disconnected deliverables. Script conditions cite expression and entrypoint line; flow declarations are distinct from execution facts. Revise with pipeline family ID and expected_revision; adapt with derived_from revision ID. Changed request-key content conflicts.",
        ),
        (
            "run_dataset_pipeline",
            RunInput,
            False,
            "Run an exact revision on a pinned project source. Preview bounds input without publication: min_rows/max_rows are deferred population checks; preserve_rows and lineage still apply. Publish enforces all checks and atomically appends one cell per step. Scripts use the approved isolated runner. Follow poll_after_seconds; queued work is not a failure. Recover the identical request_key, never resubmit to avoid a normal wait. Semantic correctness remains unmeasured.",
        ),
        (
            "validate_dataset_pipeline",
            ValidateInput,
            True,
            "Check retained package, syntax, declared requirements and optional source columns without executing code. Does not establish semantic compatibility or full-row validity.",
        ),
        (
            "cancel_dataset_pipeline_run",
            CancelRunInput,
            False,
            "Cancel publication of one exact run. Container termination remains unconfirmed until the runner observes it; source and consumer versions remain intact.",
        ),
        (
            "save_dataset_pipeline_binding",
            SaveBindingInput,
            False,
            "Save a paused source-to-revision binding. Sources are another dataset's active version or a trace selection; changes rebuild full snapshots, including late updates/removals. Pin exact revision, parameters, interval and run/row limits. Revising requires expected_version and pauses the binding; enable separately.",
        ),
        (
            "set_dataset_pipeline_binding_state",
            BindingStateInput,
            False,
            "Explicitly enable or pause a saved binding with expected_version. Pause stops new batches, not an in-flight run. Reads and registration never enable execution.",
        ),
        (
            "run_dataset_pipeline_binding",
            BindingInput,
            False,
            "Process one bound source snapshot with its exact saved revision and limits. An unchanged successful snapshot is a no-op. Returns the durable run; no agent authoring or paid providers are invoked.",
        ),
        (
            "import_dataset_version",
            ImportInput,
            False,
            "Import externally authored rows against a pinned source. Every row must retain source_row or declare _overmind_parent_rows. All parents are validated. Supply imported_rows up to 2000 rows/4 MiB, or artifact_cell plus artifact_fingerprint for a file uploaded as another dataset in this project. provenance describes the producer. External execution and semantic truth are not independently verified.",
        ),
        (
            "update_dataset",
            UpdateInput,
            False,
            "Set an explicit dataset name, user-selected intent, capability or active version. Intent and capability cannot change after use. Null clears capability or the default version. Selecting a version does not alter existing consumers.",
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
