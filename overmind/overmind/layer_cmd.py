"""Score or fine-tune from stored LLM calls. The application is not run."""

from __future__ import annotations

import re
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import requests
import typer

from overmind.api import Connection, open_session, poll, read_json
from overmind.cli import ApiKeyOption, ApiUrlOption, ConfigPathOption, ProjectIdOption, console, guard
from overmind.config import DEFAULT_PATH

BACKTEST_CAP = 5
FINETUNE_CAP = 4
_DURATION = re.compile(r"^(\d+)([dhm])$")
_PASS = {"improved", "unchanged"}


class LayerError(Exception):
    def __init__(self, message: str, *, code: int = 1):
        super().__init__(message)
        self.code = code


def parse_models(values: list[str] | None, *, cap: int) -> list[str]:
    models: list[str] = []
    for value in values or []:
        models.extend(part.strip() for part in value.split(",") if part.strip())
    if not models:
        raise LayerError(f"Pass between 1 and {cap} models with --models.")
    if len(models) != len(set(models)):
        raise LayerError("Duplicate models are not allowed.")
    if len(models) > cap:
        raise LayerError(f"Select between 1 and {cap} models.")
    return models


def parse_since(value: str, *, now: datetime) -> datetime:
    match = _DURATION.match(value.strip())
    if match:
        count, unit = int(match.group(1)), match.group(2)
        delta = {"d": timedelta(days=count), "h": timedelta(hours=count), "m": timedelta(minutes=count)}[unit]
        return now - delta
    return _parse_iso(value, what="since")


def parse_until(value: str) -> datetime | None:
    if not value.strip():
        return None
    return _parse_iso(value, what="until")


def parse_timeout(value: str) -> float:
    match = _DURATION.match(value.strip())
    if not match:
        raise LayerError("timeout must look like 45m, 2h or 1d.")
    count, unit = int(match.group(1)), match.group(2)
    scale = {"m": 60, "h": 3600, "d": 86400}[unit]
    return float(count * scale)


def _parse_iso(value: str, *, what: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise LayerError(f"{what} must be an ISO timestamp or a duration like 7d.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def resolve_capability(reference: str) -> tuple[str, str]:
    """Resolve ``--capability`` to ``(id, display)``.

    Capabilities live on the server (decorator manifest), not in ``overmind.toml``.
    Pass a UUID or a slug; the slug is sent as-is for the server to resolve.
    """
    reference = reference.strip()
    if not reference:
        raise LayerError("Pass --capability <slug-or-id>.")
    try:
        return str(uuid.UUID(reference)), reference[:8]
    except ValueError:
        return reference, reference


class Client:
    """The Overmind API as the backtest and finetune flows use it."""

    def __init__(self, session: requests.Session, base_url: str, *, sleep=time.sleep, clock=time.monotonic):
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep
        self.clock = clock

    def get(self, path: str) -> dict[str, Any]:
        return read_json(self.session.get(f"{self.base_url}{path}", timeout=60), f"GET {path}", error=LayerError)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(f"{self.base_url}{path}", json=body, timeout=60)
        return read_json(response, f"POST {path}", error=LayerError)

    def wait_dataset(self, dataset_id: str, *, deadline: float) -> dict[str, Any]:
        def settled(dataset: dict[str, Any]) -> bool:
            if dataset.get("state") == "error":
                raise LayerError(dataset.get("error") or "Landing failed.")
            return dataset.get("state") == "idle"

        return self._wait(f"/api/datasets/{dataset_id}/", settled, deadline, "Timed out waiting for the dataset.")

    def wait_eval(self, run_id: str, *, deadline: float) -> dict[str, Any]:
        def settled(run: dict[str, Any]) -> bool:
            status = run.get("status")
            if status in {"failed", "cancelled"}:
                raise LayerError(run.get("error") or f"Evaluation {status}.")
            return status == "completed"

        return self._wait(f"/api/eval-runs/{run_id}/", settled, deadline, "Timed out waiting for the evaluation.")

    def _wait(self, path: str, settled, deadline: float, timeout_message: str) -> dict[str, Any]:
        return poll(
            lambda: self.get(path),
            settled=settled,
            deadline=deadline,
            interval=2,
            timed_out=lambda _value: LayerError(timeout_message, code=2),
            sleep=self.sleep,
            clock=self.clock,
        )


def _cell_id(dataset: dict[str, Any]) -> str:
    if dataset.get("active"):
        return str(dataset["active"])
    cells = dataset.get("cells") or []
    if not cells:
        raise LayerError("The dataset has no version.")
    return str(cells[0]["id"])


def _selection(
    *,
    capability_id: str,
    since: datetime,
    until: datetime | None,
    limit: int,
    from_model: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "capability_id": capability_id,
        "since": since.isoformat(),
        "limit": limit,
    }
    if until is not None:
        body["until"] = until.isoformat()
    if from_model.strip():
        body["model"] = from_model.strip()
    return body


def _openrouter_ids(client: Client) -> set[str]:
    catalog = client.get("/api/models/catalog/")
    if catalog.get("upstream_available") is False:
        raise LayerError("The model catalog is unavailable.")
    return {str(item.get("id")) for item in catalog.get("models") or [] if item.get("id")}


def _finetune_ids(client: Client) -> set[str]:
    catalog = client.get("/api/finetuning-jobs/models/")
    found: set[str] = set()
    groups = catalog.get("models") or {}
    if isinstance(groups, dict):
        entries = [entry for group in groups.values() if isinstance(group, list) for entry in group]
    else:
        entries = list(groups)
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("id") or entry.get("disabled"):
            continue
        found.add(str(entry["id"]))
    return found


def _require_models(client: Client, models: list[str], *, finetune: bool) -> None:
    known = _finetune_ids(client) if finetune else _openrouter_ids(client)
    missing = [model for model in models if model not in known]
    if missing:
        raise LayerError(f"Unknown model: {', '.join(missing)}.")


def _judge_errors(run: dict[str, Any], label: str) -> int:
    by_variant = ((run.get("summary") or {}).get("error_counts") or {}).get("by_variant") or {}
    for row in by_variant.values():
        if isinstance(row, dict) and row.get("label") == label:
            return int(row.get("evaluator_errors") or 0)
    return 0


def _gate(run: dict[str, Any], models: list[str]) -> int:
    comparison = (run.get("summary") or {}).get("baseline_comparison") or {}
    rows = {row.get("label"): row for row in comparison.get("variants") or []}
    failed = _judge_errors(run, "recorded") > 0
    console.print(f"{'recorded':<40} {'unscored' if failed else 'baseline'}")
    for model in models:
        row = rows.get(model)
        overall = (row or {}).get("overall") or {}
        status = overall.get("status") or "missing"
        errors = _judge_errors(run, model)
        if errors and status in _PASS:
            status = "unscored"
        current = (overall.get("current") or {}).get("primary")
        delta = overall.get("delta")
        score = "" if current is None else f"{current:.3f}"
        change = "" if delta is None else f"{delta:+.3f}"
        console.print(f"{model:<40} {score:<8} {status:<12} {change}")
        if status not in _PASS or errors:
            failed = True
    return 1 if failed else 0


def run_backtest(
    client: Client,
    *,
    project_id: str,
    capability_id: str,
    capability_slug: str,
    models: list[str],
    since: datetime,
    until: datetime | None,
    limit: int,
    from_model: str,
    timeout_s: float,
) -> int:
    _require_models(client, models, finetune=False)
    capability = client.get(f"/api/capabilities/{capability_id}/")
    eval_set = capability.get("active_eval_set")
    if not eval_set:
        raise LayerError("The capability has no eval set.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    created = client.post(
        "/api/datasets/",
        {
            "project": project_id,
            "name": f"backtest {capability_slug} {stamp}"[:255],
            "capability": capability_id,
            "intent": "eval",
            "source": {
                "llm_calls": _selection(
                    capability_id=capability_id,
                    since=since,
                    until=until,
                    limit=limit,
                    from_model=from_model,
                )
            },
        },
    )
    deadline = client.clock() + timeout_s
    dataset = client.wait_dataset(str(created["id"]), deadline=deadline)
    kept = (dataset.get("source_spec") or {}).get("kept")
    skipped = (dataset.get("source_spec") or {}).get("skipped")
    console.print(f"{kept if kept is not None else dataset.get('rows')} calls, {skipped or 0} skipped")
    variants = [
        {"label": "recorded", "mode": "existing", "is_baseline": True, "order": 0},
        *[
            {
                "label": model,
                "mode": "generate",
                "model_name": model,
                "is_baseline": False,
                "order": index + 1,
                "params": {"generation_strategy": "single_completion"},
            }
            for index, model in enumerate(models)
        ],
    ]
    run = client.post(
        "/api/eval-runs/",
        {
            "project": project_id,
            "name": f"backtest {capability_slug} {stamp}"[:255],
            "dataset": dataset["id"],
            "cell": _cell_id(dataset),
            "eval_set": eval_set,
            "max_items": int(dataset.get("rows") or limit),
            "variants_input": variants,
        },
    )
    if not run.get("run_evaluators"):
        raise LayerError("The eval set has no generative evaluators.")
    finished = client.wait_eval(str(run["id"]), deadline=deadline)
    return _gate(finished, models)


def run_finetune(
    client: Client,
    *,
    project_id: str,
    capability_id: str,
    capability_slug: str,
    models: list[str],
    since: datetime,
    until: datetime | None,
    limit: int,
    from_model: str,
    eval_percent: int,
    timeout_s: float,
) -> int:
    _require_models(client, models, finetune=True)
    capability = client.get(f"/api/capabilities/{capability_id}/")
    eval_set = capability.get("active_eval_set")
    if not eval_set:
        raise LayerError("The capability has no eval set.")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    created = client.post(
        "/api/datasets/split/",
        {
            "project": project_id,
            "name": f"finetune {capability_slug} {stamp}"[:249],
            "capability": capability_id,
            "eval_percent": eval_percent,
            "position": "hash",
            "source": {
                "llm_calls": _selection(
                    capability_id=capability_id,
                    since=since,
                    until=until,
                    limit=limit,
                    from_model=from_model,
                )
            },
        },
    )
    deadline = client.clock() + timeout_s
    train = client.wait_dataset(str(created["train"]["id"]), deadline=deadline)
    evaluation = client.wait_dataset(str(created["eval"]["id"]), deadline=deadline)
    group_id = str(uuid.uuid4())
    queued: list[str] = []
    try:
        for model in models:
            job = client.post(
                "/api/finetuning-jobs/",
                {
                    "project": project_id,
                    "capability": capability_id,
                    "dataset": train["id"],
                    "cell": _cell_id(train),
                    "eval_dataset": evaluation["id"],
                    "eval_cell": _cell_id(evaluation),
                    "eval_set": eval_set,
                    "base_model": model,
                    "group_id": group_id,
                    "name": f"finetune {capability_slug} {model}"[:255],
                },
            )
            queued.append(f"{model} {job['id']}")
    except LayerError as exc:
        for line in queued:
            console.print(line)
        raise LayerError(f"{exc} Queued: {len(queued)}.", code=1) from exc
    console.print(f"group {group_id}")
    for line in queued:
        console.print(line)
    return 0


def _prepare(
    *,
    api_key: str,
    api_url: str,
    path: Path,
    project_id: str,
    capability: str,
    since: str,
    until: str,
) -> tuple[Client, str, str, str, datetime, datetime | None]:
    connection = Connection.resolve(
        path, api_key=api_key, api_url=api_url, project_id=project_id, require_project=True, error=LayerError
    )
    client = Client(open_session(connection.api_key, json_body=False), connection.base_url)
    capability_id, slug = resolve_capability(capability)
    start = parse_since(since, now=datetime.now(UTC))
    end = parse_until(until)
    if end is not None and end < start:
        raise LayerError("until is before since.")
    return client, connection.project_id, capability_id, slug, start, end


def backtest(
    models: Annotated[
        list[str] | None,
        typer.Option("--models", help="1–5 model ids, comma-separated or repeated."),
    ] = None,
    capability: Annotated[str, typer.Option("--capability", help="Capability slug or id.")] = "",
    since: Annotated[str, typer.Option("--since", help="ISO timestamp or a duration like 7d.")] = "7d",
    until: Annotated[str, typer.Option("--until", help="ISO timestamp.")] = "",
    limit: Annotated[int, typer.Option("--limit", min=1, max=10_000)] = 200,
    from_model: Annotated[str, typer.Option("--from-model", help="Only spans recorded from this model.")] = "",
    timeout: Annotated[str, typer.Option("--timeout", help="Wait budget, like 45m.")] = "45m",
    project_id: ProjectIdOption = "",
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
) -> None:
    """Score models on stored LLM calls. Exit 1 when any model regresses."""
    with guard(LayerError):
        chosen = parse_models(models, cap=BACKTEST_CAP)
        client, project, capability_id, slug, start, end = _prepare(
            api_key=api_key,
            api_url=api_url,
            path=path,
            project_id=project_id,
            capability=capability,
            since=since,
            until=until,
        )
        code = run_backtest(
            client,
            project_id=project,
            capability_id=capability_id,
            capability_slug=slug,
            models=chosen,
            since=start,
            until=end,
            limit=limit,
            from_model=from_model,
            timeout_s=parse_timeout(timeout),
        )
    raise typer.Exit(code)


def finetune(
    models: Annotated[
        list[str] | None,
        typer.Option("--models", help="1–4 catalog model ids, comma-separated or repeated."),
    ] = None,
    capability: Annotated[str, typer.Option("--capability", help="Capability slug or id.")] = "",
    since: Annotated[str, typer.Option("--since", help="ISO timestamp or a duration like 7d.")] = "7d",
    until: Annotated[str, typer.Option("--until", help="ISO timestamp.")] = "",
    limit: Annotated[int, typer.Option("--limit", min=1, max=10_000)] = 200,
    from_model: Annotated[str, typer.Option("--from-model", help="Only spans recorded from this model.")] = "",
    eval_percent: Annotated[int, typer.Option("--eval-percent", min=1, max=99)] = 20,
    timeout: Annotated[str, typer.Option("--timeout", help="Wait budget for landing, like 45m.")] = "45m",
    project_id: ProjectIdOption = "",
    api_key: ApiKeyOption = "",
    api_url: ApiUrlOption = "",
    path: ConfigPathOption = DEFAULT_PATH,
) -> None:
    """Start one fine-tune job per model from stored LLM calls. Does not wait for training."""
    with guard(LayerError):
        chosen = parse_models(models, cap=FINETUNE_CAP)
        client, project, capability_id, slug, start, end = _prepare(
            api_key=api_key,
            api_url=api_url,
            path=path,
            project_id=project_id,
            capability=capability,
            since=since,
            until=until,
        )
        code = run_finetune(
            client,
            project_id=project,
            capability_id=capability_id,
            capability_slug=slug,
            models=chosen,
            since=start,
            until=end,
            limit=limit,
            from_model=from_model,
            eval_percent=eval_percent,
            timeout_s=parse_timeout(timeout),
        )
    raise typer.Exit(code)
