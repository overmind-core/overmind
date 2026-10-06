"""Local execution for the optimiser: candidate worktrees and datapoint runs."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

TOKENS: dict[str, str] = {
    "EXPERIMENT_ID": "__EXPERIMENT_ID__",
    "CAPABILITY_ID": "__CAPABILITY_ID__",
    "CANDIDATE_ID": "__CANDIDATE_ID__",
    "ITERATION_ID": "__ITERATION_ID__",
    "PROJECT_ID": "__PROJECT_ID__",
    "DATAPOINT_INDEX": "__DATAPOINT_INDEX__",
    "TRACE_TYPE": "__TRACE_TYPE__",
    "ORIGINAL_TRACE_ID": "__ORIGINAL_TRACE_ID__",
    "DATAPOINT_INPUT": "__DATAPOINT_INPUT__",
}

# Short reference shown in codegen prompts.
REFERENCE_COMMAND_TEMPLATE = f"""\
uv run python manage.py shell <<'EOF'

def main():
    datapoint = {TOKENS["DATAPOINT_INPUT"]}
    # adapt this import + call to the capability's real entrypoint in THIS repo
    from project import trigger
    return trigger(datapoint)

main()
EOF
"""

OUTPUT_TAIL = 8_000  # chars of stdout/stderr to capture per run
REDACTED_SECRET = "[REDACTED]"
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD")


def new_traceparent() -> tuple[str, str]:
    """``(traceparent_header, trace_id)`` in W3C format, without OpenTelemetry."""
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01", trace_id


def render_command(
    template: str,
    *,
    experiment_id: str,
    capability_id: str,
    project_id: str,
    candidate_id: str,
    iteration_id: str,
    datapoint_index: int,
    trace_type: str,
    original_trace_id: str,
    datapoint_input: Any,
) -> str:
    substitutions = {
        TOKENS["EXPERIMENT_ID"]: experiment_id,
        TOKENS["CAPABILITY_ID"]: capability_id,
        TOKENS["PROJECT_ID"]: project_id,
        TOKENS["CANDIDATE_ID"]: candidate_id,
        TOKENS["ITERATION_ID"]: iteration_id,
        TOKENS["DATAPOINT_INDEX"]: str(datapoint_index),
        TOKENS["TRACE_TYPE"]: trace_type,
        TOKENS["ORIGINAL_TRACE_ID"]: original_trace_id,
        TOKENS["DATAPOINT_INPUT"]: repr(datapoint_input),
    }
    for token, value in substitutions.items():
        template = template.replace(token, value)
    return template


def run_datapoint(
    *,
    template: str,
    experiment_id: str,
    capability_id: str,
    project_id: str,
    candidate_id: str,
    iteration_id: str,
    datapoint_index: int,
    datapoint_input: Any,
    cwd: str,
    timeout: int = 600,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Run one rendered command in ``cwd``; the result is one entry for ``POST /results/``."""
    traceparent, trace_id = new_traceparent()
    command = render_command(
        template,
        experiment_id=experiment_id,
        capability_id=capability_id,
        project_id=project_id,
        candidate_id=candidate_id,
        iteration_id=iteration_id,
        datapoint_index=datapoint_index,
        trace_type="optimized",
        original_trace_id="",
        datapoint_input=datapoint_input,
    )
    env = {**os.environ, "TRACEPARENT": traceparent, **(extra_env or {})}
    result = {"candidate_id": candidate_id, "datapoint_index": datapoint_index, "trace_id": trace_id}
    try:
        proc = subprocess.run(command, shell=True, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {**result, "success": False, "output": "", "error": f"timed out after {timeout}s"}

    secrets_in_env = _secret_values(env)
    success = proc.returncode == 0
    output = _redact(proc.stdout or "", secrets_in_env)
    error = "" if success else _redact(proc.stderr or "", secrets_in_env)
    return {**result, "success": success, "output": output[-OUTPUT_TAIL:], "error": error[-OUTPUT_TAIL:]}


def _secret_values(env: dict[str, str]) -> tuple[str, ...]:
    """Longest first, so a secret containing another is redacted whole."""
    values = {str(v) for k, v in env.items() if v and any(m in str(k).upper() for m in _SECRET_ENV_MARKERS)}
    return tuple(sorted(values, key=len, reverse=True))


def _redact(text: str, secret_values: tuple[str, ...]) -> str:
    for value in secret_values:
        text = text.replace(value, REDACTED_SECRET)
    return text


def _worktrees_root(repo_cwd: str, experiment_id: str) -> Path:
    return Path(repo_cwd) / ".overmind" / "worktrees" / experiment_id[:8]


def ensure_worktree(repo_cwd: str, experiment_id: str, candidate_id: str, diff: str) -> Path:
    """A detached git worktree for the candidate, with its diff applied once on creation."""
    path = _worktrees_root(repo_cwd, experiment_id) / candidate_id[:8]
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "add", "--detach", str(path)], cwd=repo_cwd, check=True, capture_output=True)
    if diff:
        applied = _git_apply(diff, str(path))
        if applied.returncode != 0:
            raise RuntimeError(f"git apply failed for candidate {candidate_id}: {applied.stderr.strip()}")
    return path


def remove_worktrees(repo_cwd: str, experiment_id: str) -> None:
    root = _worktrees_root(repo_cwd, experiment_id)
    if root.exists():
        for worktree in root.iterdir():
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=repo_cwd, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=repo_cwd, capture_output=True)


def _git_apply(diff: str, cwd: str) -> subprocess.CompletedProcess:
    if not diff.endswith("\n"):
        diff += "\n"
    return subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"], input=diff, cwd=cwd, text=True, capture_output=True
    )
