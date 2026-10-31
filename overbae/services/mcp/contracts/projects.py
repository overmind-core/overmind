"""Strict contracts for project and account MCP tools."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from overbae.services.mcp.contracts.common import MCPModel

ExpiryDays = Literal[0, 30, 90, 180, 365]


class CreateApiKeyInput(MCPModel):
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=2_000)
    expires_in_days: ExpiryDays = 365
    project_id: str | None = None


class CreateApiKeyOutput(MCPModel):
    id: str
    key: str = Field(
        min_length=1,
        max_length=128,
        description="Plaintext secret. Returned only in this response.",
    )
    name: str = Field(max_length=255)
    description: str = Field(max_length=2_000)
    prefix: str = Field(max_length=12)
    scope: Literal["account", "project"]
    project_id: str | None
    expires_at: datetime | None
    created_at: datetime


class GetCreditBalanceInput(MCPModel):
    since: AwareDatetime | None = Field(
        default=None, description="Inclusive ledger timestamp; omit for lifetime usage."
    )


class ServiceCreditUsage(MCPModel):
    service: str = Field(max_length=32)
    spent_credits: Decimal


class CreditUsage(MCPModel):
    spent_credits: Decimal
    by_service: list[ServiceCreditUsage] = Field(max_length=100)


class GetCreditBalanceOutput(MCPModel):
    billing_enabled: bool
    balance_credits: Decimal
    granted_credits: Decimal
    spent_credits: Decimal
    credits_per_usd: Literal[100] = 100
    observed_at: datetime
    since: datetime | None
    usage: CreditUsage
