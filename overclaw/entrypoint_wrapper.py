"""Auto-generate an OverClaw-compatible entrypoint wrapper via the coding agent.

When a user registers an agent that doesn't expose a simple
``def run(input: dict) -> dict`` function (e.g. a Google ADK agent, a
LangChain graph, or a CrewAI crew), this module uses the coding agent to
read the agent's source code, detect the framework and execution pattern,
and generate a thin wrapper file that conforms to OverClaw's I/O contract.

The generated file lives inside the instrumented copy at
``.overclaw/agents/<name>/instrumented/`` — alongside the copied agent
source so that all imports resolve correctly.  The user's original code
is never modified.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

WRAPPER_FILENAME = "_overclaw_entrypoint.py"

_DEFAULT_MODEL = "anthropic/claude-sonnet-4-6"

_WRAPPER_PROMPT = """\
You are generating an OverClaw entrypoint wrapper for an AI agent.

OverClaw optimizes agents by running them against evaluation datasets.
It needs a single Python function with this exact contract:

    def run(input_data: dict) -> dict:
        # Execute the agent with the given input
        # Return a dict with the agent's output

The function:
- Receives a dict (parsed from JSON) as input
- Must return a dict (serialized to JSON) as output
- May use asyncio.run() internally if the agent is async
- Should handle all framework setup (sessions, runners, etc.) internally

AGENT SOURCE DIRECTORY: {agent_dir}

Here is the agent's code and surrounding files:

{code_context}

YOUR TASK:
1. Read the agent code and identify the framework (Google ADK, LangChain,
   CrewAI, AutoGen, LangGraph, or custom).
2. Understand how the agent is executed (look at test files, examples,
   or main blocks for patterns).
3. Create the file: {wrapper_path}
   - Import the agent from its module using absolute imports based on
     the agent source directory structure. The agent source directory
     will be on sys.path at runtime.
   - Define exactly: def run(input_data: dict) -> dict
   - The function should extract a query/prompt from input_data
     (use input_data.get("query", "") as the primary input key)
   - Execute the agent using the framework's standard runner
   - Return {{"response": <agent_output_text>}} at minimum
   - Handle async execution with asyncio.run() if needed
4. Keep it minimal — no logging, no retries, no error wrapping.
   Just the clean bridge from dict-in to dict-out.

IMPORTANT:
- The file MUST be at exactly: {wrapper_path}
- The function MUST be named: run
- Do NOT modify any existing files
- Do NOT install packages or run the agent
- Use absolute imports that work when the agent source directory is on
  sys.path (e.g. ``from llm_red_team_agent.agent import root_agent``)
"""


def _get_model() -> str | None:
    """Return the model to use for wrapper generation, or None if unavailable."""
    for env_var in ("ANALYZER_MODEL", "ENV_SETUP_MODEL"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val

    try:
        import litellm  # noqa: F401

        return _DEFAULT_MODEL
    except ImportError:
        return None


def _gather_code_context(agent_dir: Path, max_chars: int = 30_000) -> str:
    """Build a code-context string from the agent directory for the prompt."""
    sections: list[str] = []
    total = 0

    priority_patterns = [
        "agent.py",
        "agents.py",
        "main.py",
        "app.py",
        "run.py",
        "crew.py",
        "graph.py",
        "chain.py",
        "workflow.py",
    ]
    secondary_patterns = [
        "*.py",
    ]
    test_patterns = [
        "test_*.py",
        "*_test.py",
        "tests/*.py",
        "tests/**/*.py",
        "examples/*.py",
        "example*.py",
    ]

    seen: set[Path] = set()

    def _add_file(p: Path) -> bool:
        nonlocal total
        if p in seen or not p.is_file():
            return False
        seen.add(p)
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        if total + len(content) > max_chars:
            return False
        rel = p.relative_to(agent_dir) if p.is_relative_to(agent_dir) else p
        sections.append(f"--- {rel} ---\n{content}")
        total += len(content)
        return True

    for pattern in priority_patterns:
        for p in sorted(agent_dir.rglob(pattern)):
            if p.name == WRAPPER_FILENAME:
                continue
            _add_file(p)

    for pattern in test_patterns:
        for p in sorted(agent_dir.rglob(pattern)):
            if p.name == WRAPPER_FILENAME:
                continue
            _add_file(p)

    for pattern in secondary_patterns:
        for p in sorted(agent_dir.rglob(pattern)):
            if p.name.startswith(".") or p.name == WRAPPER_FILENAME:
                continue
            if "__pycache__" in str(p) or ".venv" in str(p):
                continue
            _add_file(p)

    config_files = [
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
        "package.json",
        "README.md",
    ]
    for name in config_files:
        p = agent_dir / name
        if p.is_file():
            _add_file(p)

    return "\n\n".join(sections) if sections else "(no Python files found)"


def wrapper_dir(agent_name: str) -> Path:
    """Return the directory where the wrapper is generated.

    This is ``.overclaw/agents/<name>/instrumented/`` — the same directory
    that holds the copied agent source, so imports inside the wrapper
    resolve exactly as they would in the original project.
    """
    from overclaw.core.paths import agent_instrumented_dir

    return agent_instrumented_dir(agent_name)


def wrapper_path(agent_name: str) -> Path:
    """Full path to the generated wrapper file."""
    return wrapper_dir(agent_name) / WRAPPER_FILENAME


def wrapper_entrypoint(agent_name: str, fn_name: str = "run") -> str:
    """Return the entrypoint string for the wrapper.

    Uses a slash-based relative path (not dotted module notation) because
    the ``.overclaw`` directory name starts with a dot which cannot
    survive a dotted-module round-trip.

    Example return value::

        .overclaw/agents/gsec/instrumented/_overclaw_entrypoint:run
    """
    from overclaw.core.registry import project_root

    wp = wrapper_path(agent_name)
    rel = wp.relative_to(project_root())
    module_ref = str(rel.with_suffix(""))
    return f"{module_ref}:{fn_name}"


def generate_entrypoint_wrapper(
    agent_dir: str | Path,
    agent_name: str,
) -> Path | None:
    """Use the coding agent to generate an entrypoint wrapper.

    The wrapper is written to
    ``.overclaw/agents/<name>/instrumented/_overclaw_entrypoint.py`` —
    alongside the instrumented copy of the agent source so that all
    imports resolve correctly.  The user's original code is never modified.

    Returns the path to the generated wrapper file, or None if
    generation failed or the coding agent is unavailable.
    """
    model = _get_model()
    if not model:
        logger.debug("No model available for wrapper generation")
        return None

    try:
        from overclaw.coding_agent.agent import run as run_coding_agent
    except ImportError:
        logger.debug("Coding agent not importable")
        return None

    agent_dir = Path(agent_dir).resolve()
    dest_dir = wrapper_dir(agent_name)
    dest_dir.mkdir(parents=True, exist_ok=True)
    wp = dest_dir / WRAPPER_FILENAME

    code_context = _gather_code_context(agent_dir)

    instruction = _WRAPPER_PROMPT.format(
        agent_dir=str(agent_dir),
        wrapper_path=str(wp),
        code_context=code_context,
    )

    logger.info("Using coding agent (%s) to generate entrypoint wrapper …", model)

    try:
        run_coding_agent(
            instruction=instruction,
            model=model,
            cwd=str(dest_dir),
            max_steps=15,
        )
    except Exception as exc:
        logger.warning("Wrapper generation failed: %s", exc)
        return None

    if wp.is_file():
        logger.info("Entrypoint wrapper generated at %s", wp)
        return wp

    logger.warning("Coding agent ran but wrapper file not found at %s", wp)
    return None
