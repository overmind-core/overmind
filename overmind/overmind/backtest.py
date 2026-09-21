"""OpenRouter helpers for local model comparison.

The ``/overmind backtest`` skill rewrites call sites onto ``OPENROUTER_MODEL``.
``openrouter_env`` is the overlay ``overmind optimise`` applies per hybrid
candidate and per ``model_comparison`` iteration.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
STATE_PATH = Path(".overmind") / "backtest_state.json"


def openrouter_env(model: str) -> dict[str, str]:
    """Env overlay that forces OpenAI-compatible clients onto OpenRouter + ``model``."""
    key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is required for OpenRouter routing. "
            "Export it (or set OPENAI_API_KEY) before running models."
        )
    return {
        "OPENROUTER_API_KEY": key,
        "OPENROUTER_MODEL": model,
        "OPENAI_API_KEY": key,
        "OPENAI_BASE_URL": OPENROUTER_BASE_URL,
        "OPENAI_API_BASE": OPENROUTER_BASE_URL,
        "OR_SITE_URL": os.environ.get("OR_SITE_URL", "https://overmindlab.ai"),
        "OR_APP_NAME": os.environ.get("OR_APP_NAME", "overmind-backtest"),
    }


def write_state(root: str | os.PathLike[str], payload: dict) -> Path:
    """Persist backtest experiment id next to the dataset cache."""
    path = Path(root) / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing.update(payload)
    path.write_text(json.dumps(existing, indent=2))
    return path


def rewrite_repo(root: str | os.PathLike[str] = "."):
    """Rewrite LLM clients under *root* onto OpenRouter. See openrouter_rewrite."""
    from overmind.openrouter_rewrite import rewrite_repo as _rewrite

    return _rewrite(root)
