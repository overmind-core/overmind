"""Structured logging helpers for the OverClaw CLI.

This module provides three small utilities used across the commands and
setup layers:

* :func:`setup_logging` — call once from ``overclaw`` CLI entry to
  initialise a rotating file log under ``.overclaw/logs/``.
* :func:`stage` — context manager that records the entry, outcome, and
  duration of a named pipeline stage (setup analysis, codegen, etc.).
* :func:`log_prompt` — records that the user answered an interactive
  prompt so we have an audit trail of what the user saw vs typed.

The implementation is intentionally tiny — structured logs are a thin
layer over the standard ``logging`` module.  Log records carry an
``extra`` dict so downstream processors (``jq``, Datadog, Sentry) can
filter on it without parsing free-form messages.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from collections.abc import Iterator

from overclaw.core.constants import OVERCLAW_DIR_NAME

_LOGGER_NAME = "overclaw"
_ROOT_CONFIGURED = False


def _find_overclaw_dir(start: Path | None = None) -> Path | None:
    """Walk upward from *start* looking for a ``.overclaw/`` directory."""
    cur = (start or Path.cwd()).resolve()
    while True:
        candidate = cur / OVERCLAW_DIR_NAME
        if candidate.is_dir():
            return candidate
        if cur.parent == cur:
            return None
        cur = cur.parent


def setup_logging(level: int | None = None) -> Path:
    """Configure the ``overclaw`` logger to write to ``.overclaw/logs/``.

    Idempotent: subsequent calls reuse the same handlers.  The log level
    is read from ``OVERCLAW_LOG_LEVEL`` (default ``INFO``) unless *level*
    is provided explicitly.  Returns the path to the active log file.
    """
    global _ROOT_CONFIGURED

    overclaw_dir = _find_overclaw_dir() or (Path.cwd() / OVERCLAW_DIR_NAME)
    log_dir = overclaw_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "overclaw.log"

    if _ROOT_CONFIGURED:
        return log_path

    if level is None:
        level_name = os.environ.get("OVERCLAW_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    _ROOT_CONFIGURED = True
    return log_path


@contextmanager
def stage(
    name: str,
    *,
    logger: logging.Logger | None = None,
    **context: Any,
) -> Iterator[dict[str, Any]]:
    """Record the lifecycle of a named pipeline stage.

    Usage::

        with stage("setup.policy.generate", logger=logger, model=model) as info:
            ...
            info["policy_default"] = True  # enrich the exit record

    Emits two log records:
    * ``stage.start`` — when the block is entered
    * ``stage.end`` (or ``stage.error``) — when it exits, with elapsed_ms
    """
    log = logger or logging.getLogger(_LOGGER_NAME)
    info: dict[str, Any] = dict(context)
    started = time.monotonic()
    log.info("stage.start name=%s context=%s", name, context)
    try:
        yield info
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000, 1)
        log.error(
            "stage.error name=%s elapsed_ms=%s error=%s info=%s",
            name,
            elapsed,
            f"{type(exc).__name__}: {exc}",
            info,
        )
        raise
    else:
        elapsed = round((time.monotonic() - started) * 1000, 1)
        log.info("stage.end name=%s elapsed_ms=%s info=%s", name, elapsed, info)


def log_prompt(
    title: str,
    value: Any,
    *,
    kind: str = "input",
    default: Any | None = None,
    logger: logging.Logger | None = None,
) -> None:
    """Record that the user responded to an interactive prompt."""
    log = logger or logging.getLogger(_LOGGER_NAME)
    log.info(
        "prompt kind=%s title=%r default=%r value=%r",
        kind,
        title,
        default,
        value,
    )
