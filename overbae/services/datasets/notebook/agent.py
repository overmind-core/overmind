from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

from django.db import close_old_connections
from django.utils import timezone

from overbae.models import Capability, Cell, Dataset
from overbae.services.datasets import diff as diff_svc
from overbae.services.datasets import lifecycle, paths, store
from overbae.services.datasets.notebook import engines, events, libraries, prompts
from overbae.services.datasets.notebook import run as run_svc

logger = logging.getLogger(__name__)

QUERY_ROWS = 50
SAMPLE_ROWS = 20
THOUGHT_CHARS = 2000
_SCRIPT_CHARS = 4000
_PREVIEW_ROWS = 3
_VALUE_CHARS = 400
PREPARE_DISPLAY = "Prepare this dataset: shape it to both contracts, then run the quality checks."


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _VALUE_CHARS:
        return value[:_VALUE_CHARS] + f"…[+{len(value) - _VALUE_CHARS}]"
    if isinstance(value, list):
        return [_clip(v) for v in value[:12]]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in list(value.items())[:30]}
    return value


def _safe(result: Any) -> Any:
    return json.loads(json.dumps(result, default=str))


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def _dataset(dataset_id: Any) -> Dataset:
    return Dataset.objects.select_related("capability").get(pk=dataset_id)


def resolve_cell(dataset: Dataset, ref: str | None, *, ran_only: bool = False) -> Cell:
    """A cell by version (``1.2``), id, or position; blank means the active one."""
    versions = dataset.versions()
    if not ref:
        cell = dataset.active_cell
        if cell is None:
            raise lifecycle.DatasetError("No version has run.", code="no_version")
        return cell
    ref = str(ref).strip()
    for cell_id, version in versions.items():
        if version == ref:
            cell = dataset.cells.get(pk=cell_id)
            break
    else:
        cell = dataset.cells.filter(pk=ref).first() if _is_uuid(ref) else None
        if cell is None and ref.isdigit():
            cell = dataset.cells.filter(position=int(ref)).first()
        if cell is None:
            raise lifecycle.DatasetError(f"No cell {ref}.", code="no_cell")
    if ran_only and not cell.ran:
        raise lifecycle.DatasetError(f"{ref} has not run.", code="not_ran")
    return cell


def _cell_line(dataset: Dataset, cell: Cell, versions: dict[Any, str]) -> dict[str, Any]:
    return {
        "version": versions.get(cell.id, "proposed"),
        "id": str(cell.id),
        "title": cell.title,
        "state": cell.state,
        "frozen": cell.frozen,
        "rows": cell.rows,
        "columns": _visible_columns([c["name"] for c in (cell.columns or [])]),
        "note": cell.note,
        "error": cell.error,
        "script": cell.script[:_SCRIPT_CHARS],
        "intent_report": {
            k: {"ok": v.get("ok"), "reason": v.get("reason")}
            for k, v in (cell.intent_report or {}).items()
        },
        "capability_report": cell.capability_report,
    }


def status(dataset: Dataset) -> dict[str, Any]:
    versions = dataset.versions()
    active = dataset.active_cell
    return {
        "dataset": dataset.name,
        "intent": dataset.intent,
        "capability": dataset.capability.name if dataset.capability_id else None,
        "capability_rank": dataset.capability_rank[:3],
        "state": dataset.state,
        "error": dataset.error,
        "active": versions.get(active.id) if active else None,
        "fits": dict(zip(("ok", "reason"), active.fits(dataset.intent), strict=True))
        if active
        else None,
        "cells": [_cell_line(dataset, c, versions) for c in dataset.chain],
    }


def _visible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_clip({k: v for k, v in r.items() if k != store.SOURCE_ROW}) for r in rows]


def _visible_columns(names: list[str]) -> list[str]:
    return [n for n in names if n != store.SOURCE_ROW]


def _preview(frame) -> dict[str, Any]:
    from overbae.services.datasets.store import manifest_from_frame

    return {
        "rows": int(len(frame)),
        "columns": _visible_columns([c["name"] for c in manifest_from_frame(frame)]),
        "head": _visible(frame.head(_PREVIEW_ROWS).to_dict(orient="records")),
    }


class Tools:
    def __init__(self, dataset_id: Any, user: Any, emit: Callable[[dict[str, Any]], None]):
        self.dataset_id = dataset_id
        self.user = user
        self.emit = emit
        self.touched: list[dict[str, str]] = []
        self.steps: list[dict[str, Any]] = []
        self._thinking: dict[str, Any] | None = None

    def think(self) -> None:
        if self._thinking is not None:
            return
        self._thinking = {
            "id": f"think-{len(self.steps)}",
            "started": time.monotonic(),
            "text": [],
        }
        self.step({"phase": "thinking", "id": self._thinking["id"], "status": "running"})

    def thought(self, text: str) -> None:
        self.think()
        self._thinking["text"].append(text)
        self.emit({"type": "chat_thinking", "id": self._thinking["id"], "text": text})

    def stop_thinking(self) -> None:
        if self._thinking is None:
            return
        ms = int((time.monotonic() - self._thinking["started"]) * 1000)
        part = {
            "phase": "thinking",
            "id": self._thinking["id"],
            "status": "done",
            "duration_ms": ms,
        }
        text = "".join(self._thinking["text"]).strip()
        if text:
            part["text"] = text[-THOUGHT_CHARS:]
        self.step(part)
        self._thinking = None

    def step(self, part: dict[str, Any]) -> None:
        self.steps.append(part)
        self.emit({"type": "chat_step", **part})

    def _touch(self, cell: Cell, action: str) -> None:
        self.touched.append({"id": str(cell.id), "action": action})
        self.emit({"type": "chat_cell", "cell_id": str(cell.id), "action": action})

    def status(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        return status(_dataset(self.dataset_id))

    def query(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        cell = resolve_cell(dataset, args.get("version"), ran_only=True)
        try:
            result = store.query(
                str(args.get("sql") or ""), limit=QUERY_ROWS, t=paths.cell_path(dataset.id, cell.id)
            )
        except Exception as exc:  # noqa: BLE001 — DuckDB raises many types; the message is the value
            return {"error": str(exc)[-600:]}
        return {
            "version": dataset.versions().get(cell.id),
            "rows": _visible(result["rows"]),
            "columns": _visible_columns(result["columns"]),
        }

    def diff(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        b = resolve_cell(dataset, args.get("to"), ran_only=True)
        a_ref = args.get("from")
        if a_ref:
            a = resolve_cell(dataset, a_ref, ran_only=True)
        else:
            a = (
                dataset.cells.filter(position__lt=b.position, state=Cell.State.OK)
                .order_by("-position")
                .first()
            )
            if a is None:
                return {"error": "Nothing before that version."}
        out = diff_svc.between(paths.cell_path(dataset.id, a.id), paths.cell_path(dataset.id, b.id))
        for key in ("changed_examples", "removed_examples"):
            if key in out:
                out[key] = _visible(out[key])
        return {"from": dataset.versions().get(a.id), "to": dataset.versions().get(b.id), **out}

    def try_script(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        after = resolve_cell(dataset, args.get("after"), ran_only=True)
        result = run_svc.try_script(dataset, str(args.get("script") or ""), after=after)
        if result.frame is None:
            return {"ok": False, "error": result.error, "stdout": result.stdout}
        return {"ok": True, **_preview(result.frame), "stdout": result.stdout}

    def inspect(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        at = resolve_cell(dataset, args.get("version"), ran_only=True)
        result = run_svc.inspect(dataset, str(args.get("script") or ""), at=at)
        if not result.ok:
            return {"ok": False, "error": result.error, "stdout": result.stdout}
        return {"ok": True, "stdout": result.stdout}

    def add_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        run = args.get("run", True) is not False
        try:
            cell = lifecycle.add_cell(
                dataset,
                title=str(args.get("title") or "Step"),
                script=str(args.get("script") or ""),
                note=str(args.get("note") or ""),
                proposed=not run,
                user=self.user,
            )
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self._touch(cell, "proposed" if not run else "created")
        self.emit({"type": "cells_changed"})
        if not run:
            return {"ok": True, "proposed": True, "id": str(cell.id), "title": cell.title}
        return self._run(dataset, cell)

    def edit_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            cell = resolve_cell(dataset, args.get("version") or args.get("id"))
            cell = lifecycle.edit_cell(
                dataset,
                cell,
                script=args.get("script"),
                title=args.get("title"),
                note=args.get("note"),
            )
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self._touch(cell, "edited")
        self.emit({"type": "cells_changed"})
        if cell.state == Cell.State.PROPOSED:
            return {"ok": True, "proposed": True, "id": str(cell.id)}
        return self._run(dataset, cell)

    def _run(self, dataset: Dataset, cell: Cell) -> dict[str, Any]:
        run_svc.execute(dataset, user=self.user, hold=Dataset.State.DIAGNOSING)
        dataset = _dataset(self.dataset_id)
        cell.refresh_from_db()
        self._touch(cell, "ran" if cell.state == Cell.State.OK else "failed")
        line = _cell_line(dataset, cell, dataset.versions())
        if cell.state != Cell.State.OK:
            return {"ok": False, **line}
        head = store.head(paths.cell_path(dataset.id, cell.id), _PREVIEW_ROWS)
        return {"ok": True, **line, "head": _visible(head)}

    def remove_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            cell = resolve_cell(dataset, args.get("version") or args.get("id"))
            proposed = cell.state == Cell.State.PROPOSED
            self._touch(cell, "removed")
            lifecycle.remove_cell(dataset, cell)
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "cells_changed"})
        if not proposed:
            run_svc.execute(dataset, user=self.user, hold=Dataset.State.DIAGNOSING)
        return {
            "ok": True,
            "removed": cell.title,
            "active": status(_dataset(self.dataset_id))["active"],
        }

    def set_active(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            cell = resolve_cell(dataset, args.get("version"), ran_only=True)
            lifecycle.set_active(dataset, cell)
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "active": dataset.versions().get(cell.id)}

    def set_intent(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            lifecycle.set_intent(dataset, str(args.get("intent") or ""))
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "intent": dataset.intent}

    def set_capability(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        ref = str(args.get("capability") or "").strip()
        capability = None
        if ref and ref.lower() != "none":
            candidates = Capability.objects.filter(project_id=dataset.project_id).exclude(
                status=Capability.Status.DELETED
            )
            capability = (
                candidates.filter(pk=ref).first()
                if _is_uuid(ref)
                else candidates.filter(name__iexact=ref).first()
                or candidates.filter(slug__iexact=ref).first()
            )
            if capability is None:
                names = list(candidates.order_by("name").values_list("name", flat=True)[:20])
                return {
                    "ok": False,
                    "error": f"No capability named {ref!r}.",
                    "capabilities": names,
                }
        try:
            lifecycle.set_capability(dataset, capability)
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "capability": capability.name if capability else None}

    def rename(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        name = str(args.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "A name is required."}
        lifecycle.rename(dataset, name)
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "name": dataset.name}

    def install(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            name, version = libraries.install(
                str(args.get("package") or ""), paths.library_cache(dataset.project_id)
            )
        except libraries.LibraryError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "package": name, "version": version}

    def handlers(self) -> dict[str, Callable[..., Any]]:
        return {name: _guarded(self, name, getattr(self, name)) for name in TOOL_SPECS}


_TEXT = {"type": "string"}

TOOL_SPECS: dict[str, tuple[str, dict]] = {
    "status": (
        "The dataset: intent, capability, every cell with version, state, shape, script and both contract reports.",
        {"type": "object", "properties": {}},
    ),
    "query": (
        "DuckDB SQL over one version's frame as table t. Aggregates read every row; 50 rows come back.",
        {
            "type": "object",
            "properties": {
                "sql": _TEXT,
                "version": {**_TEXT, "description": "1.2 etc; blank = active"},
            },
            "required": ["sql"],
        },
    ),
    "diff": (
        "What changed between two versions, with examples. from defaults to the version before to.",
        {"type": "object", "properties": {"from": _TEXT, "to": _TEXT}},
    ),
    "try_script": (
        "Run a cell script against a version's frame without landing it. Returns shape, columns, three rows, anything printed, or the error.",
        {
            "type": "object",
            "properties": {
                "script": _TEXT,
                "after": {**_TEXT, "description": "version the script reads; blank = active"},
            },
            "required": ["script"],
        },
    ),
    "inspect": (
        "Run a read-only script against a version's frame and read back what it printed. Lands nothing and needs no df. Use it to measure a check before you cut.",
        {
            "type": "object",
            "properties": {
                "script": _TEXT,
                "version": {**_TEXT, "description": "version the script reads; blank = active"},
            },
            "required": ["script"],
        },
    ),
    "add_cell": (
        "Land a cell at the end of the chain. run=true executes it now and returns the result; run=false leaves a proposal with a note for the user.",
        {
            "type": "object",
            "properties": {
                "title": _TEXT,
                "script": _TEXT,
                "note": {**_TEXT, "description": "one line: why, with the row count"},
                "run": {"type": "boolean"},
            },
            "required": ["title", "script"],
        },
    ),
    "edit_cell": (
        "Replace a cell's script (and optionally title or note) and re-run from it. Frozen cells refuse.",
        {
            "type": "object",
            "properties": {"version": _TEXT, "script": _TEXT, "title": _TEXT, "note": _TEXT},
            "required": ["version"],
        },
    ),
    "remove_cell": (
        "Delete a cell or a proposal. Later cells shift down and re-run. Frozen cells refuse.",
        {
            "type": "object",
            "properties": {"version": {**_TEXT, "description": "1.2 etc, or a proposal's id"}},
            "required": ["version"],
        },
    ),
    "set_active": (
        "Choose which ran version consumers read.",
        {"type": "object", "properties": {"version": _TEXT}, "required": ["version"]},
    ),
    "set_intent": (
        "Set the intent to train or eval. Fixed once a version was used.",
        {
            "type": "object",
            "properties": {"intent": {"type": "string", "enum": ["train", "eval"]}},
            "required": ["intent"],
        },
    ),
    "set_capability": (
        "Bind the dataset to a capability by name or id, or 'none' to clear it. Fixed once a version was used. Re-measures every version.",
        {"type": "object", "properties": {"capability": _TEXT}, "required": ["capability"]},
    ),
    "rename": (
        "Rename the dataset.",
        {"type": "object", "properties": {"name": _TEXT}, "required": ["name"]},
    ),
    "install": (
        "Install one package from the installable list in the Libraries section.",
        {"type": "object", "properties": {"package": _TEXT}, "required": ["package"]},
    ),
}


def tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": name, "description": description, "parameters": schema},
        }
        for name, (description, schema) in TOOL_SPECS.items()
    ]


TOOL_TITLES = {
    "status": "Read the chain",
    "query": "Query the frame",
    "diff": "Diff two versions",
    "try_script": "Try a script",
    "inspect": "Inspect the frame",
    "add_cell": "Add a cell",
    "edit_cell": "Edit a cell",
    "remove_cell": "Remove a cell",
    "set_active": "Set the active version",
    "set_intent": "Set the intent",
    "set_capability": "Set the capability",
    "rename": "Rename the dataset",
    "install": "Install a library",
}


def _guarded(tools: Tools, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:

    def call(args: dict[str, Any], ctx: Any = None) -> Any:
        args = dict(args or {})
        tools.stop_thinking()
        step_id = f"{name}-{len(tools.steps)}"
        tools.step(
            {
                "phase": "tool_start",
                "id": step_id,
                "tool": name,
                "title": TOOL_TITLES.get(name, name),
                "summary": json.dumps(_clip(args), ensure_ascii=False, default=str)[:600],
            }
        )
        started = time.monotonic()
        try:
            result = _safe(fn(args, ctx))
        except lifecycle.DatasetError as exc:
            result = {"ok": False, "error": exc.detail}
        except Exception as exc:  # noqa: BLE001
            logger.warning("notebook tool %s failed", name, exc_info=True)
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
        ok = not isinstance(result, dict) or (
            result.get("ok") is not False and not result.get("error")
        )
        tools.step(
            {
                "phase": "tool_done",
                "id": step_id,
                "tool": name,
                "ok": ok,
                "duration_ms": int((time.monotonic() - started) * 1000),
                "preview": json.dumps(_clip(result), ensure_ascii=False, default=str)[:800],
            }
        )
        tools.think()
        # The Cursor SDK runs each call on a fresh thread; no request_finished reaches it.
        close_old_connections()
        return result

    return call


def system_prompt(dataset: Dataset) -> str:
    source = dataset.source
    sample: list[dict[str, Any]] = []
    if source is not None and source.ran:
        frame_path = paths.cell_path(dataset.id, source.id)
        if frame_path.exists():
            sample = _visible(store.head(frame_path, SAMPLE_ROWS))
    return prompts.system(
        dataset.intent,
        capability=prompts.capability_section(dataset),
        libraries=libraries.describe(paths.library_cache(dataset.project_id)),
        sample=prompts.sample_section(sample),
    )


def iter_turn(
    dataset_id: Any, message: str, *, display: str, user: Any = None, turn_key: str = ""
) -> Iterator[dict[str, Any]]:
    dataset = _dataset(dataset_id)
    started = timezone.now().isoformat()
    # The turn task is acks_late; a redelivery must not land every cell twice.
    if turn_key and dataset.agent_turn_key == turn_key:
        logger.warning("dataset %s: turn %s already ran, skipping the retry", dataset_id, turn_key)
        return

    turn_user = {"role": "user", "text": display, "at": started}
    Dataset.objects.filter(pk=dataset_id).update(
        chat=[*(dataset.chat or []), turn_user], agent_turn_key=turn_key or ""
    )
    yield _emit(dataset_id, {"type": "chat_turn", **turn_user})

    pending: list[dict[str, Any]] = []
    tools = Tools(dataset_id, user, lambda event: pending.append(_emit(dataset_id, event)))
    engine = engines.select()
    outcome = engines.Outcome()
    turn_started = time.monotonic()
    tools.think()
    if engine is None:
        outcome.error = engines.NOT_CONFIGURED
    else:
        try:
            outcome = yield from engine.run(dataset, message, tools, pending)
        except Exception as exc:  # noqa: BLE001 — the turn must land on the page either way
            logger.warning("dataset %s: agent turn failed", dataset_id, exc_info=True)
            outcome.error = engine.describe_error(exc)
    tools.stop_thinking()
    while pending:
        yield pending.pop(0)

    text = outcome.text.strip()
    if not text and not outcome.error and not tools.touched:
        text = "Nothing to do."
    turn_agent = {
        "role": "agent",
        "text": text,
        "error": outcome.error,
        "cells": tools.touched,
        "steps": tools.steps,
        "ms": int((time.monotonic() - turn_started) * 1000),
        "at": timezone.now().isoformat(),
        "engine": engine.name if engine else "",
        "model": outcome.stats.get("served_model", "") if engine else "",
    }
    dataset = Dataset.objects.get(pk=dataset_id)
    Dataset.objects.filter(pk=dataset_id).update(chat=[*(dataset.chat or []), turn_agent])
    _bill(dataset, user, outcome.stats, turn_agent, started)
    yield _emit(dataset_id, {"type": "chat_turn", **turn_agent})


def _bill(
    dataset: Dataset, user: Any, stats: dict[str, Any], turn: dict[str, Any], started: str
) -> None:
    if not stats or not getattr(user, "pk", None):
        return
    try:
        from overbae.models import BillingService
        from overbae.services.billing_ledger import charge_llm_usage

        charge_llm_usage(
            user,
            stats,
            service=BillingService.DATA_WORKSHOP,
            project_id=dataset.project_id,
            idempotency_key=f"data-workshop:{dataset.id}:{started}",
            metadata={
                "dataset_id": str(dataset.id),
                "engine": turn["engine"],
                "model": turn["model"],
            },
        )
    except Exception:  # noqa: BLE001 — billing never fails a turn
        logger.warning("dataset %s: workshop billing failed", dataset.id, exc_info=True)


def _emit(dataset_id: Any, event: dict[str, Any]) -> dict[str, Any]:
    payload = {"dataset_id": str(dataset_id), **event}
    events.publish(dataset_id, payload)
    return payload


def settle(dataset_id: Any) -> None:
    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None or dataset.state != Dataset.State.DIAGNOSING:
        return
    state = Dataset.State.ERROR if dataset.error else Dataset.State.IDLE
    Dataset.objects.filter(pk=dataset_id, state=Dataset.State.DIAGNOSING).update(
        state=state, updated_at=timezone.now()
    )
    _emit(dataset_id, {"type": "dataset_changed"})


def diagnose(dataset_id: Any, *, user: Any = None, turn_key: str = "") -> Iterator[dict[str, Any]]:
    """The one automatic turn after landing: both contracts, then quality."""
    if not lifecycle.enter_busy(
        dataset_id,
        Dataset.State.DIAGNOSING,
        from_states=[Dataset.State.DIAGNOSING, Dataset.State.IDLE],
    ):
        return
    try:
        yield from iter_turn(
            dataset_id,
            prompts.PREPARE,
            display=PREPARE_DISPLAY,
            user=user,
            turn_key=turn_key,
        )
    finally:
        settle(dataset_id)


def follow_up(
    dataset_id: Any, message: str, *, user: Any = None, turn_key: str = ""
) -> Iterator[dict[str, Any]]:
    if not lifecycle.enter_busy(
        dataset_id,
        Dataset.State.DIAGNOSING,
        from_states=[Dataset.State.DIAGNOSING, Dataset.State.IDLE, Dataset.State.ERROR],
    ):
        return
    try:
        yield from iter_turn(
            dataset_id,
            prompts.FOLLOW_UP.format(message=message.strip()),
            display=message.strip(),
            user=user,
            turn_key=turn_key,
        )
    finally:
        settle(dataset_id)


def transcript(dataset: Dataset) -> str:
    return json.dumps(dataset.chat or [], indent=1, default=str)
