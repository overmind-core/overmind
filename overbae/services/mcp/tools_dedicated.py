from asgiref.sync import sync_to_async

from overbae.core.errors import InputValidationError
from overbae.models import DeployedModel
from overbae.services import dedicated
from overbae.services.dedicated_offer import serving_shape
from overbae.services.mcp.contracts.dedicated import (
    DedicatedDeploymentOutput,
    DedicatedGpuInput,
    DedicatedGpuOffer,
    DedicatedGpusOutput,
    DedicatedStartInput,
    DedicatedStopInput,
    DedicatedUpdateInput,
)
from overbae.services.mcp.errors import MCPError


def _deployed(context, value: str) -> DeployedModel:
    query = DeployedModel.objects.filter(project=context.project)
    row = query.filter(id=value).first() if _uuid(value) else None
    if row is None:
        row = query.filter(model_id=value).first()
    if row is None:
        raise MCPError("not_found", "Deployment not found.")
    return row


def _uuid(value: str) -> bool:
    try:
        from uuid import UUID

        UUID(value)
    except ValueError:
        return False
    return True


def _output(row) -> DedicatedDeploymentOutput:
    return DedicatedDeploymentOutput(
        summary=f"Dedicated deployment {row.status} on {row.gpu_type}.",
        id=str(row.id),
        model_id=row.public_model_id,
        status=row.status,
        gpu_type=row.gpu_type,
        min_replicas=row.min_replicas,
        max_replicas=row.max_replicas,
        scale_to_zero=row.scale_to_zero,
        idle_shutdown_seconds=row.idle_shutdown_seconds,
        running_replicas=row.running_replicas,
        credits_per_gpu_hour=str(row.credits_per_gpu_hour),
        accrued_credits=str(row.accrued_credits),
        error=row.error_message[:1_000],
    )


def _invalid(exc: InputValidationError) -> MCPError:
    return MCPError("invalid_input", exc.detail)


def list_dedicated_gpus(payload: DedicatedGpuInput, context) -> DedicatedGpusOutput:
    deployed = _deployed(context, payload.deployment)
    rows = []
    for offer in dedicated.gpu_offers(deployed):
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
    deployed = _deployed(context, payload.deployment)
    row = dedicated.live_for(deployed)
    if row is None:
        raise MCPError("not_found", "No dedicated deployment is running.")
    fields = payload.model_dump(exclude={"deployment"}, exclude_unset=True)
    try:
        row = dedicated.update(row, **fields)
    except InputValidationError as exc:
        raise _invalid(exc) from None
    return _output(row)


def stop_dedicated(payload: DedicatedStopInput, context) -> DedicatedDeploymentOutput:
    deployed = _deployed(context, payload.deployment)
    row = dedicated.live_for(deployed)
    if row is None:
        raise MCPError("not_found", "No dedicated deployment is running.")
    return _output(dedicated.stop(row))


def register_dedicated_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    tools = [
        (
            "list_dedicated_gpus",
            "List dedicated GPUs",
            "GPUs that fit, with credits per GPU-hour and measured speed.",
            DedicatedGpuInput,
            DedicatedGpusOutput,
            list_dedicated_gpus,
            True,
            True,
            "free",
            "sync",
        ),
        (
            "start_dedicated_deployment",
            "Start dedicated deployment",
            "Start a private GPU. Poll the deployment resource until online.",
            DedicatedStartInput,
            DedicatedDeploymentOutput,
            start_dedicated,
            False,
            False,
            "gpu",
            "sync",
        ),
        (
            "update_dedicated_deployment",
            "Update dedicated deployment",
            "Change GPU, replicas, scale-to-zero, or the idle timeout.",
            DedicatedUpdateInput,
            DedicatedDeploymentOutput,
            update_dedicated,
            False,
            True,
            "gpu",
            "sync",
        ),
        (
            "stop_dedicated_deployment",
            "Stop dedicated deployment",
            "Stop the dedicated deployment. Billing ends when replicas reach zero.",
            DedicatedStopInput,
            DedicatedDeploymentOutput,
            stop_dedicated,
            False,
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
                open_world=not read_only,
                required_scopes=frozenset({"overmind:read" if read_only else "overmind:deploy"}),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            sync_to_async(function, thread_sensitive=True),
        )
