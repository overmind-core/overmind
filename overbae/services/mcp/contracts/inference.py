"""Strict contracts for deployed-model inference."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract


class InferenceMessage(MCPModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = Field(min_length=1, max_length=32_000)


class RunInferenceInput(MCPModel):
    deployment: str = Field(min_length=1, max_length=255)
    messages: list[InferenceMessage] = Field(min_length=1, max_length=50)
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=2048)


class InferenceUsage(MCPModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class RunInferenceOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    model_id: str
    content: str = Field(max_length=32_000)
    usage: InferenceUsage | None = None
    latency_ms: float = Field(ge=0)
    is_cold: bool
    resource: ResourceLinkContract


class GetModelSwapPromptInput(MCPModel):
    finetune: str = Field(min_length=1, max_length=255)
    # False points the code at the capability alias, so later swaps need no code change.
    pin: bool = False


class GetModelSwapPromptOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    finetune: str
    pin: bool
    prompt: str = Field(min_length=1, max_length=16_000)
    capability_id: str
    capability_name: str = Field(max_length=255)
    old_model: str = Field(max_length=255)
    new_model: str = Field(max_length=255)
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
