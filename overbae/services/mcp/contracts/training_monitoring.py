from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, StrictStr

from overbae.services.mcp.contracts.common import MCPModel


class GenerationProbe(MCPModel):
    kind: Literal["classification", "exact_match", "json_schema", "json_fields"]
    labels: list[str] | None = None
    sample: StrictInt = Field(default=256, ge=1, le=10000)
    max_new_tokens: StrictInt = Field(default=128, ge=1, le=4096)
    normalization: Literal["none", "strip", "strip_thinking"] = "strip"
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    fields: list[StrictStr] | None = Field(default=None, min_length=1, max_length=64)


class EarlyStopPolicy(MCPModel):
    patience: StrictInt = Field(default=3, ge=1, le=100)
    min_delta: float = Field(default=0, ge=0, le=1000000, allow_inf_nan=False)
    warmup_checks: StrictInt = Field(default=2, ge=0, le=100)


class TrainingMonitoringPolicy(MCPModel):
    version: Literal[1] = 1
    mode: Literal["adaptive", "steps", "epoch", "off"] = "adaptive"
    initial: StrictBool = True
    final: StrictBool = True
    loss_sample: StrictInt = Field(default=2048, ge=1, le=1000000)
    train_sample: StrictInt = Field(default=256, ge=0, le=1000000)
    seed: StrictInt = Field(default=42, ge=0, le=4294967295)
    interval_steps: StrictInt | None = Field(default=None, ge=1)
    target_seconds: float = Field(default=300, ge=1, le=86400, allow_inf_nan=False)
    overhead_fraction: float = Field(default=0.1, ge=0.001, le=0.9, allow_inf_nan=False)
    max_checks: StrictInt = Field(default=12, ge=1, le=100)
    generation_every: StrictInt = Field(default=3, ge=1, le=100)
    generation: GenerationProbe | None = None
    selection: Literal["last", "development_loss"] = "last"
    early_stopping: EarlyStopPolicy | None = None
    failure_policy: Literal["continue", "stop"] | None = None
