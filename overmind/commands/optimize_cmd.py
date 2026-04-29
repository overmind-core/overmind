"""
Overmind optimize — Agent Optimizer

Usage:
    overmind optimize <agent-name>
    overmind optimize <agent-name> --fast
"""

import logging

from overmind import SpanType, attrs, observe, set_tag
from overmind.core.paths import load_agent_dotenv
from overmind.core.registry import get_agent_id
from overmind.optimize.config import collect_config
from overmind.optimize.optimizer import Optimizer
from overmind.storage import configure_storage

logger = logging.getLogger("overmind.commands.optimize")


@observe(span_name="overmind_optimize", type=SpanType.WORKFLOW)
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

    # CLI-level flags
    set_tag(attrs.COMMAND, "optimize")
    set_tag(attrs.OPTIMIZE_AGENT_NAME, agent_name)
    set_tag(attrs.OPTIMIZE_FAST, str(fast))

    # Refresh agent_id from registry in case setup just created/updated it
    config.agent_id = get_agent_id(agent_name)

    logger.info("optimize: storage agent_id=%s", config.agent_id)
    configure_storage(
        agent_path=config.agent_path,
        agent_id=config.agent_id,
        agent_name=agent_name,
    )

    # Config-level tags — everything the user chose or defaulted to
    set_tag(attrs.OPTIMIZE_AGENT_PATH, config.agent_path)
    set_tag(attrs.OPTIMIZE_ENTRYPOINT_FN, config.entrypoint_fn)
    set_tag(attrs.OPTIMIZE_STORAGE_BACKEND, "api")
    set_tag(attrs.OPTIMIZE_ANALYZER_MODEL, config.analyzer_model or "")
    set_tag(attrs.OPTIMIZE_LLM_JUDGE_MODEL, config.llm_judge_model or "disabled")
    set_tag(attrs.OPTIMIZE_ITERATIONS, config.iterations)
    set_tag(attrs.OPTIMIZE_CANDIDATES_PER_ITERATION, config.candidates_per_iteration)
    set_tag(attrs.OPTIMIZE_PARALLEL, config.parallel)
    set_tag(attrs.OPTIMIZE_MAX_WORKERS, config.max_workers)
    set_tag(attrs.OPTIMIZE_RUNS_PER_EVAL, config.runs_per_eval)
    set_tag(attrs.OPTIMIZE_REGRESSION_THRESHOLD, config.regression_threshold)
    set_tag(attrs.OPTIMIZE_HOLDOUT_RATIO, config.holdout_ratio)
    set_tag(attrs.OPTIMIZE_HOLDOUT_ENFORCEMENT, config.holdout_enforcement)
    set_tag(attrs.OPTIMIZE_EARLY_STOPPING_PATIENCE, config.early_stopping_patience)
    set_tag(attrs.OPTIMIZE_CROSS_RUN_PERSISTENCE, config.cross_run_persistence)
    set_tag(attrs.OPTIMIZE_FAILURE_CLUSTERING, config.failure_clustering)
    set_tag(attrs.OPTIMIZE_ADAPTIVE_FOCUS, config.adaptive_focus)
    set_tag(attrs.OPTIMIZE_MODEL_BACKTESTING, config.model_backtesting)
    if config.backtest_models:
        set_tag(attrs.OPTIMIZE_BACKTEST_MODELS, ",".join(config.backtest_models))
    set_tag(attrs.OPTIMIZE_EVAL_SPEC_PATH, config.eval_spec_path or "")
    set_tag(attrs.OPTIMIZE_DATA_PATH, config.data_path or "")

    optimizer = Optimizer(config)
    try:
        optimizer.run()
    except Exception:
        logger.exception("optimize: run failed for agent=%s", agent_name)
        raise
    logger.info("optimize: run complete agent=%s", agent_name)
