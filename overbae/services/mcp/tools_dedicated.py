from asgiref.sync import sync_to_async
from django.db.models import Prefetch

from overbae.core.errors import InputValidationError
from overbae.models import DedicatedDeployment, DeployedModel
from overbae.services import dedicated
from overbae.services.dedicated_offer import gpu_names_for, serving_shape
from overbae.services.mcp.contracts.dedicated import (
    DedicatedDeploymentOutput,
    DedicatedGpuInput,
    DedicatedGpuOffer,
    DedicatedGpusOutput,
    DedicatedModel,
    DedicatedStartInput,
    DedicatedStopInput,
    DedicatedUpdateInput,
    InspectDedicatedInput,
    InspectDedicatedOutput,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.references import uuid_reference
from overbae.services.mcp.resources import resource_link


def _deployed(context, value: str) -> DeployedModel:
    query = DeployedModel.objects.filter(project=context.project)
    row = query.filter(id=uuid_reference(value)).first() if uuid_reference(value) else None
    if row is None:
        row = query.filter(model_id=value).first()
    if row is None:
        raise MCPError("deployment_not_found", "Deployment not found in this project.")
    return row


def _output(row) -> DedicatedDeploymentOutput:
    return DedicatedDeploymentOutput(
        summary=f"Dedicated deployment {row.status} on {row.gpu_type}.",
        **dedicated.snapshot(row),
        resource=resource_link("dedicated-deployments", str(row.pk), "Dedicated deployment"),
    )


def _dedicated(context, identifier):
    row = (
        DedicatedDeployment.objects.select_related("deployed_model")
        .filter(project=context.project, pk=identifier)
        .first()
    )
    if row is None:
        raise MCPError("deployment_not_found", "Dedicated deployment not found in this project.")
    return row


def _invalid(exc: InputValidationError) -> MCPError:
    code = (
        "request_key_conflict"
        if isinstance(exc, dedicated.RequestKeyConflictError)
        else "invalid_input"
    )
    return MCPError(code, exc.detail)


def inspect_dedicated(payload: InspectDedicatedInput, context) -> InspectDedicatedOutput:
    query = DeployedModel.objects.filter(project=context.project).order_by("-created_at", "-id")
    if payload.deployment:
        model = _deployed(context, payload.deployment)
        query = query.filter(pk=model.pk)
    total = query.count()
    models = query.prefetch_related(
        Prefetch(
            "dedicated_deployments",
            queryset=DedicatedDeployment.objects.select_related("deployed_model").order_by(
                "-created_at"
            )[:1],
            to_attr="latest_dedicated",
        )
    )[payload.offset : payload.offset + payload.limit]
    rows = []
    for model in models:
        reason = None
        names = []
        if model.status != "ready":
            reason = dedicated.NOT_READY
        else:
            try:
                names = gpu_names_for(model)
                if not names:
                    reason = (
                        "No supported GPU has enough memory for this model at its serving context."
                    )
            except InputValidationError as exc:
                reason = exc.detail
        rows.append(
            DedicatedModel(
                deployment=str(model.pk),
                model_id=model.model_id,
                status=model.status,
                available=reason is None,
                reason=reason,
                gpu_types=names,
                dedicated=_output(model.latest_dedicated[0]) if model.latest_dedicated else None,
            )
        )
    end = payload.offset + len(rows)
    return InspectDedicatedOutput(
        summary=f"{total} models.",
        models=rows,
        total=total,
        next_offset=end if end < total else None,
    )


def list_dedicated_gpus(payload: DedicatedGpuInput, context) -> DedicatedGpusOutput:
    deployed = _deployed(context, payload.deployment)
    try:
        offers = dedicated.gpu_offers(deployed)
    except InputValidationError as exc:
        raise _invalid(exc) from None
    rows = []
    for offer in offers:
        credits = offer["credits_per_gpu_hour"]
        rows.append(
            DedicatedGpuOffer(
                gpu_type=offer["gpu_type"],
                credits_per_gpu_hour=None if credits is None else str(credits),
                ttft_ms_p50=offer["ttft_ms_p50"],
                tokens_per_second_p50=offer["tokens_per_second_p50"],
                latency_ms_p50=offer["latency_ms_p50"],
            )
        )
    return DedicatedGpusOutput(
        summary=f"{len(rows)} GPUs can run this model.",
        deployment=str(deployed.id),
        serving_shape=serving_shape(deployed),
        gpus=rows,
    )


def start_dedicated(payload: DedicatedStartInput, context) -> DedicatedDeploymentOutput:
    deployed = _deployed(context, payload.deployment)
    try:
        row = dedicated.start(
            deployed,
            context.user,
            request_key=payload.request_key,
            gpu_type=payload.gpu_type,
            min_replicas=payload.min_replicas,
            max_replicas=payload.max_replicas,
            scale_to_zero=payload.scale_to_zero,
            idle_shutdown_seconds=payload.idle_shutdown_seconds,
        )
    except InputValidationError as exc:
        raise _invalid(exc) from None
    return _output(row)


def update_dedicated(payload: DedicatedUpdateInput, context) -> DedicatedDeploymentOutput:
    row = _dedicated(context, payload.dedicated_deployment)
    fields = payload.model_dump(exclude={"dedicated_deployment"}, exclude_unset=True)
    try:
        row = dedicated.update(row, **fields)
    except InputValidationError as exc:
        raise _invalid(exc) from None
    return _output(row)


def stop_dedicated(payload: DedicatedStopInput, context) -> DedicatedDeploymentOutput:
    row = _dedicated(context, payload.dedicated_deployment)
    return _output(dedicated.stop(row))


def register_dedicated_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    tools = [
        (
            "inspect_dedicated_inference",
            "Dedicated inference",
            "Inspect saved eligibility/state; optional deployment and offset/limit. Passive.",
            InspectDedicatedInput,
            InspectDedicatedOutput,
            inspect_dedicated,
            True,
            True,
            False,
            "free",
            "sync",
        ),
        (
            "list_dedicated_gpus",
            "List dedicated GPUs",
            "Quote GPU-hour credits and measured speed; unknowns stay null.",
            DedicatedGpuInput,
            DedicatedGpusOutput,
            list_dedicated_gpus,
            True,
            True,
            True,
            "free",
            "sync",
        ),
        (
            "start_dedicated_deployment",
            "Start dedicated deployment",
            "Start GPUs with request_key; retries reuse, changed settings conflict. Poll resource. Hourly billing.",
            DedicatedStartInput,
            DedicatedDeploymentOutput,
            start_dedicated,
            False,
            True,
            True,
            "gpu",
            "job",
        ),
        (
            "update_dedicated_deployment",
            "Update dedicated deployment",
            "Change GPU, replicas or idle timeout.",
            DedicatedUpdateInput,
            DedicatedDeploymentOutput,
            update_dedicated,
            False,
            True,
            True,
            "gpu",
            "sync",
        ),
        (
            "stop_dedicated_deployment",
            "Stop dedicated deployment",
            "Drain GPUs; billing ends at zero.",
            DedicatedStopInput,
            DedicatedDeploymentOutput,
            stop_dedicated,
            False,
            True,
            True,
            "gpu",
            "sync",
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
        idempotent,
        open_world,
        cost_class,
        async_mode,
    ) in tools:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=idempotent,
                open_world=open_world,
                required_scopes=frozenset({"overmind:read" if read_only else "overmind:deploy"}),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            sync_to_async(function, thread_sensitive=True),
        )
