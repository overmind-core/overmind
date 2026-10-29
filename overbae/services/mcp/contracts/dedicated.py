from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel


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
    gpus: list[DedicatedGpuOffer]


class DedicatedStartInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)
    gpu_type: str = Field(min_length=1, max_length=20)
    min_replicas: int = Field(default=1, ge=0, le=8)
    max_replicas: int = Field(default=1, ge=1, le=8)
    scale_to_zero: bool = False
    idle_shutdown_seconds: int = Field(default=900, ge=300, le=86400)


class DedicatedUpdateInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)
    gpu_type: str | None = None
    min_replicas: int | None = Field(default=None, ge=0, le=8)
    max_replicas: int | None = Field(default=None, ge=1, le=8)
    scale_to_zero: bool | None = None
    idle_shutdown_seconds: int | None = Field(default=None, ge=300, le=86400)


class DedicatedStopInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)


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
    logs: list[DedicatedStartupLog]
    truncated: bool


class DedicatedDeploymentOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    id: str
    model_id: str
    status: str
    gpu_type: str
    min_replicas: int
    max_replicas: int
    scale_to_zero: bool
    idle_shutdown_seconds: int
    running_replicas: int
    credits_per_gpu_hour: str
    accrued_credits: str
    error: str = ""
    startup: DedicatedStartup | None = None
