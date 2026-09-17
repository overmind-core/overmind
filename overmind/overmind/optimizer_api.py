"""HTTP client + datapoint runner for client-driven optimizer / backtest loops."""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

import requests

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
MIN_ITERATION_IMPROVEMENT = 1.0  # minimum score delta counted as improvement

_EVALUATED_STATUSES = frozenset({
    "evaluated_baseline_outputs",
    "evaluated_candidate_outputs",
    "iterating",
    "completed",
    "failed",
    "cancelled",
})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _new_traceparent() -> tuple[str, str]:
    """Return (traceparent_header, trace_id) — W3C format, no OTel dependency."""
    trace_id = secrets.token_hex(16)
    span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01", trace_id


def _secret_env_values(env: dict[str, str]) -> tuple[str, ...]:
    markers = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    values = {str(v) for k, v in env.items() if v and any(m in str(k).upper() for m in markers)}
    return tuple(sorted(values, key=len, reverse=True))


def _redact_secret_values(text: str, secret_values: tuple[str, ...]) -> str:
    for v in secret_values:
        text = text.replace(v, REDACTED_SECRET)
    return text


def _git_apply(diff_text: str, cwd: str, *, index: bool = False) -> subprocess.CompletedProcess:
    if diff_text and not diff_text.endswith("\n"):
        diff_text += "\n"
    cmd = ["git", "apply", "--whitespace=nowarn"]
    if index:
        cmd.append("--index")
    cmd.append("-")
    return subprocess.run(
        cmd,
        input=diff_text,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


def _render_command(
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
    subs = {
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
    result = template
    for token, value in subs.items():
        result = result.replace(token, value)
    return result


def _worktree_path(repo_cwd: str, experiment_id: str, candidate_id: str) -> Path:
    return Path(repo_cwd) / ".overmind" / "worktrees" / experiment_id[:8] / candidate_id[:8]


def _ensure_worktree(repo_cwd: str, experiment_id: str, candidate_id: str, diff: str) -> Path:
    """Create (once) a git worktree for a candidate and apply its diff."""
    path = _worktree_path(repo_cwd, experiment_id, candidate_id)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(path)],
            cwd=repo_cwd,
            check=True,
            capture_output=True,
        )
        if diff:
            result = _git_apply(diff, str(path))
            if result.returncode != 0:
                raise RuntimeError(f"git apply failed for candidate {candidate_id}: {result.stderr.strip()}")
    return path


def _remove_worktrees(repo_cwd: str, experiment_id: str) -> None:
    """Remove all worktrees for an experiment and prune the worktree list."""
    base = Path(repo_cwd) / ".overmind" / "worktrees" / experiment_id[:8]
    if base.exists():
        for wt in base.iterdir():
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(wt)],
                cwd=repo_cwd,
                capture_output=True,
            )
    subprocess.run(["git", "worktree", "prune"], cwd=repo_cwd, capture_output=True)


def _run_datapoint(
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
    """Run one rendered command in ``cwd``, return result dict for POST /results/."""
    traceparent, trace_id = _new_traceparent()
    rendered = _render_command(
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
    secret_values = _secret_env_values(env)
    try:
        proc = subprocess.run(
            rendered,
            shell=True,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        success = proc.returncode == 0
        output = _redact_secret_values(proc.stdout or "", secret_values)
        error = "" if success else _redact_secret_values(proc.stderr or "", secret_values)
        return {
            "candidate_id": candidate_id,
            "datapoint_index": datapoint_index,
            "success": success,
            "output": output[-OUTPUT_TAIL:],
            "trace_id": trace_id,
            "error": error[-OUTPUT_TAIL:],
        }
    except subprocess.TimeoutExpired:
        return {
            "candidate_id": candidate_id,
            "datapoint_index": datapoint_index,
            "success": False,
            "output": "",
            "trace_id": trace_id,
            "error": f"timed out after {timeout}s",
        }


def _raise_with_detail(resp: requests.Response) -> None:
    """``raise_for_status`` that carries the server's reason: a refusal such as
    "X is a train dataset; this needs eval." is the message, not "Bad Request"."""
    if resp.ok:
        return
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        parts = [
            f"{field}: {'; '.join(map(str, reason)) if isinstance(reason, list) else reason}"
            for field, reason in body.items()
        ]
        detail = " ".join(parts)
    else:
        detail = (resp.text or "").strip()
    raise requests.HTTPError(f"HTTP {resp.status_code} from {resp.url}: {detail[:600] or resp.reason}", response=resp)


class OptimizerAPI:
    """Thin HTTP client for the optimizer-experiments write API.

    Endpoints are the planned client-write surface on
    ``/api/optimizer-experiments/{id}/``.  Tests mock at the requests layer.
    """

    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update({"X-Api-Key": api_key, "Content-Type": "application/json"})

    def _exp_url(self, experiment_id: str, *parts: str) -> str:
        return "/".join([f"{self.base_url}/api/optimizer-experiments/{experiment_id}", *parts, ""])

    def create_experiment(
        self,
        *,
        capability_id: str,
        dataset_id: str,
        eval_set_id: str = "",
        mode: str = "optimize",
        num_iterations: int = 5,
        num_candidates_per_iteration: int = 3,
        max_iterations_without_improvement: int = 3,
        model_ids: list[str] | None = None,
        openrouter_key_source: str = "local",
    ) -> dict:
        payload: dict[str, Any] = {
            "capability": capability_id,
            "dataset": dataset_id,
            "mode": mode,
            "num_iterations": num_iterations,
            "num_candidates_per_iteration": num_candidates_per_iteration,
            "max_iterations_without_improvement": max_iterations_without_improvement,
            "openrouter_key_source": openrouter_key_source,
        }
        if eval_set_id:
            payload["eval_set"] = eval_set_id
        if model_ids:
            payload["model_ids"] = model_ids
        resp = self._session.post(
            f"{self.base_url}/api/optimizer-experiments/",
            json=payload,
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()

    def list_iterations(self, experiment_id: str) -> list[dict]:
        resp = self._session.get(self._exp_url(experiment_id, "iterations"), timeout=30)
        _raise_with_detail(resp)
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            return list(data["results"])
        if isinstance(data, list):
            return data
        return []

    def get_experiment(self, experiment_id: str) -> dict:
        resp = self._session.get(self._exp_url(experiment_id), timeout=30)
        _raise_with_detail(resp)
        exp = resp.json()
        # Retrieve does not nest iterations; attach them for client FSM callers.
        try:
            exp["iterations"] = self.list_iterations(experiment_id)
        except Exception:  # noqa: BLE001 — status callers still work without the tree
            exp.setdefault("iterations", [])
        return exp

    def set_template(self, experiment_id: str, template: str) -> dict:
        resp = self._session.post(
            self._exp_url(experiment_id, "template"),
            json={"template": template},
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()

    def add_iteration(
        self,
        experiment_id: str,
        *,
        order: int,
        name: str,
        candidates: list[dict],
    ) -> dict:
        resp = self._session.post(
            self._exp_url(experiment_id, "add-iteration"),
            json={"order": order, "name": name, "candidates": candidates},
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()

    def post_results(self, experiment_id: str, results: list[dict]) -> dict:
        resp = self._session.post(
            self._exp_url(experiment_id, "results"),
            json={"results": results},
            timeout=60,
        )
        _raise_with_detail(resp)
        return resp.json()

    def evaluate(self, experiment_id: str, order: int) -> dict:
        resp = self._session.post(
            self._exp_url(experiment_id, "evaluate"),
            json={"order": order},
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()

    def complete(self, experiment_id: str) -> dict:
        resp = self._session.post(
            self._exp_url(experiment_id, "complete"),
            json={},
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()

    def export_dataset(self, dataset_id: str, cell_id: str, cache_dir: Path, *, fingerprint: str = "") -> Path:
        """The used version's JSONL, cached as ``<cell>.jsonl`` with its frame
        fingerprint beside it. A cache whose fingerprint is missing or differs
        from the version is fetched again."""
        if not cell_id:
            raise RuntimeError("The experiment has no used version.")
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{cell_id}.jsonl"
        hash_file = cache_dir / f"{cell_id}.hash"
        if cached.exists() and hash_file.exists():
            stored = hash_file.read_text().strip()
            if stored and (not fingerprint or stored == fingerprint):
                return cached
        resp = self._session.get(
            f"{self.base_url}/api/datasets/{dataset_id}/export/",
            params={"fmt": "jsonl", "cell": cell_id},
            timeout=120,
            stream=True,
        )
        _raise_with_detail(resp)
        partial = cached.with_suffix(".part")
        with partial.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=65_536):
                fh.write(chunk)
        partial.replace(cached)
        hash_file.write_text(resp.headers.get("X-Overmind-Fingerprint") or fingerprint)
        return cached

    def get_candidate(self, candidate_id: str) -> dict:
        resp = self._session.get(
            f"{self.base_url}/api/optimizer-candidates/{candidate_id}/",
            timeout=30,
        )
        _raise_with_detail(resp)
        return resp.json()
