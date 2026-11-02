"""MCP tools for training from checks: options, reward preview, start, inspect and list."""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings

from modal_shared import check_rewards
from overbae.core.errors import InputValidationError
from overbae.innermind import checks, deterministic
from overbae.models import Capability, Dataset, RLRun
from overbae.services import rl_training
from overbae.services.datasets import rows as dataset_rows
from overbae.services.datasets import store, versions
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.rl import (
    InspectRLTrainingInput,
    InspectRLTrainingOutput,
    ListRLOptionsInput,
    ListRLOptionsOutput,
    ListRLRunsInput,
    ListRLRunsOutput,
    PreviewRLRewardsInput,
    PreviewRLRewardsOutput,
    RewardPreviewRow,
    RLDatasetColumns,
    RLRunReceipt,
    StartRLTrainingInput,
    StartRLTrainingOutput,
)
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.references import uuid_reference

GUIDANCE = [
    "Pick rows whose prompt the model sometimes answers correctly: all-fail or all-pass groups teach nothing.",
    "Bind per-row references to check parameters (bind) or span attributes (attributes); fixed params apply to every row.",
    "Run preview_rl_rewards on a reference column and on a wrong answer: a usable reward passes the first and fails the second.",
    "Abstaining checks are left out of a completion's reward; a completion no check can score earns 0.",
    "The run scores the base model and the trained adapter on the same held-out rows; a higher training reward alone is not a release signal.",
]


def console_url(run: RLRun) -> str:
    return f"{settings.FRONTEND_URL.rstrip('/')}/rl-training/{run.pk}?projectId={run.project_id}"


def _dataset(context: MCPContext, reference: str) -> Dataset:
    dataset_id = uuid_reference(reference)
    dataset = (
        Dataset.objects.filter(project=context.project, pk=dataset_id).first()
        if dataset_id
        else None
    )
    if dataset is None:
        raise MCPError("dataset_not_found", "The dataset was not found in this project.")
    return dataset


def _run(context: MCPContext, reference: str) -> RLRun:
    run_id = uuid_reference(reference)
    run = (
        RLRun.objects.select_related("dataset", "cell", "capability")
        .filter(project=context.project, pk=run_id)
        .first()
        if run_id
        else None
    )
    if run is None:
        raise MCPError("resource_not_found", "The RL run was not found in this project.")
    return run


def _active_cell(dataset: Dataset):
    try:
        return versions.resolve_cell(dataset, "", ran_only=True)
    except DatasetError as exc:
        raise MCPError("dataset_invalid", str(exc)) from None


def list_options_sync(payload: ListRLOptionsInput, context: MCPContext) -> ListRLOptionsOutput:
    columns = None
    if payload.dataset:
        dataset = _dataset(context, payload.dataset)
        cell = _active_cell(dataset)
        first = store.read_rows(dataset_rows.frame_path(cell), [0]) if cell.rows else []
        columns = RLDatasetColumns(
            dataset=str(dataset.pk),
            cell=str(cell.pk),
            version=dataset.versions().get(cell.id, ""),
            rows=cell.rows,
            columns=rl_training.columns_of(cell),
            first_row=rl_training.json_safe(first[0]) if first else {},
        )
    return ListRLOptionsOutput(
        summary=f"{len(rl_training.MODELS)} models and {len(rl_training.REWARD_CHECKS)} reward checks.",
        models=list(rl_training.MODELS),
        checks=rl_training.reward_catalog(),
        hyperparameters={
            key: {"default": default, "min": low, "max": high}
            for key, (default, _kind, low, high) in rl_training.HYPERPARAMETERS.items()
        },
        dataset=columns,
        guidance=GUIDANCE,
    )


def preview_rewards_sync(
    payload: PreviewRLRewardsInput, context: MCPContext
) -> PreviewRLRewardsOutput:
    dataset = _dataset(context, payload.dataset)
    cell = _active_cell(dataset)
    columns = rl_training.columns_of(cell)
    if not payload.completions and payload.reference_column not in columns:
        raise MCPError(
            "invalid_input", "Pass completions, or a reference_column that is in this version."
        )
    try:
        rewards = rl_training.normalize_rewards([r.model_dump() for r in payload.rewards], columns)
    except InputValidationError as exc:
        raise MCPError("invalid_input", exc.detail) from None
    count = len(payload.completions) or payload.rows
    records = store.read_rows(dataset_rows.frame_path(cell), list(range(min(count, cell.rows))))
    rendered = [{**r, "source": checks.render(r["check"], r["params"])} for r in rewards]
    runner = check_rewards.CheckRunner(deterministic.CHILD)
    results = []
    try:
        for index, record in enumerate(records):
            row = {key: rl_training.json_safe(value) for key, value in record.items()}
            if payload.completions:
                output = payload.completions[index]
            else:
                value = row.get(payload.reference_column)
                output = value if isinstance(value, str) else json.dumps(value)
            scored = check_rewards.score_completion(
                runner, rendered, row, "", output
            )
            results.append(
                RewardPreviewRow(
                    row=index,
                    output=output[:600],
                    reward=round(scored["reward"], 4),
                    passed=scored["passed"],
                    checks=scored["checks"],
                )
            )
    finally:
        runner.close()
    passed = sum(1 for r in results if r.passed)
    return PreviewRLRewardsOutput(
        summary=f"{passed}/{len(results)} outputs pass every scored check.", results=results
    )


def receipt(run: RLRun) -> RLRunReceipt:
    return RLRunReceipt(
        id=str(run.pk),
        status=run.status,
        name=run.name,
        base_model=run.base_model,
        train_rows=run.train_rows,
        holdout_rows=len(run.holdout_indices),
        max_steps=int(run.hyperparameters.get("max_steps", 0)),
        console_url=console_url(run),
    )


def start_sync(payload: StartRLTrainingInput, context: MCPContext) -> StartRLTrainingOutput:
    dataset = _dataset(context, payload.dataset)
    capability = None
    if payload.capability:
        capability_id = uuid_reference(payload.capability)
        capability = Capability.objects.filter(project=context.project, pk=capability_id).first()
        if capability is None:
            raise MCPError("capability_not_found", "The capability was not found in this project.")
    try:
        run = rl_training.start(
            context.project,
            user=context.user,
            dataset=dataset,
            capability=capability,
            cell_reference=payload.cell,
            name=payload.name,
            base_model=payload.base_model,
            prompt_column=payload.prompt_column,
            tools_column=payload.tools_column,
            system_prompt=payload.system_prompt,
            rewards=[r.model_dump() for r in payload.rewards],
            hyperparameters=payload.hyperparameters,
            request_key=payload.request_key,
        )
    except InputValidationError as exc:
        code = "request_key_conflict" if "request key" in exc.detail else "invalid_input"
        raise MCPError(code, exc.detail) from None
    return StartRLTrainingOutput(
        summary=(
            f"RL run {run.name} is {run.status}: {run.train_rows} training rows, "
            f"{len(run.holdout_indices)} held-out rows, {run.hyperparameters['max_steps']} steps."
        ),
        run=receipt(run),
        next_steps=[
            "Call inspect_rl_training with this run id to follow rewards and the held-out comparison.",
            f"Open {console_url(run)} to watch the run live.",
        ],
    )


def _point(step: dict[str, Any]) -> dict[str, Any]:
    metrics, batch = step.get("metrics") or {}, step.get("batch") or {}

    def rounded(value):
        return round(value, 4) if isinstance(value, (int, float)) else None

    return {
        "step": step["step"],
        "reward": rounded(metrics.get("reward", batch.get("mean_reward"))),
        "pass_rate": rounded(batch.get("pass_rate")),
        "checks": {k: rounded(v.get("mean")) for k, v in (batch.get("per_check") or {}).items()},
    }


def _downsample(points: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    stride = len(points) / limit
    picked = [points[int(i * stride)] for i in range(limit - 1)]
    return [*picked, points[-1]]


def _comparison(run: RLRun, count: int) -> dict[str, Any] | None:
    paired = [s for s in run.samples if s.get("baseline") and s.get("final")]
    if not paired:
        return None

    def change(sample):
        return (sample["final"]["reward"] or 0.0) - (sample["baseline"]["reward"] or 0.0)

    def brief(sample):
        return {
            "row": sample["index"],
            "prompt": (sample.get("prompt") or "")[:300],
            "before": {
                "output": (sample["baseline"]["output"] or "")[:400],
                "reward": round(sample["baseline"]["reward"], 4),
                "passed": sample["baseline"]["passed"],
            },
            "after": {
                "output": (sample["final"]["output"] or "")[:400],
                "reward": round(sample["final"]["reward"], 4),
                "passed": sample["final"]["passed"],
            },
        }

    ordered = sorted(paired, key=change)
    return {
        "fixed": sum(1 for s in paired if s["final"]["passed"] and not s["baseline"]["passed"]),
        "broken": sum(1 for s in paired if s["baseline"]["passed"] and not s["final"]["passed"]),
        "unchanged": sum(1 for s in paired if s["baseline"]["passed"] == s["final"]["passed"]),
        "most_improved": [brief(s) for s in reversed(ordered[-count:]) if change(s) > 0]
        if count
        else [],
        "most_regressed": [brief(s) for s in ordered[:count] if change(s) < 0] if count else [],
    }


def inspect_sync(payload: InspectRLTrainingInput, context: MCPContext) -> InspectRLTrainingOutput:
    run = _run(context, payload.run)
    record = rl_training.describe(run)
    steps = record.pop("steps")
    record.pop("samples")
    max_steps = int(run.hyperparameters.get("max_steps", 0))
    done = record["steps_completed"]
    latest = next((s for s in reversed(steps) if (s.get("group") or {}).get("attempts")), None)
    latest_group = None
    if latest:
        latest_group = {
            "step": latest["step"],
            "prompt": latest["group"]["prompt"][:300],
            "attempts": [
                {
                    "reward": round(a["reward"], 4),
                    "passed": a["passed"],
                    "text": a["text"][:300],
                }
                for a in latest["group"]["attempts"]
            ],
        }
    for key in ("started_at", "finished_at", "created_at", "updated_at"):
        record[key] = record[key].isoformat() if record[key] else None
    baseline, final = record["baseline"] or {}, record["final"] or {}
    parts = [f"{run.name} is {run.status}", f"step {done}/{max_steps}"]
    if baseline:
        parts.append(f"base pass rate {baseline['pass_rate']:.0%}")
    if final:
        parts.append(f"trained pass rate {final['pass_rate']:.0%}")
    if run.error:
        parts.append(run.error[:200])
    return InspectRLTrainingOutput(
        summary="; ".join(parts) + ".",
        run=record,
        progress={
            "status": run.status,
            "steps_completed": done,
            "max_steps": max_steps,
            "gpu": run.gpu,
            "gpu_seconds": run.gpu_seconds,
        },
        curve=_downsample([_point(s) for s in steps if s.get("metrics") or s.get("batch")]),
        latest_group=latest_group,
        comparison=_comparison(run, payload.samples),
        verdict=record["verdict"],
        console_url=console_url(run),
    )


def list_runs_sync(payload: ListRLRunsInput, context: MCPContext) -> ListRLRunsOutput:
    query = RLRun.objects.filter(project=context.project).select_related("dataset")
    if payload.status:
        query = query.filter(status=payload.status)
    runs = []
    for run in query.order_by("-created_at")[: payload.limit]:
        runs.append(
            {
                "id": str(run.pk),
                "name": run.name,
                "status": run.status,
                "base_model": run.base_model,
                "dataset": run.dataset.name,
                "base_pass_rate": (run.baseline or {}).get("pass_rate"),
                "trained_pass_rate": (run.final or {}).get("pass_rate"),
                "created_at": run.created_at.isoformat(),
                "console_url": console_url(run),
            }
        )
    return ListRLRunsOutput(summary=f"{len(runs)} RL runs.", runs=runs)


def register_rl_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "list_rl_training_options",
            "List RL training options",
            "Models, reward checks (deterministic check scripts and their parameters) and hyperparameter ranges for training from checks. Pass dataset to also get its active version's columns and first row, which you need to bind per-row references.",
            ListRLOptionsInput,
            ListRLOptionsOutput,
            list_options_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "preview_rl_rewards",
            "Preview RL rewards",
            "Score candidate outputs (or a reference column) on the first dataset rows with a reward configuration, without training. Use it to confirm the rewards pass a correct answer and fail a wrong one before start_rl_training.",
            PreviewRLRewardsInput,
            PreviewRLRewardsOutput,
            preview_rewards_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "start_rl_training",
            "Start RL training from checks",
            "Train a LoRA adapter with single-turn GRPO on one Modal GPU. Each step samples a group of completions per prompt; the reward is the weighted mean of the chosen check scripts. The run scores the base model and the trained adapter on the same held-out rows. Same project request_key and inputs return the existing run; changed inputs conflict.",
            StartRLTrainingInput,
            StartRLTrainingOutput,
            start_sync,
            False,
            False,
            "gpu",
            "job",
            {"overmind:train"},
        ),
        (
            "inspect_rl_training",
            "Inspect RL training",
            "Status, step progress, a downsampled reward and pass-rate curve with per-check means, the latest group of sampled attempts, the held-out before/after comparison and the release verdict of one RL run.",
            InspectRLTrainingInput,
            InspectRLTrainingOutput,
            inspect_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "list_rl_training_runs",
            "List RL training runs",
            "RL runs in this project, newest first, with base and trained held-out pass rates.",
            ListRLRunsInput,
            ListRLRunsOutput,
            list_runs_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
    ]
    for (
        name,
        title,
        description,
        input_model,
        output_model,
        function,
        read_only,
        idempotent,
        cost_class,
        async_mode,
        scopes,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=idempotent,
                open_world=name == "start_rl_training",
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            sync_to_async(function, thread_sensitive=True),
        )
