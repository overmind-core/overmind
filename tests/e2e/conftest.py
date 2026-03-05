"""
E2E test configuration: session fixtures, CLI flags, skip/purge logic.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

# Ensure the e2e directory is on sys.path so helpers/mock_agents are importable
E2E_DIR = Path(__file__).resolve().parent
REPORTS_DIR = E2E_DIR / "reports"
if str(E2E_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_DIR))

from helpers.api_client import OvermindAPIClient  # noqa: E402
from helpers.llm_cache import LLMCache  # noqa: E402
from helpers.state_manager import (  # noqa: E402
    STAGE_REGISTRY,
    clean_all,
    _find_e2e_project,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML report (auto-generated with timestamp)
# ---------------------------------------------------------------------------


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config):
    if not getattr(config.option, "htmlpath", None):
        REPORTS_DIR.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        config.option.htmlpath = str(REPORTS_DIR / f"report_{ts}.html")


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser):
    parser.addoption(
        "--e2e-rerun",
        action="store_true",
        default=False,
        help="Purge previous results for each stage before running.",
    )
    parser.addoption(
        "--e2e-clean",
        action="store_true",
        default=False,
        help="Wipe ALL E2E test data at session start, then run everything fresh.",
    )


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.environ.get("E2E_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def overmind_client(base_url: str) -> OvermindAPIClient:
    client = OvermindAPIClient(base_url, timeout=60.0)
    assert client.health(), f"Overmind API not reachable at {base_url}"
    client.login()
    yield client
    client.close()


@pytest.fixture(scope="session")
def llm_cache() -> LLMCache:
    return LLMCache(E2E_DIR / "cache")


@pytest.fixture(scope="session")
def e2e_mode(request: pytest.FixtureRequest) -> str:
    """Return 'clean', 'rerun', or 'default'."""
    if request.config.getoption("--e2e-clean"):
        return "clean"
    if request.config.getoption("--e2e-rerun"):
        return "rerun"
    return "default"


def _restore_qa_agent_slug(
    client: OvermindAPIClient, project_id: str, state: dict
) -> None:
    """Identify the QA agent by checking prompt text for FUNCTION_EXECUTOR."""
    for slug in state["prompt_slugs"]:
        try:
            detail = client.get_agent_detail(slug, project_id)
            versions = detail.get("versions", [])
            prompt_text = versions[0].get("prompt_text", "") if versions else ""
            if "FUNCTION_EXECUTOR" not in (prompt_text or ""):
                state["qa_agent_slug"] = slug
                return
        except Exception:
            continue


@pytest.fixture(scope="session")
def shared_state(
    overmind_client: OvermindAPIClient,
    e2e_mode: str,
) -> dict:
    """
    Mutable dict shared across all tests in the session.

    Pre-populated with project_id / api_token from existing E2E data if
    the database already contains them (supports skip semantics).
    """
    state: dict = {
        "project_id": None,
        "api_token": None,
        "prompt_slugs": {},
        "_completed_stages": set(),
    }

    if e2e_mode == "clean":
        clean_all(overmind_client)

    project = _find_e2e_project(overmind_client)
    if project:
        pid = project["project_id"]
        state["project_id"] = pid

        # The plain-text token is only returned on creation, not from list.
        # Create a fresh token so the mock agents can authenticate.
        import time as _time

        token_name = f"e2e-token-{int(_time.time())}"
        try:
            token_data = overmind_client.create_token(pid, name=token_name)
            state["api_token"] = token_data["token"]
        except Exception as exc:
            logger.warning(
                "Could not create resumed token for project %s: %s", pid, exc
            )

        try:
            agents = overmind_client.list_agents(pid)
            for agent in agents.get("data", []):
                slug = agent["slug"]
                state["prompt_slugs"][slug] = agent["prompt_id"]
            if state["prompt_slugs"]:
                _restore_qa_agent_slug(overmind_client, pid, state)
        except Exception:
            pass

    # Snapshot which stages are already complete at session start.
    # Only these will be auto-skipped; stages that complete mid-session won't be.
    for stage_name, entry in STAGE_REGISTRY.items():
        check_kwargs = _build_check_kwargs(state, stage_name)
        try:
            if entry["check"](overmind_client, **check_kwargs):
                state["_completed_stages"].add(stage_name)
        except Exception:
            pass

    logger.info("Stages already completed: %s", state["_completed_stages"] or "none")
    return state


# ---------------------------------------------------------------------------
# Auto-skip / auto-purge based on stage markers
# ---------------------------------------------------------------------------

STAGE_MARKER_PREFIX = "stage_"


@pytest.fixture(autouse=True)
def handle_stage_state(
    request: pytest.FixtureRequest,
    overmind_client: OvermindAPIClient,
    e2e_mode: str,
    shared_state: dict,
):
    """Skip or purge stages depending on the e2e_mode and existing DB state.

    Only stages that were complete *at session start* are skipped; stages that
    become complete during the current run are not re-checked per-test.
    """
    stage_name = _get_stage_name(request)
    if not stage_name or e2e_mode == "clean":
        return

    completed_at_start = shared_state.get("_completed_stages", set())

    if e2e_mode == "default" and stage_name in completed_at_start:
        pytest.skip(
            f"Stage '{stage_name}' already completed (use --e2e-rerun to force)"
        )

    if e2e_mode == "rerun" and stage_name in completed_at_start:
        registry_entry = STAGE_REGISTRY.get(stage_name)
        if registry_entry:
            purge_fn = registry_entry["purge"]
            check_kwargs = _build_check_kwargs(shared_state, stage_name)
            logger.info("Purging previous results for stage '%s'", stage_name)
            purge_fn(overmind_client, **check_kwargs)
            completed_at_start.discard(stage_name)


def _get_stage_name(request: pytest.FixtureRequest) -> str | None:
    """Extract the stage name from pytest markers (e.g. 'stage_backtest' -> 'backtest')."""
    for marker in request.node.iter_markers():
        if marker.name.startswith(STAGE_MARKER_PREFIX):
            return marker.name[len(STAGE_MARKER_PREFIX) :]
    return None


def _build_check_kwargs(shared_state: dict, stage_name: str) -> dict:
    """Build kwargs for check/purge functions from shared state."""
    kwargs: dict = {}
    pid = shared_state.get("project_id")
    if pid:
        kwargs["project_id"] = pid

    slugs = shared_state.get("prompt_slugs", {})
    if stage_name in ("eval", "review", "rescore", "tuning", "backtest") and slugs:
        first_slug = next(iter(slugs))
        kwargs["prompt_slug"] = first_slug

    return kwargs
