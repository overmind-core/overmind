"""Client-driven optimiser loop — skill generates diffs/commands; server scores.

Driven by the ``/overmind optimise`` skill. SDK verbs: ``overmind optimise start`` /
``next``. Winning diffs stay on the candidate ``code_path``; apply them locally.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from overmind.backtest import openrouter_env
from overmind.optimizer_api import (
    _EVALUATED_STATUSES,
    _TERMINAL_STATUSES,
    OUTPUT_TAIL,
    REFERENCE_COMMAND_TEMPLATE,
    OptimizerAPI,
    _ensure_worktree,
    _remove_worktrees,
    _run_datapoint,
)


def command_template_prompt(*, capability_name: str = "", entrypoint: str = "") -> str:
    return (
        "Write EXACTLY ONE reusable shell command that runs the capability on a single "
        "datapoint and prints its output.\n\n"
        f"## Capability\n- name: {capability_name or '(from overmind.toml)'}\n"
        f"- entrypoint: {entrypoint or '(discover in repo)'}\n\n"
        "Leave these tokens verbatim (substituted per datapoint):\n"
        "  __DATAPOINT_INPUT__, __EXPERIMENT_ID__, __CAPABILITY_ID__, __CANDIDATE_ID__,\n"
        "  __ITERATION_ID__, __DATAPOINT_INDEX__, __PROJECT_ID__\n\n"
        f"## Reference shape\n```\n{REFERENCE_COMMAND_TEMPLATE}```\n"
        "Adapt the import to this repo, keep tokens unchanged, write the file, then:\n\n"
        "  overmind optimise set-template <file>\n"
    )


def candidate_prompt(
    *,
    capability_name: str = "",
    entrypoint: str = "",
    scores: dict | None = None,
    n_candidates: int = 3,
) -> str:
    return (
        f"Write {n_candidates} candidate unified git diff(s) that improve "
        f"{capability_name or 'the capability'} "
        f"(entrypoint `{entrypoint or 'discover in repo'}`).\n\n"
        f"Scores so far: {json.dumps(scores or {})}\n\n"
        "Each patch is a git unified diff against the current repo. Keep the entrypoint "
        "signature. Do not hardcode datapoint answers.\n\n"
        "Then:\n"
        "  overmind optimise add-candidate --diff <file>\n"
        "  overmind optimise run-iteration\n"
    )


class OptimiseLoop:
    """Template → smoke → baseline → iterations → complete."""

    def __init__(
        self,
        api: OptimizerAPI,
        experiment_id: str,
        *,
        repo_cwd: str,
        dataset_path: Path,
        state_path: Path,
        timeout: int = 600,
        poll_interval: float = 5.0,
        poll_timeout: float = 600.0,
    ) -> None:
        self.api = api
        self.experiment_id = experiment_id
        self.repo_cwd = repo_cwd
        self.dataset_path = dataset_path
        self.state_path = state_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def _load_dataset(self) -> list[dict]:
        rows: list[dict] = []
        with self.dataset_path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def _save_state(self, state: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2))

    def _poll_until_evaluated(self) -> dict:
        deadline = time.monotonic() + self.poll_timeout
        while time.monotonic() < deadline:
            exp = self.api.get_experiment(self.experiment_id)
            status = exp.get("status", "")
            if status in _EVALUATED_STATUSES or status in _TERMINAL_STATUSES:
                return exp
            time.sleep(self.poll_interval)
        raise TimeoutError(f"Optimise {self.experiment_id} did not finish scoring within {self.poll_timeout}s")

    def next_action(self) -> dict[str, Any]:
        exp = self.api.get_experiment(self.experiment_id)
        status = exp.get("status", "")
        local = self._load_state()
        iterations = exp.get("iterations") or []

        if status in _TERMINAL_STATUSES:
            return {"action": "DONE", "experiment": exp, "scores": exp.get("scores") or {}}

        if not exp.get("command_template"):
            return {
                "action": "WRITE_COMMAND_TEMPLATE",
                "prompt": command_template_prompt(
                    capability_name=str(exp.get("capability_name") or ""),
                    entrypoint=str(exp.get("entrypoint") or ""),
                ),
                "experiment": exp,
            }

        if not local.get("smoke_done"):
            return {"action": "RUN_SMOKE", "experiment": exp}

        if status in ("evaluating_baseline_outputs", "evaluating_candidate_outputs"):
            return {
                "action": "WAIT",
                "message": "Server is scoring — run `overmind optimise next` again shortly.",
                "experiment": exp,
            }

        has_baseline = any(int(it.get("order") or 0) == 0 for it in iterations)
        if not has_baseline:
            return {"action": "RUN_BASELINE", "experiment": exp}

        mode = exp.get("mode") or "optimize"
        max_iter = int(exp.get("num_iterations") or 5)
        current = int(exp.get("current_iteration") or 0)
        if mode == "model_comparison":
            models = list(exp.get("model_ids") or [])
            order = int(local.get("next_order") or 1)
            if current >= max_iter or order < 1 or order > len(models):
                return {
                    "action": "COMPLETE",
                    "experiment": exp,
                    "scores": exp.get("scores") or {},
                }
            return {
                "action": "RUN_ITERATION",
                "target_model": models[order - 1],
                "experiment": exp,
            }

        n_cand = int(exp.get("num_candidates_per_iteration") or 3)
        pending = list(local.get("pending_diffs") or [])
        if pending:
            return {
                "action": "RUN_ITERATION",
                "pending": len(pending),
                "experiment": exp,
            }

        if current >= max_iter:
            return {
                "action": "COMPLETE",
                "experiment": exp,
                "scores": exp.get("scores") or {},
            }

        stall_limit = int(exp.get("max_iterations_without_improvement") or 0)
        stalled = int(exp.get("stalled_iterations") or 0)
        if stall_limit and stalled >= stall_limit:
            return {
                "action": "COMPLETE",
                "reason": f"plateau: {stalled} iteration(s) without improvement",
                "experiment": exp,
                "scores": exp.get("scores") or {},
            }

        return {
            "action": "WRITE_CANDIDATES",
            "prompt": candidate_prompt(
                capability_name=str(exp.get("capability_name") or ""),
                entrypoint=str(exp.get("entrypoint") or ""),
                scores=exp.get("scores") or {},
                n_candidates=n_cand,
            ),
            "experiment": exp,
        }

    def set_template(self, template: str) -> dict:
        return self.api.set_template(self.experiment_id, template)

    def run_smoke(self) -> dict:
        exp = self.api.get_experiment(self.experiment_id)
        template = exp.get("command_template") or ""
        if not template:
            raise RuntimeError("No command template. Run `overmind optimise set-template` first.")
        dataset = self._load_dataset()
        if not dataset:
            raise RuntimeError("Dataset is empty.")
        extra = None
        # Hybrid smoke checks OpenRouter routing. Comparison smoke is the incumbent.
        if (exp.get("mode") or "optimize") == "hybrid":
            models = list(exp.get("model_ids") or [])
            if models:
                extra = openrouter_env(models[0])
        result = _run_datapoint(
            template=template,
            experiment_id=self.experiment_id,
            capability_id=str(exp.get("capability") or ""),
            project_id=str(exp.get("project") or ""),
            candidate_id="smoke",
            iteration_id="smoke",
            datapoint_index=0,
            datapoint_input=dataset[0].get("input"),
            cwd=self.repo_cwd,
            timeout=self.timeout,
            extra_env=extra,
        )
        if result["success"]:
            local = self._load_state()
            local["smoke_done"] = True
            self._save_state(local)
        result["output"] = (result.get("output") or "")[-OUTPUT_TAIL:]
        return result

    def _run_candidates(
        self,
        *,
        template: str,
        exp: dict,
        iteration: dict,
        pairs: list[tuple[dict, str]],
        dataset: list[dict],
    ) -> list[dict]:
        """Run every (candidate, datapoint) job across ``pairs`` of (candidate, cwd)
        in one shared pool, so candidates execute in parallel across worktrees."""
        iter_id = str(iteration.get("id") or "")
        jobs: list[dict] = []
        for candidate, cwd in pairs:
            target = str(candidate.get("target_model") or "")
            extra = openrouter_env(target) if target else None
            for idx, dp in enumerate(dataset):
                jobs.append({
                    "template": template,
                    "experiment_id": self.experiment_id,
                    "capability_id": str(exp.get("capability") or ""),
                    "project_id": str(exp.get("project") or ""),
                    "candidate_id": str(candidate.get("id") or ""),
                    "iteration_id": iter_id,
                    "datapoint_index": idx,
                    "datapoint_input": dp.get("input"),
                    "cwd": cwd,
                    "timeout": self.timeout,
                    "extra_env": extra,
                })
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            for fut in as_completed([pool.submit(_run_datapoint, **kw) for kw in jobs]):
                results.append(fut.result())
        return results

    def run_baseline(self) -> dict:
        exp = self.api.get_experiment(self.experiment_id)
        template = exp.get("command_template") or ""
        if not template:
            raise RuntimeError("No command template set.")
        local = self._load_state()
        iteration = self.api.add_iteration(
            self.experiment_id,
            order=0,
            name="Baseline",
            candidates=[{"candidate_index": 0, "code_path": "", "is_baseline": True}],
        )
        candidates = iteration.get("candidates") or []
        if not candidates:
            raise RuntimeError("No baseline candidate created.")
        dataset = self._load_dataset()
        results = self._run_candidates(
            template=template,
            exp=exp,
            iteration=iteration,
            pairs=[(candidates[0], self.repo_cwd)],
            dataset=dataset,
        )
        self.api.post_results(self.experiment_id, results)
        self.api.evaluate(self.experiment_id, 0)
        exp = self._poll_until_evaluated()
        local["next_order"] = 1
        self._save_state(local)
        return exp

    def add_candidate_diff(self, diff: str) -> dict:
        local = self._load_state()
        pending = list(local.get("pending_diffs") or [])
        pending.append(diff)
        local["pending_diffs"] = pending
        self._save_state(local)
        return {"pending": len(pending)}

    def run_iteration(self, diffs: list[str] | None = None) -> dict:
        exp = self.api.get_experiment(self.experiment_id)
        template = exp.get("command_template") or ""
        if not template:
            raise RuntimeError("No command template set.")
        local = self._load_state()
        order = int(local.get("next_order") or 1)
        models = list(exp.get("model_ids") or [])
        mode = exp.get("mode") or "optimize"

        if mode == "model_comparison":
            model_index = order - 1
            if model_index < 0 or model_index >= len(models):
                raise RuntimeError("No remaining models to compare.")
            model = models[model_index]
            candidates_payload = [
                {
                    "candidate_index": 0,
                    "code_path": "",
                    "target_model": model,
                    "is_baseline": False,
                }
            ]
            iteration_name = model
        else:
            pending = list(diffs if diffs is not None else local.get("pending_diffs") or [])
            if not pending:
                raise RuntimeError("No candidate diffs. Run `overmind optimise add-candidate --diff`.")
            candidates_payload = []
            index = 0
            for diff in pending:
                if mode == "hybrid" and models:
                    for model in models:
                        candidates_payload.append({
                            "candidate_index": index,
                            "code_path": diff,
                            "target_model": model,
                            "is_baseline": False,
                        })
                        index += 1
                else:
                    candidates_payload.append({
                        "candidate_index": index,
                        "code_path": diff,
                        "is_baseline": False,
                    })
                    index += 1
            iteration_name = f"Iteration {order}"

        iteration = self.api.add_iteration(
            self.experiment_id,
            order=order,
            name=iteration_name,
            candidates=candidates_payload,
        )
        created = iteration.get("candidates") or []
        if not created:
            raise RuntimeError(f"No candidates created for iteration order={order}.")

        dataset = self._load_dataset()
        # Worktree creation mutates the shared .git, so it stays sequential; the
        # datapoint runs themselves share one pool across all candidates.
        pairs: list[tuple[dict, str]] = []
        for candidate in created:
            diff = str(candidate.get("code_path") or "")
            # Empty patch (baseline-shaped comparison candidates) must run in the
            # checkout so an uncommitted rewrite_repo is visible; worktrees are HEAD.
            cwd = (
                self.repo_cwd
                if not diff
                else str(_ensure_worktree(self.repo_cwd, self.experiment_id, str(candidate.get("id")), diff))
            )
            pairs.append((candidate, cwd))
        all_results = self._run_candidates(
            template=template,
            exp=exp,
            iteration=iteration,
            pairs=pairs,
            dataset=dataset,
        )

        self.api.post_results(self.experiment_id, all_results)
        self.api.evaluate(self.experiment_id, order)
        exp = self._poll_until_evaluated()
        local["pending_diffs"] = []
        local["next_order"] = order + 1
        self._save_state(local)
        return exp

    def complete(self) -> dict:
        _remove_worktrees(self.repo_cwd, self.experiment_id)
        return self.api.complete(self.experiment_id)

    def status(self) -> dict:
        return self.api.get_experiment(self.experiment_id)


def start_optimise(
    api: OptimizerAPI,
    *,
    capability_id: str,
    dataset_id: str,
    eval_set_id: str = "",
    cache_dir: Path,
    state_path: Path,
    repo_cwd: str,
    mode: str = "optimize",
    model_ids: list[str] | None = None,
    num_iterations: int = 5,
    num_candidates_per_iteration: int = 3,
    max_iterations_without_improvement: int = 3,
    openrouter_key_source: str = "local",
) -> tuple[dict, Path, OptimiseLoop]:
    exp = api.create_experiment(
        capability_id=capability_id,
        dataset_id=dataset_id,
        eval_set_id=eval_set_id,
        mode=mode,
        model_ids=model_ids,
        num_iterations=num_iterations,
        num_candidates_per_iteration=num_candidates_per_iteration,
        max_iterations_without_improvement=max_iterations_without_improvement,
        openrouter_key_source=openrouter_key_source,
    )
    # The experiment exists from here on: its id is saved before the download,
    # so a failed pull is resumed with --experiment and never starts a second one.
    state = {
        "experiment_id": exp["id"],
        "dataset_id": dataset_id,
        "dataset_path": "",
        "capability_id": capability_id,
        "smoke_done": False,
        "next_order": 0,
        "pending_diffs": [],
        "kind": "optimise",
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    dataset_path = pull_dataset(api, exp, cache_dir)
    state["dataset_path"] = str(dataset_path)
    state_path.write_text(json.dumps(state, indent=2))
    loop = OptimiseLoop(
        api,
        exp["id"],
        repo_cwd=repo_cwd,
        dataset_path=dataset_path,
        state_path=state_path,
    )
    return exp, dataset_path, loop


def pull_dataset(api: OptimizerAPI, exp: dict, cache_dir: Path) -> Path:
    """The experiment's used version, on disk under ``cache_dir``."""
    info = exp.get("cell_info") or {}
    return api.export_dataset(
        str(exp.get("dataset") or ""),
        str(exp.get("cell") or ""),
        cache_dir,
        fingerprint=str(info.get("fingerprint") or ""),
    )


def attach_optimise(
    api: OptimizerAPI,
    experiment_id: str,
    *,
    cache_dir: Path,
    state_path: Path,
    repo_cwd: str,
) -> tuple[dict, Path, OptimiseLoop]:
    exp = api.get_experiment(experiment_id)
    dataset_id = str(exp.get("dataset") or "")
    if not dataset_id:
        raise RuntimeError(f"Experiment {experiment_id} has no dataset.")
    dataset_path = pull_dataset(api, exp, cache_dir)
    iterations = exp.get("iterations") or []
    has_baseline = any(int(it.get("order") or 0) == 0 for it in iterations)
    next_order = 0
    if iterations:
        next_order = max(int(it.get("order") or 0) for it in iterations) + 1
    state = {
        "experiment_id": exp["id"],
        "dataset_id": dataset_id,
        "dataset_path": str(dataset_path),
        "capability_id": str(exp.get("capability") or ""),
        "smoke_done": has_baseline,
        "next_order": next_order,
        "pending_diffs": [],
        "kind": "optimise",
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))
    loop = OptimiseLoop(
        api,
        exp["id"],
        repo_cwd=repo_cwd,
        dataset_path=dataset_path,
        state_path=state_path,
    )
    return exp, dataset_path, loop
