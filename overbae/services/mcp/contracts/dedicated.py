from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from overbae.services.mcp.contracts.common import JobReceipt, MCPModel


class DedicatedGpuInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)


class DedicatedGpuOffer(MCPModel):
    gpu_type: str
    credits_per_gpu_hour: str | None = None
    ttft_ms_p50: float | None = None
    tokens_per_second_p50: float | None = None
    latency_ms_p50: float | None = None


class DedicatedGpusOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    deployment: str
    serving_shape: str
    gpus: list[DedicatedGpuOffer] = Field(max_length=20)


class DedicatedStartInput(MCPModel):
    request_key: str = Field(min_length=1, max_length=128)
    deployment: str = Field(min_length=1, max_length=255)
    gpu_type: str = Field(min_length=1, max_length=20)
    min_replicas: int = Field(default=1, ge=0, le=8)
    max_replicas: int = Field(default=1, ge=1, le=8)
    scale_to_zero: bool = False
    idle_shutdown_seconds: int = Field(default=900, ge=300, le=86400)


class DedicatedUpdateInput(MCPModel):
    dedicated_deployment: UUID
    gpu_type: str | None = None
    min_replicas: int | None = Field(default=None, ge=0, le=8)
    max_replicas: int | None = Field(default=None, ge=1, le=8)
    scale_to_zero: bool | None = None
    idle_shutdown_seconds: int | None = Field(default=None, ge=300, le=86400)

    @model_validator(mode="after")
    def require_values(self):
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("Omit unchanged settings; supplied settings cannot be null.")
        return self


class DedicatedStopInput(MCPModel):
    dedicated_deployment: UUID


class DedicatedStartupLog(MCPModel):
    stage: str
    message: str
    at: str
    worker: str
    completed: float | None = None
    total: float | None = None
    unit: str | None = None


class DedicatedStartup(MCPModel):
    operation_id: str
    stage: str
    label: str
    completed: float | None = None
    total: float | None = None
    unit: str | None = None
    available: bool
    observed_at: str | None = None
    logs: list[DedicatedStartupLog] = Field(max_length=100)
    logs_total: int = Field(ge=0)
    next_log_offset: int | None = Field(default=None, ge=0)
    truncated: bool


class DedicatedDeploymentOutput(JobReceipt):
    kind: Literal["dedicated_deployment"] = "dedicated_deployment"
    summary: str = Field(min_length=1, max_length=240)
    deployment: str
    model_id: str
    gpu_type: str
    serving_shape: str
    min_replicas: int
    max_replicas: int
    scale_to_zero: bool
    idle_shutdown_seconds: int
    running_replicas: int
    credits_per_gpu_hour: str
    accrued_credits: str
    rates_fetched_at: str | None = None
    error: str = ""
    startup: DedicatedStartup | None = None


class InspectDedicatedInput(MCPModel):
    deployment: str | None = Field(default=None, min_length=1, max_length=255)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=25, ge=1, le=50)


class DedicatedModel(MCPModel):
    deployment: str
    model_id: str
    status: str
    available: bool
    reason: str | None = None
    gpu_types: list[str] = Field(max_length=20)
    dedicated: DedicatedDeploymentOutput | None = None


class InspectDedicatedOutput(MCPModel):
    summary: str
    models: list[DedicatedModel] = Field(max_length=50)
    total: int
    next_offset: int | None = None
