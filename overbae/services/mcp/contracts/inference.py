"""Strict contracts for deployed-model inference."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from overbae.services.mcp.contracts.common import JobReceipt, MCPModel, ResourceLinkContract


class InferenceMessage(MCPModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=32_000)


class DecisionInput(MCPModel):
    state: str = Field(max_length=128_000)
    question: str = Field(min_length=1, max_length=32_000)
    kind: Literal["choice", "noul", "score"]
    options: list[str] = Field(min_length=2, max_length=255)


class RunInferenceInput(MCPModel):
    request_key: str = Field(min_length=1, max_length=128)
    deployment: str = Field(min_length=1, max_length=255)
    messages: list[InferenceMessage] | None = Field(default=None, min_length=1, max_length=50)
    decisions: list[DecisionInput] | None = Field(default=None, min_length=1, max_length=16)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def one_contract(self):
        if bool(self.messages) == bool(self.decisions):
            raise ValueError("Supply messages or decisions, not both.")
        if self.decisions and (self.temperature is not None or self.max_tokens is not None):
            raise ValueError("Decision inference does not accept generation parameters.")
        return self


class RunInferenceOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    model_id: str
    job: JobReceipt
    resource: ResourceLinkContract


class GetModelSwapPromptInput(MCPModel):
    finetune: str = Field(min_length=1, max_length=255)
    pin: bool = Field(
        default=False,
        description="False uses the capability alias when one exists.",
    )


class GetModelSwapPromptOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    finetune: str
    pin: bool
    prompt: str = Field(min_length=1, max_length=16_000)
    capability_id: str | None
    capability_name: str = Field(max_length=255)
    old_model: str = Field(max_length=255)
    new_model: str = Field(max_length=255)
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
