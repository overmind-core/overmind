"""
OrgPolicyProvider interface and core (noop) implementation.

Core endpoints (proxy.py, layers.py) use this protocol to fetch org-level
policy configuration without importing SQL models directly.

- NoopOrgPolicyProvider: core default — returns empty/None for everything.
- Enterprise provides SqlOrgPolicyProvider which wraps Valkey-cached DB queries.
"""

from typing import Any, Dict, List, Optional, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class OrgPolicyProvider(Protocol):
    async def get_org_policy_version(
        self, organisation_id: UUID, db: AsyncSession
    ) -> Optional[Any]:
        ...

    async def get_org_llm_policies(
        self, db: AsyncSession, current_user: Any
    ) -> Dict[str, List[Any]]:
        ...

    async def get_org_mcp_policy(
        self, organisation_id: UUID, db: AsyncSession
    ) -> Optional[Dict]:
        ...


class NoopOrgPolicyProvider:
    """Core default: no org-level policies configured."""

    async def get_org_policy_version(
        self, organisation_id: UUID, db: AsyncSession
    ) -> Optional[Any]:
        return None

    async def get_org_llm_policies(
        self, db: AsyncSession, current_user: Any
    ) -> Dict[str, List[Any]]:
        return {"input": [], "output": []}

    async def get_org_mcp_policy(
        self, organisation_id: UUID, db: AsyncSession
    ) -> Optional[Dict]:
        return None
