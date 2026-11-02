"""Train from checks: single-turn GRPO whose reward is the project's deterministic checks.

The platform pins the inputs (dataset version, model, rendered checks, held-out rows),
spawns one Modal call and records what the worker reports. Reward interpretation stays
with the checks; nothing here judges an output.
"""

from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

import modal
from django.db import transaction
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.innermind import checks, deterministic
from overbae.models import Capability, Cell, Dataset, Project, RLRun, RLStep
from overbae.services.datasets import rows as dataset_rows
from overbae.services.datasets import store, use, versions
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)

APP_NAME = "overmind-rl"
FUNCTION_NAME = "train"
EVENTS_QUEUE = "overmind-rl-events"
GPU = "H100"
MAX_TRAIN_ROWS = 2_000
MIN_TRAIN_ROWS = 8

MODELS = (
    {"id": "Qwen/Qwen3-0.6B", "label": "Qwen3 0.6B", "parameters": "0.6B"},
    {"id": "Qwen/Qwen3-1.7B", "label": "Qwen3 1.7B", "parameters": "1.7B"},
    {"id": "Qwen/Qwen3-4B", "label": "Qwen3 4B", "parameters": "4B"},
    {"id": "Qwen/Qwen2.5-0.5B-Instruct", "label": "Qwen2.5 0.5B Instruct", "parameters": "0.5B"},
)

# Budgets read span usage and latency, which a sampled completion does not have.
REWARD_CHECKS = tuple(
    name
    for name, _span_type, _enabled in checks.CATALOG
    if name not in {"llm_call_budget", "latency_budget", "cost_budget"}
)

HYPERPARAMETERS = {
    "group_size": (8, int, 2, 16),
    "prompts_per_step": (2, int, 1, 8),
    "max_steps": (80, int, 5, 500),
    "learning_rate": (3e-5, float, 1e-7, 1e-3),
    "beta": (0.0, float, 0.0, 0.5),
    "temperature": (1.0, float, 0.1, 1.5),
    "max_completion_tokens": (128, int, 16, 1024),
    "lora_rank": (16, int, 4, 64),
    "holdout_size": (64, int, 8, 256),
    "seed": (7, int, 0, 2**31 - 1),
}

STATUS_RANK = {
    RLRun.Status.QUEUED: 0,
    RLRun.Status.STARTING: 1,
    RLRun.Status.BASELINE: 2,
    RLRun.Status.TRAINING: 3,
    RLRun.Status.FINAL: 4,
    RLRun.Status.SUCCEEDED: 5,
    RLRun.Status.FAILED: 5,
    RLRun.Status.CANCELLED: 5,
}

WORKER_STATUS = {
    "loading_model": RLRun.Status.STARTING,
    "baseline": RLRun.Status.BASELINE,
    "training": RLRun.Status.TRAINING,
    "final": RLRun.Status.FINAL,
}


def modal_environment() -> str:
    return os.environ.get("MODAL_ENVIRONMENT", "overmind-dev")


def reward_catalog() -> list[dict[str, Any]]:
    entries = []
    for name in REWARD_CHECKS:
        text = checks.source(name)
        doc = text.split('"""', 2)[1].strip() if text.startswith('"""') else ""
        entries.append(
            {
                "check": name,
                "description": " ".join(doc.split("\n\n", 1)[0].split()),
                "parameters": {
                    key: value
                    for key, value in checks.parameters(name).items()
                    if key not in {"TOOL_TYPES", "ARG_PREFIX"}
                },
            }
        )
    return entries


def columns_of(cell: Cell) -> list[str]:
    return [str(column.get("name")) for column in cell.columns or [] if column.get("name")]


def normalize_hyperparameters(values: dict[str, Any] | None) -> dict[str, Any]:
    values = dict(values or {})
    unknown = sorted(set(values) - set(HYPERPARAMETERS))
    if unknown:
        raise InputValidationError(f"Unknown hyperparameter {unknown[0]!r}")
    result = {}
    for key, (default, kind, low, high) in HYPERPARAMETERS.items():
        raw = values.get(key, default)
        try:
            value = kind(raw)
        except (TypeError, ValueError):
            raise InputValidationError(f"{key} must be a {kind.__name__}") from None
        if not low <= value <= high:
            raise InputValidationError(f"{key} must be between {low} and {high}")
        result[key] = value
    return result


def normalize_rewards(rewards: list[dict[str, Any]], columns: list[str]) -> list[dict[str, Any]]:
    if not isinstance(rewards, list) or not 1 <= len(rewards) <= 6:
        raise InputValidationError("Choose one to six reward checks")
    known = set(columns)
    normalized, seen = [], {}
    for entry in rewards:
        if not isinstance(entry, dict):
            raise InputValidationError("Each reward must be an object")
        check = str(entry.get("check") or "")
        if check not in REWARD_CHECKS:
            raise InputValidationError(
                f"Unknown reward check {check!r}; use one of {', '.join(REWARD_CHECKS)}"
            )
        params = entry.get("params") or {}
        bind = entry.get("bind") or {}
        attributes = entry.get("attributes") or {}
        expected = str(entry.get("expected") or "")
        if not all(isinstance(x, dict) for x in (params, bind, attributes)):
            raise InputValidationError(f"{check}: params, bind and attributes must be objects")
        try:
            checks.render(check, params)
        except ValueError as exc:
            raise InputValidationError(str(exc)) from None
        parameters = checks.parameters(check)
        for param, column in bind.items():
            if param not in parameters:
                raise InputValidationError(f"{check} has no parameter {param!r}")
            if param in params:
                raise InputValidationError(f"{check}: {param} is both fixed and bound to a column")
            if column not in known:
                raise InputValidationError(f"{check}: column {column!r} is not in this version")
        for column in attributes.values():
            if column not in known:
                raise InputValidationError(f"{check}: column {column!r} is not in this version")
        if expected and expected not in known:
            raise InputValidationError(f"{check}: column {expected!r} is not in this version")
        try:
            weight = float(entry.get("weight", 1.0))
        except (TypeError, ValueError):
            raise InputValidationError(f"{check}: weight must be a number") from None
        if not 0 < weight <= 10:
            raise InputValidationError(f"{check}: weight must be above 0 and at most 10")
        seen[check] = seen.get(check, 0) + 1
        normalized.append(
            {
                "key": check if seen[check] == 1 else f"{check}_{seen[check]}",
                "check": check,
                "params": params,
                "bind": {str(k): str(v) for k, v in bind.items()},
                "attributes": {str(k): str(v) for k, v in attributes.items()},
                "expected": expected,
                "weight": weight,
            }
        )
    return normalized


def spec_of(run: RLRun) -> dict[str, Any]:
    return {
        "dataset": str(run.dataset_id),
        "cell": str(run.cell_id),
        "base_model": run.base_model,
        "prompt_column": run.prompt_column,
        "tools_column": run.tools_column,
        "system_prompt": run.system_prompt,
        "rewards": run.rewards,
        "hyperparameters": run.hyperparameters,
        "name": run.name,
    }


@transaction.atomic
def start(
    project: Project,
    *,
    user,
    dataset: Dataset,
    request_key: str,
    base_model: str,
    prompt_column: str,
    rewards: list[dict[str, Any]],
    cell_reference: str = "",
    tools_column: str = "",
    system_prompt: str = "",
    hyperparameters: dict[str, Any] | None = None,
    name: str = "",
    capability: Capability | None = None,
) -> RLRun:
    Project.objects.select_for_update().get(pk=project.pk)
    if dataset.project_id != project.pk:
        raise InputValidationError("That dataset belongs to another project")
    if base_model not in {m["id"] for m in MODELS}:
        raise InputValidationError(
            f"Choose one of {', '.join(m['id'] for m in MODELS)} for RL training"
        )
    try:
        cell = versions.resolve_cell(dataset, cell_reference, ran_only=True)
        dataset_rows.verify(cell)
    except (DatasetError, dataset_rows.RowStoreError) as exc:
        raise InputValidationError(str(exc)) from None
    columns = columns_of(cell)
    if prompt_column not in columns:
        raise InputValidationError(
            f"Column {prompt_column!r} is not in {dataset.name}; it has {', '.join(columns)}"
        )
    if tools_column and tools_column not in columns:
        raise InputValidationError(f"Column {tools_column!r} is not in {dataset.name}")
    normalized = normalize_rewards(rewards, columns)
    hp = normalize_hyperparameters(hyperparameters)
    holdout = min(hp["holdout_size"], max(1, cell.rows // 5))
    if cell.rows - holdout < MIN_TRAIN_ROWS:
        raise InputValidationError(
            f"{dataset.name} has {cell.rows} rows; RL needs at least {MIN_TRAIN_ROWS + holdout}"
        )
    candidate = RLRun(
        project=project,
        dataset=dataset,
        cell=cell,
        capability=capability,
        name=(name or f"{dataset.name} · {base_model.split('/')[-1]}")[:255],
        base_model=base_model,
        prompt_column=prompt_column,
        tools_column=tools_column,
        system_prompt=system_prompt,
        rewards=normalized,
        hyperparameters=hp,
    )
    prior = RLRun.objects.filter(project=project, request_key=request_key).first()
    if prior:
        if spec_of(prior) != spec_of(candidate):
            raise InputValidationError("This request key already identifies a different RL run")
        return prior
    candidate.holdout_indices = sorted(random.Random(hp["seed"]).sample(range(cell.rows), holdout))
    candidate.train_rows = min(cell.rows - holdout, MAX_TRAIN_ROWS)
    candidate.request_key = request_key
    candidate.created_by = user if getattr(user, "is_authenticated", False) else None
    candidate.save()
    use.freeze(cell)
    run_id = str(candidate.pk)
    transaction.on_commit(lambda: dispatch_later(run_id))
    return candidate


def dispatch_later(run_id: str) -> None:
    # Lazy: the task module imports this service.
    from overbae.tasks.rl_training import dispatch_rl_run

    dispatch_rl_run.delay(run_id)


def prompt_text(value: Any) -> str:
    """The last user turn, for people reading a sample; the worker renders the real prompt."""
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        value = value["messages"]
    if isinstance(value, list):
        users = [m.get("content") for m in value if isinstance(m, dict) and m.get("role") == "user"]
        return str(users[-1] if users else value[-1] if value else "")
    return "" if value is None else str(value)


def payload(run: RLRun) -> dict[str, Any]:
    needed = {run.prompt_column, run.tools_column}
    for reward in run.rewards:
        needed.update(reward["bind"].values())
        needed.update(reward["attributes"].values())
        needed.add(reward["expected"])
    needed.discard("")
    holdout = set(run.holdout_indices)
    train_rows, holdout_rows = [], []
    for index, record in enumerate(store.iter_rows(dataset_rows.frame_path(run.cell))):
        row = {column: json_safe(record.get(column)) for column in needed}
        row["_prompt_text"] = prompt_text(row.get(run.prompt_column))
        if index in holdout:
            holdout_rows.append(row)
        elif len(train_rows) < run.train_rows:
            train_rows.append(row)
    return {
        "run_id": str(run.pk),
        "base_model": run.base_model,
        "prompt_column": run.prompt_column,
        "tools_column": run.tools_column,
        "system_prompt": run.system_prompt,
        "hyperparameters": run.hyperparameters,
        "rewards": [
            {**reward, "source": checks.render(reward["check"], reward["params"])}
            for reward in run.rewards
        ],
        "runner_source": deterministic.CHILD,
        "train_rows": train_rows,
        "holdout_rows": holdout_rows,
    }


def json_safe(value: Any) -> Any:
    """Parquet hands back numpy scalars and arrays; the Modal payload is plain JSON."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, float) and value != value:
        return None
    return value


def dispatch(run_id: str) -> bool:
    """Spawn the Modal call once; the row lock keeps a racing dispatch from spawning twice.
    Returns whether the run needs polling."""
    with transaction.atomic():
        run = RLRun.objects.select_for_update().select_related("cell").get(pk=run_id)
        if run.status != RLRun.Status.QUEUED or run.modal_call_id:
            return not run.is_terminal
        try:
            job = payload(run)
            function = modal.Function.from_name(
                APP_NAME, FUNCTION_NAME, environment_name=modal_environment()
            )
            call = function.spawn(job)
        except Exception as exc:
            logger.exception("RL run %s could not start", run_id)
            run.status = RLRun.Status.FAILED
            run.error = f"The training worker could not start: {exc}"[:4000]
            run.finished_at = timezone.now()
            run.save()
            return False
        run.status = RLRun.Status.STARTING
        run.modal_call_id = call.object_id
        run.gpu = GPU
        run.started_at = timezone.now()
        run.save()
    return True


def advance(run: RLRun, status: str) -> None:
    if STATUS_RANK[status] > STATUS_RANK[run.status]:
        run.status = status


def merge_samples(run: RLRun, stage: str, samples: list[dict[str, Any]]) -> None:
    by_index = {sample["index"]: dict(sample) for sample in run.samples}
    for sample in samples:
        entry = by_index.setdefault(sample["index"], {"index": sample["index"]})
        entry["prompt"] = sample.get("prompt") or entry.get("prompt", "")
        entry[stage] = {k: sample.get(k) for k in ("output", "reward", "passed", "checks")}
    run.samples = [by_index[i] for i in sorted(by_index)]


def apply(run: RLRun, event: dict[str, Any]) -> None:
    kind = event.get("type")
    if kind == "status" and event.get("status") in WORKER_STATUS:
        advance(run, WORKER_STATUS[event["status"]])
    elif kind in ("baseline", "final"):
        setattr(run, kind, event.get("metrics") or {})
        merge_samples(run, kind, event.get("samples") or [])
    elif kind == "group":
        step, _ = RLStep.objects.get_or_create(run=run, step=int(event["step"]))
        RLStep.objects.filter(pk=step.pk).update(
            group={"prompt": event.get("prompt", ""), "attempts": event.get("attempts", [])},
            batch=event.get("batch") or {},
        )
    elif kind == "step":
        step, _ = RLStep.objects.get_or_create(run=run, step=int(event["step"]))
        RLStep.objects.filter(pk=step.pk).update(metrics=event.get("metrics") or {})
        if event.get("max_steps"):
            run.hyperparameters = {**run.hyperparameters, "max_steps": int(event["max_steps"])}
    elif kind == "done":
        advance(run, RLRun.Status.SUCCEEDED)
        run.gpu_seconds = event.get("gpu_seconds")
        run.adapter_path = event.get("adapter") or ""
        run.finished_at = timezone.now()
    elif kind == "error":
        advance(run, RLRun.Status.FAILED)
        run.error = str(event.get("message") or "The training worker failed")[:4000]
        run.gpu_seconds = event.get("gpu_seconds")
        run.finished_at = timezone.now()


def poll(run_id: str) -> bool:
    """Drain the run's events and settle it from the Modal call. Returns True when terminal."""
    with transaction.atomic():
        run = RLRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None or run.is_terminal:
            return True
        if not run.modal_call_id:
            return False
        queue = modal.Queue.from_name(EVENTS_QUEUE, environment_name=modal_environment())
        for _ in range(20):
            batch = queue.get_many(200, block=False, partition=str(run.pk))
            for event in batch:
                apply(run, event)
            if len(batch) < 200:
                break
        if not run.is_terminal:
            settle(run)
        run.save()
        return run.is_terminal


def settle(run: RLRun) -> None:
    """A call that ended without a terminal event still ends the run."""
    try:
        result = modal.FunctionCall.from_id(run.modal_call_id).get(timeout=0)
    except TimeoutError:
        return
    except modal.exception.OutputExpiredError:
        advance(run, RLRun.Status.FAILED)
        run.error = "The training worker's result expired before it was collected"
    except Exception as exc:
        advance(run, RLRun.Status.FAILED)
        run.error = run.error or f"The training worker stopped: {exc}"[:4000]
    else:
        if not run.final:
            return
        advance(run, RLRun.Status.SUCCEEDED)
        run.gpu_seconds = (result or {}).get("gpu_seconds", run.gpu_seconds)
        run.adapter_path = (result or {}).get("adapter") or run.adapter_path
    run.finished_at = run.finished_at or timezone.now()


def cancel(run: RLRun) -> RLRun:
    if run.is_terminal:
        return run
    if run.modal_call_id:
        try:
            modal.FunctionCall.from_id(run.modal_call_id).cancel()
        except Exception:
            logger.exception("RL run %s cancel request failed", run.pk)
    RLRun.objects.filter(pk=run.pk).exclude(status__in=RLRun.TERMINAL).update(
        status=RLRun.Status.CANCELLED, finished_at=timezone.now()
    )
    run.refresh_from_db()
    return run


def delta(run: RLRun) -> dict[str, Any] | None:
    if not run.baseline or not run.final:
        return None
    return {
        "pass_rate": run.final["pass_rate"] - run.baseline["pass_rate"],
        "mean_reward": run.final["mean_reward"] - run.baseline["mean_reward"],
    }


def verdict(run: RLRun) -> dict[str, str] | None:
    """The release call the comparison supports; it is advice, never an activation."""
    change = delta(run)
    if change is None:
        return None
    if change["pass_rate"] > 0 and change["mean_reward"] >= 0:
        return {
            "decision": "promote",
            "reason": "The trained adapter passes more held-out prompts than the base model.",
        }
    if change["pass_rate"] == 0 and change["mean_reward"] > 0:
        return {
            "decision": "review",
            "reason": "Rewards rose but no more held-out prompts pass.",
        }
    return {
        "decision": "keep_current",
        "reason": "The trained adapter does not beat the base model on held-out prompts.",
    }


def describe(run: RLRun, *, steps: bool = True) -> dict[str, Any]:
    record = {
        "id": str(run.pk),
        "project": str(run.project_id),
        "name": run.name,
        "status": run.status,
        "error": run.error,
        "base_model": run.base_model,
        "dataset": {"id": str(run.dataset_id), "name": run.dataset.name},
        "cell": use.describe(run.cell),
        "capability": (
            {"id": str(run.capability_id), "name": run.capability.name} if run.capability else None
        ),
        "prompt_column": run.prompt_column,
        "tools_column": run.tools_column,
        "system_prompt": run.system_prompt,
        "rewards": run.rewards,
        "hyperparameters": run.hyperparameters,
        "train_rows": run.train_rows,
        "holdout_rows": len(run.holdout_indices),
        "gpu": run.gpu,
        "gpu_seconds": run.gpu_seconds,
        "baseline": run.baseline or None,
        "final": run.final or None,
        "delta": delta(run),
        "verdict": verdict(run),
        "adapter_path": run.adapter_path,
        "steps_completed": run.steps.exclude(metrics={}).count(),
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
    if steps:
        record["steps"] = [
            {"step": s.step, "metrics": s.metrics, "batch": s.batch, "group": s.group}
            for s in run.steps.all()
        ]
        record["samples"] = run.samples
    return record


def active_run_ids() -> list[str]:
    return [
        str(pk)
        for pk in RLRun.objects.exclude(status__in=RLRun.TERMINAL)
        .exclude(modal_call_id="")
        .values_list("pk", flat=True)
    ]


def queued_run_ids() -> list[str]:
    return [
        str(pk)
        for pk in RLRun.objects.filter(status=RLRun.Status.QUEUED, modal_call_id="").values_list(
            "pk", flat=True
        )
    ]
