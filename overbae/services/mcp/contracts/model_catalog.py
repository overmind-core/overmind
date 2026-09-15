"""Strict contracts for the dataset-independent fine-tuning model catalog."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictBool, StrictInt

from overbae.services.mcp.contracts.common import MCPModel


class GetModelCatalogInput(MCPModel):
    has_tool_calling: StrictBool = False
    max_context: StrictInt | None = Field(default=None, gt=0, le=10_000_000)


class ModelCatalogTrainingMethod(MCPModel):
    enabled: bool
    context_length: int | None
    validated_context_length: bool


class ModelCatalogTrainingType(MCPModel):
    lora: ModelCatalogTrainingMethod
    full: ModelCatalogTrainingMethod


class ModelCatalogModel(MCPModel):
    id: str
    display: str
    params: str
    total_params_b: float
    context_length_sft: int
    context_length: int | None = Field(default=None, exclude_if=lambda value: value is None)
    min_batch_size: int
    max_batch_size: int
    supports_tool_calling: bool
    training_type: ModelCatalogTrainingType
    disabled: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    disabled_reason: str | None = Field(default=None, exclude_if=lambda value: value is None)


class GetModelCatalogOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    backend: Literal["together", "baseten", "modal"]
    tiers: list[Literal["compact", "small", "mid", "large"]]
    models: dict[str, list[ModelCatalogModel]]
    has_tool_calling: bool
    max_context: int | None = Field(gt=0, le=10_000_000)
