"""
OverClaw optimize — Agent Optimizer

Usage:
    overclaw optimize <agent-name>
    overclaw optimize <agent-name> --fast
"""

import logging

from rich.console import Console

from overclaw.client import get_client, get_project_id
from overclaw.commands.setup_cmd import _sync_setup_artifacts
from overclaw.core.paths import load_agent_dotenv
from overclaw.core.registry import get_agent_id
from overclaw.optimize.config import collect_config
from overclaw.optimize.optimizer import Optimizer
from overclaw.storage import configure_storage

logger = logging.getLogger("overclaw.commands.optimize")


def main(
    agent_name: str,
    fast: bool = False,
    scope_globs: list[str] | None = None,
    max_files: int | None = None,
    max_chars: int | None = None,
) -> None:
    logger.info("optimize: start agent=%s fast=%s", agent_name, fast)
    # Load agent-specific .env before anything else so the agent's credentials
    # are available throughout the entire optimize run (config collection,
    # agent execution, and evaluation).
    load_agent_dotenv(agent_name)

    config = collect_config(
        agent_name=agent_name,
        fast=fast,
        scope_globs=scope_globs,
        max_files=max_files,
        max_chars=max_chars,
    )
    logger.info(
        "optimize: collected config agent_path=%s iterations=%d parallel=%s",
        config.agent_path,
        config.iterations,
        getattr(config, "parallel", False),
    )

    # If API is configured, make sure setup artifacts are synced first and
    # refresh agent_id from registry (sync may create/update it).
    if get_client() and get_project_id():
        _sync_setup_artifacts(agent_name, config.agent_path, Console())
        config.agent_id = get_agent_id(agent_name)

    use_api_backend = bool(config.agent_id and get_client() and get_project_id())
    logger.info(
        "optimize: storage backend=%s agent_id=%s",
        "api" if use_api_backend else "fs",
        config.agent_id,
    )
    configure_storage(
        agent_path=config.agent_path,
        agent_id=config.agent_id,
        backend="api" if use_api_backend else "fs",
    )
    optimizer = Optimizer(config)
    try:
        optimizer.run()
    except Exception:
        logger.exception("optimize: run failed for agent=%s", agent_name)
        raise
    logger.info("optimize: run complete agent=%s", agent_name)
