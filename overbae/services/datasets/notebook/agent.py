from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from threading import RLock
from typing import Any

from django.db import transaction
from django.utils import timezone
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from overbae.models import Capability, Cell, Dataset
from overbae.services.billing_ledger import record_workshop_usage
from overbae.services.chatgpt import ChatGPTError
from overbae.services.datasets import (
    chunking,
    generation,
    generation_quality,
    lifecycle,
    operations,
    paths,
    preparation,
    review,
    sampling,
    semantic_checks,
    store,
    workflow,
)
from overbae.services.datasets import diff as diff_svc
from overbae.services.datasets.context import context_fingerprint, workshop_context
from overbae.services.datasets.notebook import engines, events, libraries, prompts
from overbae.services.datasets.notebook import run as run_svc
from overbae.services.datasets.proposals import retire_outdated

logger = logging.getLogger(__name__)

QUERY_ROWS = 50
THOUGHT_CHARS = 16_000
_SCRIPT_CHARS = 4000
_PREVIEW_ROWS = 3
_VALUE_CHARS = 400
PREPARE_DISPLAY = "Prepare this dataset for its purpose and capability, then check its quality."
INTENT_QUESTION = "What will you use this data for?"


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


def _cell_line(
    dataset: Dataset,
    cell: Cell,
    versions: dict[Any, str],
    *,
    frozen_before: int | None = None,
    context: str | None = None,
) -> dict[str, Any]:
    return {
        "version": versions.get(cell.id, "proposed"),
        "id": str(cell.id),
        "title": cell.title,
        "state": cell.state,
        "frozen": cell.frozen if frozen_before is None else cell.position <= frozen_before,
        "rows": cell.rows,
        "fingerprint": cell.fingerprint,
        "input_fingerprint": cell.input_fingerprint,
        "seconds": cell.seconds,
        "columns": _visible_columns([c["name"] for c in (cell.columns or [])]),
        "note": cell.note,
        "error": cell.error,
        "script": cell.script[:_SCRIPT_CHARS],
        "script_truncated": len(cell.script) > _SCRIPT_CHARS,
        "intent_report": {
            k: {key: v[key] for key in ("ok", "reason", "fixable") if key in v}
            for k, v in (cell.intent_report or {}).items()
        },
        "capability_report": cell.capability_report,
        "review": review.summary(cell.review),
        "readiness": review.readiness(dataset, cell, context=context) if cell.ran else None,
        "quality_report": review.summary(cell.quality_report),
    }


def status(dataset: Dataset) -> dict[str, Any]:
    chain = dataset.chain
    versions = dataset.versions(chain=chain)
    ran = [cell for cell in chain if cell.state == Cell.State.OK]
    active = next((cell for cell in ran if cell.id == dataset.active_id), ran[-1] if ran else None)
    frozen = max((cell.position for cell in chain if cell.used_at is not None), default=-1)
    context = context_fingerprint(dataset.capability)
    return {
        "dataset": dataset.name,
        "brief": dataset.brief,
        "source": dataset.source_spec,
        "intent": dataset.intent,
        "capability": dataset.capability.name if dataset.capability_id else None,
        "capability_rank": dataset.capability_rank[:3],
        "state": dataset.state,
        "preparation_plan": preparation.describe(dataset),
        "workflow": workflow.describe(dataset),
        "error": dataset.error,
        "active": versions.get(active.id) if active else None,
        "active_id": str(active.id) if active else None,
        "fits": dict(zip(("ok", "reason"), active.fits(dataset.intent), strict=True))
        if active
        else None,
        "cells": [
            _cell_line(dataset, c, versions, frozen_before=frozen, context=context) for c in chain
        ],
    }


def _visible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _clip({k: v for k, v in r.items() if k not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}})
        for r in rows
    ]


def _visible_columns(names: list[str]) -> list[str]:
    return [n for n in names if n not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}]


def _preview(frame) -> dict[str, Any]:
    from overbae.services.datasets.store import manifest_from_frame

    return {
        "rows": int(len(frame)),
        "columns": _visible_columns([c["name"] for c in manifest_from_frame(frame)]),
        "head": _visible(frame.head(_PREVIEW_ROWS).to_dict(orient="records")),
    }


def _preview_path(path):
    return {
        "rows": store.row_count(path),
        "columns": _visible_columns([c["name"] for c in store.read_manifest(path)]),
        "head": _visible(store.head(path, _PREVIEW_ROWS)),
    }


class Tools:
    def __init__(self, dataset_id: Any, user: Any, emit: Callable[[dict[str, Any]], None]):
        self.dataset_id = dataset_id
        self.cell_references = {
            version: str(cell_id) for cell_id, version in _dataset(dataset_id).versions().items()
        }
        self.operation_id: str | None = None
        self.user = user
        self.emit = emit
        self.touched: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self._thinking: dict[str, Any] | None = None
        self.automatic = False
        self.preparation_turn = False
        self.exploration = []
        self.user_request = ""
        self.lock = RLock()
        self.turn_id = ""
        self.progress: dict[str, Any] = {}
        self.workflow_id = workflow.ensure(_dataset(dataset_id), user=user).pk
        self.stop_requested = workflow.current(dataset_id).state == "blocked"
        self.generation: dict[str, Any] | None = None
        self.last_thought_save = 0.0
        self.text = ""
        self.text_offset = 0
        self.step_offsets: dict[str, int] = {}
        self.response_break = False
        self.preview = None
        self.user_request = ""

    def resolve_cell(self, dataset, reference, **kwargs):
        return resolve_cell(dataset, self.cell_references.get(reference, reference), **kwargs)

    def requires_preparation_plan(self, dataset: Dataset) -> bool:
        return dataset.intent in {Dataset.Intent.TRAIN, Dataset.Intent.EVAL} and (
            self.automatic or self.preparation_turn
        )

    def report_progress(self, stage: str, label: str, detail: str, **values: Any) -> None:
        self.progress = {
            **self.progress,
            **values,
            "stage": stage,
            "label": label,
            "detail": detail,
            "updated_at": timezone.now().isoformat(),
        }
        if self.turn_id:
            save_turn(
                self.dataset_id,
                self.turn_id,
                {
                    "progress": self.progress,
                    "cells": self.touched,
                    "steps": self.steps,
                    "text": self.text,
                },
            )
        self.emit(
            {
                "type": "chat_progress",
                "progress": self.progress,
                "text": self.text,
                "steps": self.steps,
                "cells": self.touched,
            }
        )

    def respond(self, text: str) -> None:
        self.stop_thinking()
        if self.response_break and self.text and not self.text.endswith("\n\n"):
            text = "\n\n" + text
        self.response_break = False
        self.text += text
        # Offsets use JavaScript's UTF-16 string indexing in the streamed chat.
        self.text_offset += len(text.encode("utf-16-le")) // 2
        self.emit({"type": "chat_delta", "text": text})
        if time.monotonic() - self.last_thought_save >= 1:
            self.last_thought_save = time.monotonic()
            self.report_progress(
                self.progress.get("stage", "working"),
                self.progress.get("label", "Working"),
                self.progress.get("detail", ""),
            )

    def start_response(self) -> None:
        self.response_break = True

    def think(self) -> None:
        if self._thinking is not None:
            return
        part = {"phase": "thinking", "id": f"think-{len(self.steps)}", "status": "running"}
        self._thinking = {
            "id": f"think-{len(self.steps)}",
            "started": time.monotonic(),
            "text": "",
            "part": part,
        }
        self.step(part)

    def thought(self, text: str) -> None:
        self.think()
        self._thinking["text"] = (self._thinking["text"] + text)[-THOUGHT_CHARS:]
        self._thinking["part"]["text"] = self._thinking["text"]
        self.emit({"type": "chat_thinking", "id": self._thinking["id"], "text": text})
        now = time.monotonic()
        # Snapshot streaming activity at most once a second, not once per token.
        if now - self.last_thought_save >= 1:
            self.last_thought_save = now
            self.report_progress(
                self.progress.get("stage", "working"),
                self.progress.get("label", "Thinking"),
                self.progress.get("detail", ""),
            )

    def stop_thinking(self, *, duration_ms: int | None = None) -> None:
        if self._thinking is None:
            return
        ms = (
            duration_ms
            if duration_ms is not None
            else int((time.monotonic() - self._thinking["started"]) * 1000)
        )
        part = {
            "phase": "thinking",
            "id": self._thinking["id"],
            "status": "done",
            "duration_ms": ms,
        }
        text = self._thinking["text"].strip()
        if text:
            part["text"] = text[-THOUGHT_CHARS:]
        self.step(part)
        self._thinking = None

    def step(self, part: dict[str, Any]) -> None:
        if part["id"] not in self.step_offsets:
            self.step_offsets[part["id"]] = self.text_offset
        # A late completion belongs at the step's start, not between current text chunks.
        part["text_offset"] = self.step_offsets[part["id"]]
        self.steps.append(part)
        self.emit({"type": "chat_step", **part})

    def _touch(self, cell: Cell, action: str) -> None:
        offset = next(
            (ref["text_offset"] for ref in self.touched if ref["id"] == str(cell.id)),
            self.text_offset,
        )
        self.touched = [ref for ref in self.touched if ref["id"] != str(cell.id)]
        self.touched.append({"id": str(cell.id), "action": action, "text_offset": offset})
        self.emit(
            {"type": "chat_cell", "cell_id": str(cell.id), "action": action, "text_offset": offset}
        )

    def status(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        result = status(_dataset(self.dataset_id))
        result["available_tools"] = list(self.specs())
        for cell in result["cells"]:
            if cell["version"] != "proposed":
                self.cell_references.setdefault(cell["version"], cell["id"])
        return result

    def record_preparation_plan(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            request = preparation.PlanRequest.model_validate(args)
            cell = self.resolve_cell(dataset, request.version, ran_only=True)
            preparation.save_plan(
                dataset,
                cell,
                request,
                user_request=self.user_request,
                exploration=self.exploration,
                automatic=self.requires_preparation_plan(dataset),
            )
        except (ValueError, lifecycle.DatasetError) as exc:
            return {"ok": False, "error": str(exc)}
        workflow.bind_plan(dataset, dataset.preparation_plan)
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "plan": preparation.describe(dataset)}

    def query(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        cell = self.resolve_cell(dataset, args.get("version"), ran_only=True)
        try:
            result = store.query(
                str(args.get("sql") or ""), limit=QUERY_ROWS, t=paths.cell_path(dataset.id, cell.id)
            )
        except Exception as exc:  # noqa: BLE001 — DuckDB raises many types; the message is the value
            return {"ok": False, "error": str(exc)[-600:]}
        self.exploration.append(
            {
                "cell": str(cell.id),
                "fingerprint": cell.fingerprint,
                "tool": "query",
                "sql": str(args.get("sql") or "")[:4000],
                "preview": _safe(_clip(result)),
                "preview_bounded": True,
            }
        )
        return {
            "version": dataset.versions().get(cell.id),
            "rows": _visible(result["rows"]),
            "columns": _visible_columns(result["columns"]),
        }

    def diff(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        b = self.resolve_cell(dataset, args.get("to"), ran_only=True)
        a_ref = args.get("from")
        if a_ref:
            a = self.resolve_cell(dataset, a_ref, ran_only=True)
        else:
            a = (
                dataset.cells.filter(position__lt=b.position, state=Cell.State.OK)
                .order_by("-position")
                .first()
            )
            if a is None:
                return {"ok": False, "error": "Nothing before that version."}
        out = diff_svc.between(paths.cell_path(dataset.id, a.id), paths.cell_path(dataset.id, b.id))
        for key in ("changed_examples", "removed_examples"):
            if key in out:
                out[key] = _visible(out[key])
        return {"from": dataset.versions().get(a.id), "to": dataset.versions().get(b.id), **out}

    def try_script(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        after = self.resolve_cell(dataset, args.get("version"), ran_only=True)
        result = run_svc.try_script(dataset, str(args.get("script") or ""), after=after)
        self.preview = (after.id, after.fingerprint, str(args.get("script") or ""), result)
        if result.path is None:
            return {"ok": False, "error": result.error, "stdout": result.stdout}
        return {"ok": True, **_preview_path(result.path), "stdout": result.stdout}

    def inspect(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        at = self.resolve_cell(dataset, args.get("version"), ran_only=True)
        result = run_svc.inspect(dataset, str(args.get("script") or ""), at=at)
        if not result.ok:
            return {"ok": False, "error": result.error, "stdout": result.stdout}
        self.exploration.append(
            {
                "cell": str(at.id),
                "fingerprint": at.fingerprint,
                "tool": "inspect",
                "script": str(args.get("script") or "")[:4000],
                "stdout": result.stdout[:4000],
                "preview_bounded": True,
            }
        )
        return {"ok": True, "stdout": result.stdout}

    def add_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        previous = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        if previous is None or not previous.ran:
            return {"ok": False, "error": "Run or fix the existing chain first."}
        try:
            plan = preparation.execution_plan(
                dataset,
                previous,
                args.get("plan_step"),
                required=self.requires_preparation_plan(dataset),
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        script = str(args.get("script") or "")
        for pending in dataset.cells.filter(state=Cell.State.PROPOSED, script=script):
            report = pending.review
            if (
                report.get("input_fingerprint") == previous.fingerprint
                and report.get("context_fingerprint") == context_fingerprint(dataset.capability)
                and report.get("intent") == dataset.intent
                and pending.preparation_plan.get("id") == plan.get("id")
                and pending.preparation_plan.get("step_id") == plan.get("step_id")
                and paths.cell_path(dataset.id, pending.id).exists()
                and store.file_sha256(paths.cell_path(dataset.id, pending.id))
                == report.get("output_fingerprint")
            ):
                lifecycle.accept_proposal(dataset, pending)
                pending.review = {**pending.review, "approval": "agent"}
                pending.save(update_fields=["review"])
                self._touch(pending, "created")
                return {**self._run(dataset, pending), "reused": True}
        key = (previous.id, previous.fingerprint, script)
        result = (
            self.preview[3]
            if self.preview and self.preview[:3] == key
            else run_svc.try_script(dataset, script, after=previous)
        )
        self.preview = None
        if result.path is None:
            return {
                "ok": False,
                "error": result.error,
                "failure": {"code": "execution_failed", "category": "execution"},
            }
        before = paths.cell_path(dataset.id, previous.id)
        if review.same_data(before, result.path):
            return {
                "ok": True,
                "unchanged": True,
                "proposed": False,
                **_cell_line(dataset, previous, dataset.versions()),
            }
        changes = review.impact_files(before, result.path)
        if self.generation and (
            changes["rows_added"] or store.row_count(result.path) > store.row_count(before)
        ):
            return {
                "ok": False,
                "error": "Generate new examples with add_synthetic_rows. Do not expand the dataset by copying rows or remapping identifiers in a script.",
            }
        kind = args.get("kind", "mechanical")
        if kind not in {"mechanical", "semantic"}:
            return {"ok": False, "error": "kind must be mechanical or semantic."}
        if changes["instruction_changes"] or changes["decision_changes"]:
            kind = "semantic"
        try:
            cell = lifecycle.add_cell(
                dataset,
                title=str(args.get("title") or "Step"),
                script=str(args.get("script") or ""),
                note=str(args.get("note") or ""),
                user=self.user,
            )
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        cell.preparation_plan = plan
        cell.save(update_fields=["preparation_plan"])
        self._touch(cell, "created")
        self.emit({"type": "cells_changed"})
        report = review.save_proposal(
            dataset, cell, previous, result.path, kind=kind, note=str(args.get("note") or "")
        )
        cell.review = {
            **report,
            "status": "accepted",
            "approval": "agent",
        }
        cell.save(update_fields=["review"])
        return self._run(dataset, cell)

    def prepare_examples(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        if dataset.intent not in {"train", "eval"}:
            return {"ok": False, "error": "Set the dataset purpose before preparing examples."}
        spec = dataset.preparation_plan.get("specification", {}) if args.get("plan_step") else {}
        kwargs = "".join(
            f", {key}={spec[key]!r}" for key in ("mapping", "constants") if spec.get(key)
        )
        return self.add_cell(
            {
                "plan_step": args.get("plan_step"),
                "title": "Prepare evaluation examples"
                if dataset.intent == "eval"
                else "Prepare training examples",
                "script": f"def transform_batch(df):\n    return prepare_examples(df, intent={dataset.intent!r}{kwargs})",
                "note": "Preserve evidence, multiplicity and declared native targets; separate evaluation references from inputs.",
            }
        )

    def sample_rows(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        try:
            request = sampling.validate_request(
                **{k: v for k, v in args.items() if k != "plan_step"}
            )
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return self.add_cell(
            {
                "title": "Representative sample",
                "plan_step": args.get("plan_step"),
                "script": "df = sample_rows("
                + ", ".join(f"{key}={value!r}" for key, value in request.items())
                + ")",
                "note": f"Select {request['rows']} unchanged rows with seed {request['seed']}; retain minimum stratum coverage and allocate remaining capacity proportionally. This is a sample, not a train/eval split.",
                "kind": "semantic",
            }
        )

    def chunk_text(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        source = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        plan = preparation.execution_plan(dataset, source, args.get("plan_step"), required=True)
        cell = chunking.chunk_text(
            dataset,
            source,
            text_column=args["text_column"],
            group_by=args.get("group_by", []),
            max_chars=args["max_chars"],
            plan=plan,
            user=self.user,
        )
        self._touch(cell, "ran")
        self.emit({"type": "cells_changed"})
        return {"ok": True, **_cell_line(dataset, cell, dataset.versions())}

    def seed_examples(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        mode = args.get("mode", "augment")
        if self.automatic and mode != "derive":
            return {"ok": False, "error": "Synthetic generation requires a user request."}
        source = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        if source is None or not source.ran:
            return {"ok": False, "error": "Run or fix the existing chain first."}
        run_id = args.get("run_id")
        if run_id:
            saved = generation.get(run_id)
            if saved.dataset_id != dataset.pk:
                return {"ok": False, "error": "No saved generation with that run id."}
            source = saved.source
        try:
            plan = preparation.execution_plan(
                dataset, source, args.get("plan_step"), required=mode == "derive"
            )
            run = generation.start(
                dataset,
                source,
                target_rows=args.get("target_rows"),
                instruction=str(args.get("instruction") or ""),
                mode=mode,
                plan=plan,
                user=self.user,
                parent=workflow.current(dataset.pk),
                run_id=run_id,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.generation = {"id": str(run.pk), "instruction": run.request, "mode": mode}
        summary = generation.describe(run.pk)
        self.report_progress(
            "generating",
            "Generating examples",
            run.request,
            rows_before=run.specification["source_rows"] if mode == "augment" else 0,
            **{
                k: summary[k]
                for k in (
                    "source_rows",
                    "target_rows",
                    "generated_rows",
                    "remaining_rows",
                    "batches",
                    "source_rows_without_examples",
                )
            },
            run_id=str(run.pk),
        )
        return {
            "ok": True,
            **summary,
            "run_id": str(run.pk),
            "examples": generation.seeds(run, limit=min(max(int(args.get("limit", 3)), 1), 10)),
        }

    def add_synthetic_rows(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        if self.automatic and (self.generation or {}).get("mode") != "derive":
            return {"ok": False, "error": "Synthetic generation requires a user request."}
        if self.generation is None:
            return {
                "ok": False,
                "error": "Call seed_examples with target_rows and instruction first.",
            }
        examples = args.get("examples")
        key = hashlib.sha256(store.json_dumps(examples).encode()).hexdigest()
        result = generation.accept_batch(self.generation["id"], key, examples)
        cell = generation.publish(self.generation["id"]) if result["remaining_rows"] == 0 else None
        summary = generation.describe(self.generation["id"])
        if cell:
            self._touch(cell, "ran")
            self.emit({"type": "cells_changed"})
        self.report_progress(
            "generating",
            "Generating examples",
            self.generation["instruction"],
            **{
                k: summary[k]
                for k in (
                    "generated_rows",
                    "remaining_rows",
                    "batches",
                    "source_rows_without_examples",
                )
            },
            run_id=self.generation["id"],
            cell_id=str(cell.pk) if cell else None,
        )
        return {
            "ok": True,
            **summary,
            "id": str(cell.pk) if cell else None,
            "rows_after": summary["generated_rows"] + self.progress.get("rows_before", 0),
            "version": _dataset(self.dataset_id).versions().get(cell.pk) if cell else None,
            "run_id": self.generation["id"],
        }

    def generate_examples(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        if self.generation is None:
            return {"ok": False, "error": "Configure the saved recipe with seed_examples first."}
        run = generation.get(self.generation["id"])
        generation.validate_source(run)
        if run.generated_rows == 0:
            return {
                "ok": False,
                "error": "Save a small valid example batch with add_synthetic_rows before scheduling this recipe.",
            }
        if run.output_id:
            return {"ok": True, **generation.describe(run.pk)}
        qualification = generation_quality.qualify(run.pk)
        run.refresh_from_db()
        if qualification["status"] == "failed":
            return {
                "ok": False,
                "error": "The pilot did not meet the saved task. Revise the generation instruction using these findings and call seed_examples again; the previous pilot remains saved.",
                "qualification": qualification,
            }
        if run.state in {"paused", "cancelled", "blocked"}:
            return {
                "ok": False,
                "error": "The saved workflow is stopped.",
                **generation.describe(run.pk),
            }
        run.state = "queued"
        run.save(update_fields=["state", "updated_at"])
        self.stop_requested = True
        return {"ok": True, **generation.describe(run.pk)}

    def record_quality_review(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        cell = self.resolve_cell(dataset, args.get("version"), ran_only=True)
        try:
            report = review.record_quality(
                dataset, cell, args.get("checks") or [], script=str(args.get("script") or "")
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        self.emit({"type": "cells_changed"})
        return {"ok": True, "quality_report": report, "readiness": review.readiness(dataset, cell)}

    def check_semantic_quality(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            request = semantic_checks.SemanticReviewRequest.model_validate(args)
            cell = self.resolve_cell(dataset, request.version, ran_only=True)
            if self.requires_preparation_plan(dataset):
                reserved = preparation.reserve_semantic_rows(
                    dataset, cell, [check.name for check in request.checks], request.max_rows
                )
                if not reserved:
                    return {
                        "ok": True,
                        "skipped": True,
                        "unverified_rows": cell.rows,
                        "reason": "No semantic rows available for these checks in the current preparation plan. Unmeasured results remain unknown.",
                    }
                request = request.model_copy(update={"max_rows": reserved})
            result = semantic_checks.run_checks(
                dataset,
                cell,
                request,
                user=self.user,
                chatgpt_session=getattr(self, "chatgpt_session", None),
            )
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "error": str(exc)}
        self.emit({"type": "cells_changed"})
        return {"ok": True, **result, "readiness": review.readiness(dataset, cell)}

    def edit_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        if self.requires_preparation_plan(_dataset(self.dataset_id)):
            return {
                "ok": False,
                "error": "During preparation, revise the plan and add a repair cell with plan_step. Keep earlier executed versions and their plan snapshots intact.",
            }
        version = args.get("version")
        if not isinstance(version, str) or not version.strip():
            return {
                "ok": False,
                "error": "version is required. Use a cell version or UUID from status.",
            }
        dataset = _dataset(self.dataset_id)
        try:
            cell = self.resolve_cell(dataset, version)
            if args.get("script") is not None:
                previous = (
                    dataset.cells.filter(position__lt=cell.position, state=Cell.State.OK)
                    .order_by("-position")
                    .first()
                )
                if previous is None:
                    return {"ok": False, "error": "The source cannot be edited."}
                result = run_svc.try_script(dataset, str(args["script"]), after=previous)
                if result.path is None:
                    return {"ok": False, "error": result.error}
                changes = review.impact_files(paths.cell_path(dataset.id, previous.id), result.path)
                if args.get("kind") == "semantic" or review.requires_approval(changes):
                    return {
                        "ok": False,
                        "error": "Keep the earlier version intact. Use add_cell with kind=semantic and a script written against the current chain tail; it runs directly without approval.",
                    }
            cell = lifecycle.edit_cell(
                dataset,
                cell,
                script=args.get("script"),
                title=args.get("title"),
                note=args.get("note"),
            )
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self._touch(cell, "edited")
        self.emit({"type": "cells_changed"})
        if cell.state == Cell.State.PROPOSED:
            return {"ok": True, "proposed": True, "id": str(cell.id)}
        return self._run(dataset, cell)

    def _run(self, dataset: Dataset, cell: Cell) -> dict[str, Any]:
        tail = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        run_svc.execute(
            dataset,
            user=self.user,
            hold=Dataset.State.DIAGNOSING,
            activate_cell_id=tail.id if tail else None,
        )
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
        ref = str(args.get("id") or "")
        if not _is_uuid(ref):
            return {
                "ok": False,
                "error": "Provide the pending proposal's exact UUID from status. Versions and positions cannot be discarded.",
            }
        try:
            cell = dataset.cells.filter(pk=ref).first()
            lifecycle.discard_proposal(dataset, ref)
            self._touch(cell, "removed")
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "cells_changed"})
        return {
            "ok": True,
            "removed": cell.title,
            "active": status(_dataset(self.dataset_id))["active"],
        }

    def set_active(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            cell = self.resolve_cell(dataset, args.get("version"), ran_only=True)
            lifecycle.set_active(dataset, cell)
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "active": dataset.versions().get(cell.id)}

    def set_intent(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        evidence = str(args.get("evidence") or "").strip()
        if not evidence or evidence.casefold() not in self.user_request.casefold():
            return {
                "ok": False,
                "error": "Intent requires an exact quote from the user's request explicitly choosing its purpose. Otherwise ask the intent question and stop.",
            }
        try:
            lifecycle.set_intent(dataset, str(args.get("intent") or ""))
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            return {"ok": False, "error": exc.detail}
        self.emit({"type": "dataset_changed"})
        return {"ok": True, "intent": dataset.intent, "playbook": prompts.PLAYBOOKS[dataset.intent]}

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
        except ChatGPTError:
            raise
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

    def specs(self):
        dataset = _dataset(self.dataset_id)
        available = set(TOOL_SPECS)
        if self.stop_requested:
            available &= workflow.READ_ONLY
        elif dataset.intent == Dataset.Intent.PENDING:
            available &= workflow.READ_ONLY | {"set_intent", "rename", "record_preparation_plan"}
        if not self.generation:
            available -= {"add_synthetic_rows", "generate_examples"}
        if dataset.frozen_before >= 0:
            available -= {"set_intent", "set_capability"}
        if not dataset.cells.filter(state=Cell.State.PROPOSED).exists():
            available.discard("remove_cell")
        return {name: spec for name, spec in TOOL_SPECS.items() if name in available}

    def schemas(self):
        return [
            {
                "type": "function",
                "function": {"name": name, "description": description, "parameters": schema},
            }
            for name, (description, schema) in self.specs().items()
        ]


_TEXT = {"type": "string"}

TOOL_SPECS: dict[str, tuple[str, dict]] = {
    "record_preparation_plan": (
        "Save a complete preparation plan before preparing rows. Automatic preparation first requires query or inspect on this exact version, plus outcome.task, outcome.deliverables and outcome.model_input describing inference inputs separately from generator-only evidence. Required arguments: version, objective, consumer, understanding, families and checks. Families need name and evidence; only list observed column paths. Non-unknown target_meaning requires target_evidence; a supported interpretation also needs evidence_references, interpretation_scope and no conflicts. Constants are limited to shared decision.state, decision.kind, decision.options, decision.option_values and decision.target_semantics. Derive decision.question and row targets in a custom transform. Checks need name, category, method and question. Declare steps with id, description and kind; execution uses plan_step. Semantic row budget bounds automatic judging across turns. Plans record evidence and assumptions, not proof of label truth. Correct the exact field named by validation; do not repeat a rejected plan. Revise when evidence or intent changes.",
        preparation.PlanRequest.model_json_schema(),
    ),
    "check_semantic_quality": (
        "Measure semantic row quality against named evidence and answer fields using the selected Workshop funding source. Nested objects and list indices are supported: inspect roles, then select e.g. messages.1.content as user evidence and messages.2.content as the assistant answer when those indices match. Never select a whole transcript as both evidence and answer. Audit after final shaping; do not add/remove helper columns when indexed paths suffice. Unknowns stay null; findings are advisory. Processes at most 200 unmeasured rows per call within the saved semantic budget. Changing the version, task context, or checks starts a new audit. Use record_quality_review for deterministic format/schema checks.",
        semantic_checks.SemanticReviewRequest.model_json_schema(),
    ),
    "prepare_examples": (
        "Prepare typed decisions or conversations in bounded batches. Native train preserves full probability targets or declared target_mean/option_values; native eval separates input.decision from expected_output.probabilities or {mean, values}. Target meaning comes from the inspected preparation plan, never a numeric-shape heuristic. Preserve blank states, duplicates, option order and weights. Conversations retain instructions, evidence and tools. Invalid data stays visible; never normalize targets or invent evidence.",
        {"type": "object", "properties": {"plan_step": _TEXT}},
    ),
    "sample_rows": (
        "Apply a deterministic sample of unchanged rows as a new active version. During preparation, first save record_preparation_plan with a kind=sample step, then pass its id as plan_step here. A description of a plan in chat is not a saved plan. Reads the full source in bounded passes; no quota tables or scripts needed. Stratify by scalar column paths, optionally native hard/soft targets. Reserve minimum coverage per nonempty stratum, then allocate remaining capacity proportionally by largest remainder. Preserves row order, multiplicity and lineage. Does not create train/eval splits. Fails instead of silently underfilling. The saved output and original source remain inspectable.",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rows": {"type": "integer", "minimum": 1, "maximum": 1000000},
                "plan_step": _TEXT,
                "seed": {"type": "integer", "minimum": 0, "maximum": 4294967295},
                "stratify_by": {
                    "type": "array",
                    "maxItems": 8,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 200},
                },
                "target_type": {"type": "boolean", "default": False},
                "minimum_per_stratum": {"type": "integer", "minimum": 1, "default": 1},
            },
            "required": ["rows", "seed"],
        },
    ),
    "status": (
        "The dataset: intent, capability, every cell with version, state, shape, script and both contract reports.",
        {"type": "object", "properties": {}},
    ),
    "query": (
        "DuckDB SQL over one version's frame as table t. Declared nested columns have JSON type: use json_extract_string(column, '$.field') for text and cast extracted arrays to DOUBLE[] for arithmetic. For a per-row array statistic use list_min/list_max/list_sum on the extracted array, not a correlated SELECT over UNNEST: correlation can materialize the whole JSON payload and exhaust memory. Aggregates read every row; 50 rows come back. For complex large nested-array scans use inspect with inspect_batch(df). Reuse measured profile counts on the same fingerprint.",
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
        "Run a cell script against a version's frame without landing it. Returns whole-output shape, columns, three rows, anything printed, or the error. Define transform_batch(df) returning a DataFrame for large row-local transforms; ordinary scripts operate on the entire frame.",
        {
            "type": "object",
            "properties": {
                "script": _TEXT,
                "version": {
                    **_TEXT,
                    "description": "Exact cell UUID or version label; blank = active",
                },
            },
            "required": ["script"],
        },
    ),
    "inspect": (
        "Run read-only Python and return printed output, without landing a cell. For large frames define inspect_batch(df), update bounded accumulators, then print totals in finish_inspection(). Every row is visited in batches; df/source are unavailable at module scope in this mode. Ordinary scripts receive the whole frame and are only suitable for small inputs. Reuse existing measured profile counts instead of recounting them.",
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
        "Validate and run a cell at the end of the chain in one call. Failed scripts leave the chain unchanged. A separate try_script is optional; an identical preview is reused. Mechanical and semantic changes both apply directly, preserving the source and recording impact.",
        {
            "type": "object",
            "properties": {
                "title": _TEXT,
                "script": _TEXT,
                "plan_step": _TEXT,
                "note": {
                    **_TEXT,
                    "description": "Why this change, the evidence or rule used, and affected row count. Record the chosen interpretation and tradeoff; no user decision is required.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["mechanical", "semantic"],
                    "description": "mechanical: evidence-preserving restructuring or deterministic derivation from supplied facts and declared rules, including complex schema changes. semantic: an evidence-backed judgement about meaning, labels, scope or sampling. Both kinds run directly with recorded impact. Preserve unsupported meaning as unknown. Do not fabricate new examples in scripts. Requested generation uses add_synthetic_rows instead.",
                },
            },
            "required": ["title", "script"],
        },
    ),
    "generate_examples": (
        "Continue the qualified, saved recipe in durable background batches. First configure seed_examples and save a small representative batch with add_synthetic_rows. The platform resumes accepted batches, checks evidence and duplicates, and publishes once. This ends the interactive turn; do not repeatedly submit batches for a large target.",
        {"type": "object", "properties": {}, "additionalProperties": False},
    ),
    "chunk_text": (
        "Group adjacent evidence fragments and split text into bounded chunks. Every output receives a new row identity with all parent row and character-span references. Text is preserved exactly. Choose grouping and size from the source; no generated answers or labels.",
        {
            "type": "object",
            "properties": {
                "text_column": _TEXT,
                "group_by": {"type": "array", "items": _TEXT, "maxItems": 10},
                "max_chars": {"type": "integer", "minimum": 1, "maximum": 100000},
                "plan_step": _TEXT,
            },
            "required": ["text_column", "max_chars", "plan_step"],
        },
    ),
    "seed_examples": (
        "Build examples from source evidence. mode=derive creates a separate output version containing only derived examples; preserve raw source rows in their existing version. Required for documents-to-Q&A and other source-to-examples preparation, with a saved plan_step. target_rows is the output example count and may be smaller than the seed count. Choose a measured coverage-based count when the user has not supplied one; do not ask another question. mode=augment adds explicitly requested synthetic variants to existing examples; target_rows then includes existing rows and must exceed their count. Saved work resumes for the same mode and target; run_id selects it explicitly. Accepted batches survive across turns. Publish only when the target is met, or explicitly report a partial result.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "target_rows": {"type": "integer", "minimum": 1},
                "instruction": _TEXT,
                "run_id": _TEXT,
                "mode": {"type": "string", "enum": ["augment", "derive"], "default": "augment"},
                "plan_step": _TEXT,
            },
            "required": ["target_rows", "instruction"],
        },
    ),
    "add_synthetic_rows": (
        "Validate and save up to 50 pilot examples in the run started by seed_examples. Each row must already fit its consumer: chat training needs messages containing the input context/question and assistant answer; chat evaluation needs input and expected_output. Native decisions retain their typed contract. Derived examples require exact nonempty source quotes in evidence. Attribution is checked, not answer truth. Returns saved, remaining and uncovered-source counts. For a larger target call generate_examples after the pilot; do not loop over batches in chat. Exact batch retries are a no-op. Never invent trace identities.",
        {
            "type": "object",
            "properties": {
                "examples": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 50,
                    "items": {
                        "type": "object",
                        "properties": {
                            "seed_row": {"type": "integer"},
                            "row": {"type": "object", "additionalProperties": True},
                            "evidence": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": {
                                    "type": "object",
                                    "properties": {"column": _TEXT, "quote": _TEXT},
                                    "required": ["column", "quote"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["seed_row", "row"],
                    },
                },
            },
            "required": ["examples"],
        },
    ),
    "record_quality_review": (
        "Execute a read-only audit against the exact version. Return ONLY one boolean-or-null column per named check, preserving df.index and every input row; the runner carries source_row automatically. Example: df = pd.DataFrame({'answer_present': df['answer'].notna()}, index=df.index). Do not return the source's other columns. If explicitly returning source_row, copy df['source_row'] unchanged: sample identities are NOT 0..N-1 or df.index. True=pass, False=fail, null=unmeasured. Counts and results are computed by the server. Use the saved plan's checks, actual predicates for deterministic checks and null for unmeasured claims, never constant passes. Native probability validity is output_schema, never answer_support; preserved publisher labels remain semantically unverified. Repair actionable findings and rerun on the changed version. After success, read status and report only the persisted quality_report; rejected audits do not establish passing checks. Findings never block use; this agent-authored audit is not independent proof.",
        {
            "type": "object",
            "properties": {
                "version": _TEXT,
                "script": _TEXT,
                "checks": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 30,
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": _TEXT,
                            "evidence": _TEXT,
                        },
                        "required": ["name", "evidence"],
                    },
                },
            },
            "required": ["checks", "script"],
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
        "Discard only a pending proposal by its exact UUID from status. Applied, source and generated versions cannot be removed. Never use a version number or omit the ID.",
        {
            "type": "object",
            "properties": {"id": {**_TEXT, "format": "uuid"}},
            "required": ["id"],
        },
    ),
    "set_active": (
        "Choose which ran version consumers read.",
        {"type": "object", "properties": {"version": _TEXT}, "required": ["version"]},
    ),
    "set_intent": (
        "Record the purpose explicitly requested by the user, never inferred from data. Include their exact words as evidence. Fixed once a version was used.",
        {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "enum": ["train", "eval", "explore"]},
                "evidence": {
                    "type": "string",
                    "description": "Exact quote from the user's request explicitly choosing this purpose; not a filename, row, assistant message, or generic instruction to prepare data.",
                },
            },
            "required": ["intent", "evidence"],
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
    "generate_examples": "Continue generation",
    "chunk_text": "Chunk source text",
    "record_preparation_plan": "Save preparation plan",
    "prepare_examples": "Prepare examples",
    "sample_rows": "Sample rows",
    "seed_examples": "Read generation seeds",
    "add_synthetic_rows": "Add synthetic examples",
    "record_quality_review": "Record quality review",
    "check_semantic_quality": "Check semantic quality",
    "status": "Read the chain",
    "query": "Query the frame",
    "diff": "Diff two versions",
    "try_script": "Try a script",
    "inspect": "Inspect the frame",
    "add_cell": "Add a cell",
    "edit_cell": "Edit a cell",
    "remove_cell": "Discard proposal",
    "set_active": "Set the active version",
    "set_intent": "Set the intent",
    "set_capability": "Set the capability",
    "rename": "Rename the dataset",
    "install": "Install a library",
}

TOOL_REASONS = {
    "generate_examples": "Scheduling the saved recipe in bounded batches.",
    "chunk_text": "Preserving text and parent evidence in bounded chunks.",
    "status": "Checking the current version, purpose and capability.",
    "query": "Checking the source data before making changes.",
    "inspect": "Measuring the data and its coverage.",
    "try_script": "Checking the proposed transformation without changing the dataset.",
    "seed_examples": "Reading source examples and setting the generation target.",
    "add_synthetic_rows": "Checking format, capability, seed identities and duplicates before adding the batch.",
    "record_quality_review": "Saving measured quality checks for this version.",
    "check_semantic_quality": "Checking rows against source evidence.",
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
        tools.report_progress(
            "validating" if name == "add_synthetic_rows" else "working",
            TOOL_TITLES.get(name, name),
            TOOL_REASONS.get(name, "Updating the dataset through its notebook tools."),
        )
        started = time.monotonic()
        try:
            schema = {**TOOL_SPECS[name][1], "additionalProperties": False}
            violations = sorted(
                Draft202012Validator(schema).iter_errors(args), key=lambda e: str(e.path)
            )
            if violations:
                result = {
                    "ok": False,
                    "error": violations[0].message[:600],
                    "failure": {
                        "code": "invalid_arguments",
                        "category": "input",
                        "action": "Correct the named argument using this tool's schema.",
                    },
                }
            else:
                result = _safe(fn(args, ctx))
        except ChatGPTError:
            raise
        except lifecycle.DatasetError as exc:
            result = {
                "ok": False,
                "error": exc.detail,
                "failure": {
                    "code": exc.code,
                    "category": "state",
                    "action": "Read status and select a valid action on the current version.",
                },
            }
        except generation.EvidenceValidationError as exc:
            result = exc.result()
        except ValueError as exc:
            result = {
                "ok": False,
                "error": str(exc)[:600],
                "failure": {"code": "incompatible_input", "category": "input"},
            }
        except Exception:  # noqa: BLE001
            logger.warning("notebook tool %s failed", name, exc_info=True)
            result = {
                "ok": False,
                "error": "The operation failed internally. Saved inputs and completed work are preserved.",
                "failure": {
                    "code": "internal_fault",
                    "category": "internal",
                    "action": "Inspect the saved failure; do not repeat an unresolved provider submission.",
                },
            }
        ok = not isinstance(result, dict) or (
            result.get("ok") is not False and not result.get("error")
        )
        source = _dataset(tools.dataset_id).active_cell
        result, stopped = workflow.record(
            tools.workflow_id,
            tool=name,
            arguments=args,
            result=result if isinstance(result, dict) else {"value": result},
            source_fingerprint=source.fingerprint if source else "",
        )
        tools.stop_requested = tools.stop_requested or stopped
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
        if not ok:
            tools.report_progress(
                "working",
                "A tool call was rejected",
                str(result.get("error", "Check the activity details.")),
            )
        elif tools.generation:
            tools.report_progress(
                "generating", "Generating examples", tools.generation["instruction"]
            )
        else:
            tools.report_progress(
                "working", "Preparing the next step", "The agent is reviewing the tool result."
            )
        tools.think()
        return result

    def serial_call(args: dict[str, Any], ctx: Any = None) -> Any:
        with tools.lock:
            if tools.stop_requested and name not in workflow.READ_ONLY:
                return {
                    "ok": False,
                    "error": "This workflow stopped after repeated failures. Saved results are preserved.",
                    "failure": workflow.current(tools.dataset_id).failure,
                }
            if tools.operation_id is not None:
                operations.check_cancelled(tools.dataset_id, task_id=tools.operation_id)
            if _dataset(tools.dataset_id).intent == Dataset.Intent.PENDING and name not in {
                "status",
                "query",
                "inspect",
                "diff",
                "set_intent",
            }:
                return {
                    "ok": False,
                    "error": "Choose the user's explicit intent before preparing data. Read-only exploration is available; ask for the intended use before making changes.",
                }
            return call(args, ctx)

    return serial_call


@transaction.atomic
def save_turn(dataset_id: Any, turn_id: str, changes: dict[str, Any]) -> None:
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    chat = list(dataset.chat or [])
    for index in range(len(chat) - 1, -1, -1):
        if chat[index].get("id") == turn_id:
            chat[index] = {**chat[index], **changes}
            Dataset.objects.filter(pk=dataset_id).update(chat=chat, updated_at=timezone.now())
            break


def system_prompt(dataset: Dataset) -> str:
    system = prompts.system(
        dataset.intent,
        capability=prompts.capability_section(dataset),
        libraries=libraries.describe(paths.library_cache(dataset.project_id)),
        sample=prompts.context_section(workshop_context(dataset)),
    )
    return (
        system
        + prompts.EXECUTION
        + "\n\n## Original user request\n"
        + json.dumps(dataset.brief)
        + "\n"
        + prompts.DOCUMENTS
    )


def complete_request(dataset, message, tools, engine, pending):
    outcome = engines.Outcome()
    inspected = set()
    for _ in range(3):
        current = yield from engine.run(dataset, message, tools, pending)
        for key, value in current.stats.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                outcome.stats[key] = outcome.stats.get(key, 0) + value
            elif isinstance(value, list):
                outcome.stats[key] = [*outcome.stats.get(key, []), *value]
            else:
                outcome.stats[key] = value
        outcome.text, outcome.error = current.text, current.error
        if outcome.error or tools.stop_requested or not (tools.automatic or tools.preparation_turn):
            break
        dataset = _dataset(dataset.pk)
        result = workflow.finish(tools.workflow_id)
        missing = result["missing_checks"]
        if not missing or result["execution"] in {
            "blocked",
            "cancelled",
            "paused",
            "queued",
            "running",
        }:
            break
        signature = (dataset.active_cell.fingerprint if dataset.active_cell else "", tuple(missing))
        if signature in inspected:
            break
        inspected.add(signature)
        message = (
            "The saved task is not finished. Required checks are missing on the active version: "
            + json.dumps(missing)
            + ". Finish any shaping before auditing this exact version. Complete supported checks within the saved audit budget; do not regenerate, change targets, invent passing checks or ask the user to continue. If evidence or budget is insufficient, report the partial outcome and why."
        )
    return outcome


def iter_turn(
    dataset_id: Any,
    message: str,
    *,
    display: str,
    user: Any = None,
    turn_key: str = "",
    automatic: bool = False,
    preparation_turn: bool = False,
) -> Iterator[dict[str, Any]]:
    dataset = _dataset(dataset_id)
    started = timezone.now().isoformat()
    # The turn task is acks_late; a redelivery must not land every cell twice.
    if turn_key and (
        dataset.agent_turn_key == turn_key
        or any(item.get("turn_key") == turn_key for item in dataset.chat or [])
    ):
        logger.warning("dataset %s: turn %s already ran, skipping the retry", dataset_id, turn_key)
        for item in dataset.chat or []:
            if item.get("turn_key") == turn_key and item.get("status") == "running":
                error = (
                    "The worker stopped before this turn completed. Completed cells are preserved."
                )
                closed = {
                    **item,
                    "status": "error",
                    "error": error,
                    "progress": {
                        "stage": "error",
                        "label": "Preparation interrupted",
                        "detail": error,
                    },
                }
                save_turn(dataset_id, item["id"], closed)
                yield _emit(dataset_id, {"type": "chat_turn", **closed})
        return

    unfinished = [
        {"title": cell.title, "script": cell.script, "note": cell.note}
        for cell in dataset.cells.filter(state=Cell.State.PROPOSED).order_by("position")
    ]
    if unfinished:
        message += (
            "\n\nUnfinished preparation suggestions from earlier turns (reference only). "
            "Reassess these against the request and current data, then apply supported changes "
            "sequentially with add_cell. Do not ask for approval or blindly reuse saved outputs.\n"
            + json.dumps(unfinished, ensure_ascii=False)
        )
    operation_id = turn_key or str(uuid.uuid4())
    operations.started(dataset_id, operation_id)
    retire_outdated(dataset)
    turn_user = {"role": "user", "text": display, "at": started, "context": message}
    turn_id = str(uuid.uuid4())
    draft = {
        "id": turn_id,
        "role": "agent",
        "status": "running",
        "text": "",
        "at": started,
        "turn_key": turn_key,
    }
    Dataset.objects.filter(pk=dataset_id).update(
        chat=[*(dataset.chat or []), turn_user, draft], agent_turn_key=operation_id
    )
    dataset.agent_turn_key = operation_id
    yield _emit(dataset_id, {"type": "chat_turn", **turn_user})

    pending: list[dict[str, Any]] = []
    existing_workflow = workflow.current(dataset_id)
    if existing_workflow is None or existing_workflow.state not in {"queued", "running", "paused"}:
        workflow.start(dataset, request=display, user=user, owner=operation_id)
    tools = Tools(dataset_id, user, lambda event: pending.append(_emit(dataset_id, event)))
    tools.operation_id = operation_id
    tools.automatic = automatic
    tools.preparation_turn = preparation_turn
    tools.turn_id = turn_id
    tools.user_request = "\n".join(
        part for part in (dataset.brief, display if display != PREPARE_DISPLAY else "") if part
    )
    tools.report_progress(
        "working",
        "Reading your request",
        "Checking the dataset and deciding the next step.",
        started_at=started,
    )
    engine = engines.select(user)
    tools.chatgpt_session = getattr(engine, "session", None)
    outcome = engines.Outcome()
    turn_started = time.monotonic()
    tools.think()
    if engine is None:
        outcome.error = engines.NOT_CONFIGURED
        operations.finished(dataset_id, task_id=operation_id)
    else:
        try:
            outcome = yield from complete_request(dataset, message, tools, engine, pending)
        except Exception as exc:  # noqa: BLE001 — the turn must land on the page either way
            logger.warning("dataset %s: agent turn failed", dataset_id, exc_info=True)
            outcome.error = engine.describe_error(exc)
            outcome.stats = getattr(engine, "usage", outcome.stats)
        finally:
            operations.finished(dataset_id, task_id=operation_id)
    tools.stop_thinking()
    retire_outdated(_dataset(dataset_id))
    awaiting_intent = _dataset(dataset_id).intent == Dataset.Intent.PENDING
    generated = tools.progress.get("generated_rows", 0)
    saved_generation = generation.get(tools.generation["id"]) if tools.generation else None
    queued_generation = saved_generation is not None and saved_generation.state == "queued"
    if awaiting_intent and (not outcome.error or engine is None):
        outcome.text = INTENT_QUESTION
        outcome.error = ""
        tools.report_progress(
            "awaiting_intent", "Awaiting intent", "Choose Training, Eval, or Data exploration."
        )
    elif queued_generation:
        outcome.text = "Generation continues in saved batches."
        tools.report_progress("queued", "Generation queued", "Accepted batches are saved.")
    elif tools.generation:
        requested = tools.progress["target_rows"] - tools.progress["rows_before"]
        if generated < requested and not outcome.error:
            outcome.error = (
                f"Generation stopped after saving {generated} of {requested} requested rows."
            )
        published = saved_generation.output_id is not None
        saved_result = (
            f"{generated} generated rows added to the dataset."
            if published
            else f"{generated} generated rows saved in unpublished batches. The active dataset is unchanged."
        )
        tools.report_progress(
            "partial" if outcome.error else "complete",
            "Generation incomplete" if outcome.error else "Generation complete",
            saved_result,
            published_rows=generated if published else 0,
            publication="published" if published else "pending",
        )
        outcome.text = "\n\n".join(part for part in (outcome.text.strip(), saved_result) if part)
    else:
        tools.report_progress(
            "error" if outcome.error else "complete",
            "Request stopped" if outcome.error else "Complete",
            outcome.error or "The agent has finished this request.",
        )
    result = workflow.finish(
        tools.workflow_id,
        error=outcome.error,
        require_prepared=automatic or preparation_turn or bool(tools.generation),
    )
    if (
        not awaiting_intent
        and not queued_generation
        and result["execution"] in {"blocked", "partial"}
        and not outcome.error
    ):
        saved_workflow = workflow.current(dataset_id)
        failure_detail = (saved_workflow.failure or {}).get("detail", "")
        outcome.error = (
            f"Preparation stopped: {failure_detail}"
            if failure_detail
            else "The requested preparation is incomplete. Inspect the saved workflow outcome."
        )
        tools.report_progress(result["execution"], "Preparation incomplete", outcome.error)
    while pending:
        yield pending.pop(0)

    text = outcome.text if outcome.text else tools.text
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
        "preparation_turn": preparation_turn or automatic,
        "funding_source": "chatgpt" if engine and engine.name == "chatgpt" else "platform",
        "model": outcome.stats.get("served_model", "") if engine else "",
        "status": "error"
        if outcome.error
        else "awaiting_intent"
        if awaiting_intent
        else "complete",
        "progress": tools.progress,
    }
    for ref in tools.touched:
        cell = Cell.objects.filter(dataset_id=dataset_id, pk=ref["id"]).first()
        if (
            cell is not None
            and cell.review.get("kind") == "synthetic"
            and cell.review.get("generation_id") == (tools.generation or {}).get("id")
        ):
            cell.review.update(
                generator={"engine": turn_agent["engine"], "model": turn_agent["model"]}
            )
            cell.save(update_fields=["review"])
    dataset = Dataset.objects.get(pk=dataset_id)
    save_turn(dataset_id, turn_id, turn_agent)
    _bill(dataset, user, outcome.stats, turn_agent, started)
    yield _emit(dataset_id, {"type": "chat_turn", "id": turn_id, **turn_agent})


def _bill(
    dataset: Dataset, user: Any, stats: dict[str, Any], turn: dict[str, Any], started: str
) -> None:
    if not getattr(user, "pk", None):
        return
    try:
        record_workshop_usage(
            user,
            stats,
            funding_source=turn.get("funding_source", "platform"),
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
    generated = (
        dataset.workshop_runs.filter(kind="generation", state="queued")
        .order_by("-created_at")
        .first()
    )
    if generated:
        # Dispatch after the interactive operation releases its provider ownership.
        from overbae.services.datasets.generation_worker import schedule

        schedule(generated.pk)
        return
    if dataset.operation.get("state") == "cancel_pending":
        operations.reconcile(dataset_id, local_stopped=True)
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
        dataset = _dataset(dataset_id)
        yield from iter_turn(
            dataset_id,
            f"{prompts.PREPARE}\n\nUser request: {dataset.brief}"
            if dataset.brief
            else prompts.PREPARE,
            display=dataset.brief or PREPARE_DISPLAY,
            automatic=not bool(dataset.brief),
            preparation_turn=True,
            user=user,
            turn_key=turn_key,
        )
    finally:
        settle(dataset_id)


def follow_up(
    dataset_id: Any,
    message: str,
    *,
    user: Any = None,
    turn_key: str = "",
    display: str | None = None,
    preparation_turn: bool = False,
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
            display=display if display is not None else message.strip(),
            preparation_turn=preparation_turn,
            user=user,
            turn_key=turn_key,
        )
    finally:
        settle(dataset_id)


def transcript(dataset: Dataset) -> str:
    return json.dumps(dataset.chat or [], indent=1, default=str)
