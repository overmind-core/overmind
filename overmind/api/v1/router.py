"""
Core-only router assembly.

Includes all product endpoints (traces, spans, prompts, etc.) plus the
simplified core IAM endpoints (projects, tokens, user login/profile).

Enterprise (overmind_backend) builds its own router that includes the same
product endpoints but substitutes full-RBAC IAM endpoints.
"""

from fastapi import APIRouter, Depends

from overmind.api.v1.helpers.authentication import get_current_user

from overmind.api.v1.endpoints import (
    traces,
    spans,
    layers,
    proxy,
    prompts,
    backtesting,
    agent_reviews,
    suggestions,
    onboarding,
    agents,
    jobs,
)
from overmind.api.v1.endpoints.otlp import api as otlp_api
from overmind.api.v1.endpoints.iam import (
    users as core_users,
    projects as core_projects,
    tokens as core_tokens,
)

# Authenticated product endpoints
core_api_router = APIRouter(dependencies=[Depends(get_current_user)])
core_api_router.include_router(layers.router, prefix="/layers", tags=["layers"])
core_api_router.include_router(proxy.router, prefix="/proxy", tags=["proxy"])
core_api_router.include_router(traces.router, prefix="/traces", tags=["traces"])
core_api_router.include_router(spans.router, prefix="/spans", tags=["spans"])
core_api_router.include_router(prompts.router, prefix="/prompts", tags=["prompts"])
core_api_router.include_router(
    backtesting.router, prefix="/backtesting", tags=["backtesting"]
)

# Endpoints that manage their own auth (some routes are public)
core_auth_router = APIRouter()
core_auth_router.include_router(
    suggestions.router, prefix="/suggestions", tags=["suggestions"]
)
core_auth_router.include_router(agents.router, prefix="/agents", tags=["agents"])
core_auth_router.include_router(
    agent_reviews.router, prefix="/agent-reviews", tags=["agent-reviews"]
)
core_auth_router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
core_auth_router.include_router(
    onboarding.router, prefix="/onboarding", tags=["onboarding"]
)
core_auth_router.include_router(otlp_api.router, prefix="/traces", tags=["traces"])

# Core IAM (login is public, rest need auth — handled per-endpoint)
core_auth_router.include_router(core_users.router, prefix="/iam", tags=["iam"])
core_auth_router.include_router(core_projects.router, prefix="/iam", tags=["iam"])
core_auth_router.include_router(core_tokens.router, prefix="/iam", tags=["iam"])
