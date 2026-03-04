"""
Skip / purge / clean logic for persistent E2E database state.

Each test stage registers how to check if it already completed and how to
purge its results so it can be re-run cleanly.
"""

from __future__ import annotations

import logging
from typing import Any

from helpers.api_client import OvermindAPIClient

logger = logging.getLogger(__name__)

E2E_PROJECT_NAME = "E2E Test Project"


def _find_e2e_project(client: OvermindAPIClient) -> dict | None:
    """Return the E2E project dict or None."""
    projects = client.list_projects()
    for p in projects:
        if p.get("name") == E2E_PROJECT_NAME:
            return p
    return None


def _find_completed_jobs(
    client: OvermindAPIClient,
    project_id: str,
    job_type: str,
    prompt_slug: str | None = None,
) -> list[dict]:
    """Return completed jobs matching criteria."""
    result = client.list_jobs(
        project_id=project_id, job_type=job_type, status="completed"
    )
    jobs = result.get("jobs", [])
    if prompt_slug:
        jobs = [j for j in jobs if j.get("prompt_slug") == prompt_slug]
    return jobs


# ---------------------------------------------------------------------------
# Stage check functions: return truthy if stage already produced results
# ---------------------------------------------------------------------------


def check_onboarding(client: OvermindAPIClient, **_: Any) -> bool:
    project = _find_e2e_project(client)
    if not project:
        return False
    tokens = client.list_tokens(project["project_id"])
    return any(t.get("name") in ("e2e-token", "e2e-token-resumed") for t in tokens)


def check_telemetry(
    client: OvermindAPIClient, project_id: str, provider: str = "", **_: Any
) -> bool:
    if not project_id:
        return False
    try:
        result = client.list_traces(project_id, limit=1)
        return result.get("count", 0) > 0
    except Exception:
        return False


def check_discovery(client: OvermindAPIClient, project_id: str, **_: Any) -> bool:
    if not project_id:
        return False
    try:
        agents = client.list_agents(project_id)
        return len(agents.get("data", [])) >= 2
    except Exception:
        return False


def check_evaluations(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
) -> bool:
    if not project_id or not prompt_slug:
        return False
    try:
        detail = client.get_agent_detail(prompt_slug, project_id)
        return detail.get("analytics", {}).get("scored_spans", 0) >= 10
    except Exception:
        return False


def check_tuning(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
) -> bool:
    if not project_id or not prompt_slug:
        return False
    jobs = _find_completed_jobs(client, project_id, "prompt_tuning", prompt_slug)
    return len(jobs) > 0


def check_backtesting(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
) -> bool:
    if not project_id or not prompt_slug:
        return False
    jobs = _find_completed_jobs(client, project_id, "model_backtesting", prompt_slug)
    return len(jobs) > 0


def check_review(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
) -> bool:
    if not project_id or not prompt_slug:
        return False
    try:
        detail = client.get_agent_detail(prompt_slug, project_id)
        desc = detail.get("agent_description") or {}
        return desc.get("initial_review_completed", False)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Stage purge functions: remove results so stage can be re-run
# ---------------------------------------------------------------------------


def purge_jobs_by_type(
    client: OvermindAPIClient,
    project_id: str,
    job_type: str,
    prompt_slug: str | None = None,
):
    """Delete pending and stale running jobs so stages can be re-triggered."""
    result = client.list_jobs(project_id=project_id, job_type=job_type)
    for job in result.get("jobs", []):
        if prompt_slug and job.get("prompt_slug") != prompt_slug:
            continue
        if job["status"] in ("pending", "running"):
            try:
                client.delete_job(job["job_id"])
            except Exception:
                logger.warning("Could not delete %s job %s", job_type, job["job_id"])


def purge_onboarding(client: OvermindAPIClient, **_: Any):
    project = _find_e2e_project(client)
    if project:
        try:
            client.delete_project(project["project_id"])
        except Exception as exc:
            logger.warning(
                "Cannot purge onboarding (project delete failed: %s). "
                "Use --e2e-clean or clean the DB manually.",
                exc,
            )


def purge_telemetry(client: OvermindAPIClient, **_: Any):
    logger.warning("Cannot selectively purge traces; use --e2e-clean instead")


def purge_discovery(client: OvermindAPIClient, project_id: str, **_: Any):
    if project_id:
        purge_jobs_by_type(client, project_id, "agent_discovery")


def purge_evaluations(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
):
    if project_id:
        purge_jobs_by_type(client, project_id, "judge_scoring", prompt_slug)


def purge_tuning(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
):
    if project_id:
        purge_jobs_by_type(client, project_id, "prompt_tuning", prompt_slug)


def purge_backtesting(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
):
    if project_id:
        purge_jobs_by_type(client, project_id, "model_backtesting", prompt_slug)


def purge_review(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
):
    logger.info("Review state is on the prompt; use --e2e-clean for full reset")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def check_rescore(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
) -> bool:
    """Stage is done when avg_score < 1.0 (strict criteria applied)."""
    if not project_id or not prompt_slug:
        return False
    try:
        detail = client.get_agent_detail(prompt_slug, project_id)
        analytics = detail.get("analytics", {})
        scored = analytics.get("scored_spans", 0)
        avg = analytics.get("avg_score")
        return scored >= 30 and avg is not None and avg < 0.95
    except Exception:
        return False


def purge_rescore(
    client: OvermindAPIClient, project_id: str, prompt_slug: str = "", **_: Any
):
    if project_id:
        purge_jobs_by_type(client, project_id, "judge_scoring", prompt_slug)


STAGE_REGISTRY: dict[str, dict] = {
    "onboarding": {"check": check_onboarding, "purge": purge_onboarding},
    "telemetry": {"check": check_telemetry, "purge": purge_telemetry},
    "discovery": {"check": check_discovery, "purge": purge_discovery},
    "eval": {"check": check_evaluations, "purge": purge_evaluations},
    "review": {"check": check_review, "purge": purge_review},
    "rescore": {"check": check_rescore, "purge": purge_rescore},
    "tuning": {"check": check_tuning, "purge": purge_tuning},
    "backtest": {"check": check_backtesting, "purge": purge_backtesting},
}


def clean_all(client: OvermindAPIClient):
    """Wipe all E2E data by deleting the E2E project (cascades)."""
    project = _find_e2e_project(client)
    if project:
        logger.info("Cleaning: deleting E2E project %s", project["project_id"])
        try:
            client.delete_project(project["project_id"])
        except Exception as exc:
            logger.warning(
                "Could not delete project %s via API: %s. "
                "You may need to clean the database manually.",
                project["project_id"],
                exc,
            )
    else:
        logger.info("Cleaning: no E2E project found, nothing to delete")
