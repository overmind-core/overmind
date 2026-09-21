from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from threading import RLock
from typing import Any

from django.db import close_old_connections, transaction
from django.utils import timezone
from pydantic import ValidationError

from overbae.models import Capability, Cell, Dataset
from overbae.services.datasets import diff as diff_svc
from overbae.services.datasets import lifecycle, paths, review, semantic_checks, store, synthetic
from overbae.services.datasets.context import context_fingerprint, workshop_context
from overbae.services.datasets.notebook import engines, events, libraries, prompts
from overbae.services.datasets.notebook import run as run_svc

logger = logging.getLogger(__name__)

QUERY_ROWS = 50
THOUGHT_CHARS = 16_000
_SCRIPT_CHARS = 4000
_PREVIEW_ROWS = 3
_VALUE_CHARS = 400
PREPARE_DISPLAY = "Prepare this dataset for its purpose and capability, then check its quality."


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
        "intent": dataset.intent,
        "capability": dataset.capability.name if dataset.capability_id else None,
        "capability_rank": dataset.capability_rank[:3],
        "state": dataset.state,
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


class Tools:
    def __init__(self, dataset_id: Any, user: Any, emit: Callable[[dict[str, Any]], None]):
        self.dataset_id = dataset_id
        self.user = user
        self.emit = emit
        self.touched: list[dict[str, Any]] = []
        self.steps: list[dict[str, Any]] = []
        self._thinking: dict[str, Any] | None = None
        self.automatic = False
        self.lock = RLock()
        self.turn_id = ""
        self.progress: dict[str, Any] = {}
        self.generation: dict[str, Any] | None = None
        self.last_thought_save = 0.0
        self.text = ""
        self.text_offset = 0
        self.step_offsets: dict[str, int] = {}
        self.response_break = False
        self.preview = None

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
        part["text_offset"] = self.step_offsets.setdefault(part["id"], self.text_offset)
        self.response_break = True
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
        return status(_dataset(self.dataset_id))

    def query(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        cell = resolve_cell(dataset, args.get("version"), ran_only=True)
        try:
            result = store.query(
                str(args.get("sql") or ""), limit=QUERY_ROWS, t=paths.cell_path(dataset.id, cell.id)
            )
        except Exception as exc:  # noqa: BLE001 — DuckDB raises many types; the message is the value
            return {"ok": False, "error": str(exc)[-600:]}
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
                return {"ok": False, "error": "Nothing before that version."}
        out = diff_svc.between(paths.cell_path(dataset.id, a.id), paths.cell_path(dataset.id, b.id))
        for key in ("changed_examples", "removed_examples"):
            if key in out:
                out[key] = _visible(out[key])
        return {"from": dataset.versions().get(a.id), "to": dataset.versions().get(b.id), **out}

    def try_script(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        after = resolve_cell(dataset, args.get("after"), ran_only=True)
        result = run_svc.try_script(dataset, str(args.get("script") or ""), after=after)
        self.preview = (after.id, after.fingerprint, str(args.get("script") or ""), result)
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
        previous = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        if previous is None or not previous.ran:
            return {"ok": False, "error": "Run or fix the existing chain first."}
        script = str(args.get("script") or "")
        for pending in dataset.cells.filter(state=Cell.State.PROPOSED, script=script):
            report = pending.review
            if (
                report.get("input_fingerprint") == previous.fingerprint
                and report.get("context_fingerprint") == context_fingerprint(dataset.capability)
                and report.get("intent") == dataset.intent
                and paths.cell_path(dataset.id, pending.id).exists()
                and store.file_sha256(paths.cell_path(dataset.id, pending.id))
                == report.get("output_fingerprint")
            ):
                self._touch(pending, "proposed")
                return {
                    "ok": True,
                    "proposed": True,
                    "reused": True,
                    "id": str(pending.id),
                    "title": pending.title,
                    "review": review.summary(report),
                }
        key = (previous.id, previous.fingerprint, script)
        result = (
            self.preview[3]
            if self.preview and self.preview[:3] == key
            else run_svc.try_script(dataset, script, after=previous)
        )
        self.preview = None
        if result.frame is None:
            return {"ok": False, "error": result.error}
        before = store.read_frame(paths.cell_path(dataset.id, previous.id))
        if review.same_frame(before, result.frame):
            return {
                "ok": True,
                "unchanged": True,
                "proposed": False,
                **_cell_line(dataset, previous, dataset.versions()),
            }
        changes = review.impact(before, result.frame)
        if self.generation and (changes["rows_added"] or len(result.frame) > len(before)):
            return {
                "ok": False,
                "error": "Generate new examples with add_synthetic_rows. Do not expand the dataset by copying rows or remapping identifiers in a script.",
            }
        kind = args.get("kind", "mechanical")
        if kind not in {"mechanical", "semantic"}:
            return {"ok": False, "error": "kind must be mechanical or semantic."}
        if changes["instruction_changes"]:
            kind = "semantic"
        if kind == "semantic" or review.requires_approval(changes, allow_exclusions=self.automatic):
            run = False
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
        report = review.save_proposal(
            dataset, cell, previous, result.frame, kind=kind, note=str(args.get("note") or "")
        )
        if not run:
            return {
                "ok": True,
                "proposed": True,
                "id": str(cell.id),
                "title": cell.title,
                "review": review.summary(report),
            }
        cell.review = {
            **report,
            "status": "accepted",
            "approval": "preparation" if self.automatic else "mechanical",
        }
        cell.save(update_fields=["review"])
        return self._run(dataset, cell)

    def prepare_examples(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        if dataset.intent not in {"train", "eval"}:
            return {"ok": False, "error": "Set the dataset purpose before preparing examples."}
        return self.add_cell(
            {
                "title": "Prepare evaluation examples"
                if dataset.intent == "eval"
                else "Prepare training examples",
                "script": f"df = prepare_examples(df, intent={dataset.intent!r})",
                "note": "Preserve task instructions, evidence and tools; separate the target for evaluation.",
            }
        )

    def seed_examples(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        if self.automatic:
            return {"ok": False, "error": "Synthetic generation requires a user request."}
        dataset = _dataset(self.dataset_id)
        target = args.get("target_rows")
        instruction = str(args.get("instruction") or "").strip()[:4000]
        if isinstance(target, bool) or not isinstance(target, int):
            return {"ok": False, "error": "Set target_rows to the requested final dataset size."}
        if not instruction:
            return {"ok": False, "error": "Describe the requested examples and intended coverage."}
        tail = dataset.cells.exclude(state=Cell.State.PROPOSED).order_by("-position").first()
        if tail is None or not tail.ran:
            return {"ok": False, "error": "Run or fix the existing chain first."}
        cell_id = args.get("cell_id")
        generated_cell = dataset.cells.filter(pk=cell_id).first() if cell_id else None
        if cell_id and (generated_cell is None or not generated_cell.review.get("generation_id")):
            return {"ok": False, "error": "No saved generation with that cell id."}
        if (
            not cell_id
            and tail.review.get("target_rows") == target
            and tail.review.get("generation_id")
        ):
            generated_cell = tail
        if self.generation and (
            self.generation["target_rows"] != target
            or (generated_cell and self.generation["id"] != generated_cell.review["generation_id"])
        ):
            return {"ok": False, "error": "Keep the generation and target fixed for this request."}
        cell = tail
        if generated_cell is not None:
            cell = dataset.cells.filter(pk=generated_cell.review.get("source_cell")).first()
            if cell is None:
                return {"ok": False, "error": "The generation source is no longer available."}
            try:
                synthetic.validate_generation(dataset, generated_cell, cell, target)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            instruction = generated_cell.review["instruction"]
            self._touch(generated_cell, "ran")
        frame = store.read_frame(paths.cell_path(dataset.id, cell.id))
        if target <= len(frame):
            return {"ok": False, "error": "Set target_rows to the requested final dataset size."}
        self.generation = self.generation or {
            "id": generated_cell.review["generation_id"] if generated_cell else str(uuid.uuid4()),
            "target_rows": target,
            "instruction": instruction,
            "source_cell": str(cell.id),
            "source_fingerprint": cell.fingerprint,
        }
        generated = (
            generated_cell.review.get("generated_rows", 0)
            if generated_cell
            else self.progress.get("generated_rows", 0)
        )
        self.report_progress(
            "generating",
            "Generating examples",
            instruction,
            rows_before=len(frame),
            target_rows=target,
            generated_rows=generated,
            **({"cell_id": str(generated_cell.id)} if generated_cell else {}),
        )
        count = min(max(int(args.get("limit", 3)), 1), 10)
        sample = frame.sample(n=min(len(frame), count), random_state=42)
        return {
            "version": dataset.versions().get(cell.id),
            "rows_before": len(frame),
            "target_rows": target,
            "generated_rows": generated,
            "remaining_rows": target - len(frame) - generated,
            "examples": [
                {
                    "seed_row": int(row[store.SOURCE_ROW]),
                    "row": {
                        k: v
                        for k, v in row.items()
                        if k not in {store.SOURCE_ROW, review.PROVENANCE_COLUMN}
                    },
                }
                for row in sample.to_dict(orient="records")
            ],
        }

    def add_synthetic_rows(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        if self.automatic:
            return {"ok": False, "error": "Synthetic generation requires a user request."}
        if self.generation is None:
            return {
                "ok": False,
                "error": "Call seed_examples with target_rows and instruction first.",
            }
        dataset = _dataset(self.dataset_id)
        previous = dataset.cells.filter(pk=self.generation["source_cell"]).first()
        if previous is None or not previous.ran:
            return {"ok": False, "error": "Run or fix the existing chain first."}
        if (
            str(previous.id) != self.generation["source_cell"]
            or previous.fingerprint != self.generation["source_fingerprint"]
        ):
            return {
                "ok": False,
                "error": "The source changed during generation. Start a new request.",
            }
        cell = synthetic.add(
            dataset,
            previous,
            args.get("examples"),
            instruction=self.generation["instruction"],
            generation_id=self.generation["id"],
            target_rows=self.generation["target_rows"],
            user=self.user,
        )
        self._touch(cell, "ran")
        self.report_progress(
            "generating",
            "Generating examples",
            self.generation["instruction"],
            generated_rows=cell.review["generated_rows"],
            cell_id=str(cell.id),
        )
        self.emit({"type": "cells_changed"})
        return {
            "ok": True,
            "id": str(cell.id),
            "version": dataset.versions().get(cell.id),
            "generated_rows": cell.review["generated_rows"],
            "rows_after": cell.review["rows_after"],
            "remaining_rows": self.generation["target_rows"] - cell.review["rows_after"],
        }

    def record_quality_review(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        cell = resolve_cell(dataset, args.get("version"), ran_only=True)
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
            cell = resolve_cell(dataset, request.version, ran_only=True)
            result = semantic_checks.run_checks(dataset, cell, request, user=self.user)
        except (ValueError, ValidationError) as exc:
            return {"ok": False, "error": str(exc)}
        self.emit({"type": "cells_changed"})
        return {"ok": True, **result, "readiness": review.readiness(dataset, cell)}

    def edit_cell(self, args: dict[str, Any], _ctx: Any = None) -> dict[str, Any]:
        dataset = _dataset(self.dataset_id)
        try:
            cell = resolve_cell(dataset, args.get("version") or args.get("id"))
            if args.get("script") is not None:
                previous = (
                    dataset.cells.filter(position__lt=cell.position, state=Cell.State.OK)
                    .order_by("-position")
                    .first()
                )
                if previous is None:
                    return {"ok": False, "error": "The source cannot be edited."}
                result = run_svc.try_script(dataset, str(args["script"]), after=previous)
                if result.frame is None:
                    return {"ok": False, "error": result.error}
                changes = review.impact(
                    store.read_frame(paths.cell_path(dataset.id, previous.id)), result.frame
                )
                if args.get("kind") == "semantic" or review.requires_approval(changes):
                    return {
                        "ok": False,
                        "error": "Exclusions, evidence removal, instruction changes and semantic changes need a new proposal. Use add_cell with kind=semantic.",
                    }
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
    "check_semantic_quality": (
        "Measure semantic row quality against named evidence and answer columns using Jev with a generative fallback. Never uses answers as their own evidence. Unknowns stay null; findings are advisory and do not authorize edits. Processes at most 200 unmeasured rows per call; repeat identical checks while remaining_rows is nonzero. Changing the version, task context, or checks starts a new audit. Use record_quality_review for deterministic format/schema checks.",
        semantic_checks.SemanticReviewRequest.model_json_schema(),
    ),
    "prepare_examples": (
        "Prepare conversations for the selected purpose without losing instructions, evidence or tools. Eval separates the final answer into expected_output; train retains the full transcript. Does not change task scope or invent missing evidence. Run before projecting columns.",
        {"type": "object", "properties": {}},
    ),
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
        "Validate and run a cell at the end of the chain in one call. Failed scripts leave the chain unchanged. A separate try_script is optional; an identical preview is reused. run=false leaves a proposal.",
        {
            "type": "object",
            "properties": {
                "title": _TEXT,
                "script": _TEXT,
                "note": {
                    **_TEXT,
                    "description": "Why this change, the evidence or rule used, and affected row count. For a proposal, state the decision the user is approving and its tradeoff.",
                },
                "run": {"type": "boolean"},
                "kind": {
                    "type": "string",
                    "enum": ["mechanical", "semantic"],
                    "description": "mechanical: evidence-preserving restructuring or deterministic derivation from supplied facts and declared rules, including complex schema changes. semantic: a judgement about meaning, labels, scope or sampling; always a reviewed proposal, including initial preparation. Initial measured cleaning can run directly; follow-up exclusions and script-based row additions require review. Requested generation uses add_synthetic_rows instead.",
                },
            },
            "required": ["title", "script"],
        },
    ),
    "seed_examples": (
        "Start explicitly requested generation: set the final target_rows and coverage instruction, then read complete seed examples. An unfinished generation at the chain tail resumes automatically for the same target; cell_id selects a saved generation explicitly. Never generate during automatic preparation.",
        {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                "target_rows": {"type": "integer", "minimum": 1},
                "instruction": _TEXT,
                "cell_id": _TEXT,
            },
            "required": ["target_rows", "instruction"],
        },
    ),
    "add_synthetic_rows": (
        "Validate and add up to 50 rows directly to this request's generated version, making it active. Returns saved and remaining counts. Call seed_examples first. Repeat until remaining_rows is zero; exact batch retries are a no-op. Never invent trace identities. No approval step is needed.",
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
                        },
                        "required": ["seed_row", "row"],
                    },
                },
            },
            "required": ["examples"],
        },
    ),
    "record_quality_review": (
        "Execute a read-only audit against the exact version. The script must leave df with source_row and one boolean-or-null column per named check, one result per original row. True=pass, False=fail, null=unmeasured. Counts and results are computed by the server, not supplied by you. Required checks: task_alignment, input_evidence, answer_support, output_schema. Use actual predicates, not constant passes. Use null for semantic claims you cannot verify. Repair actionable findings and rerun on the changed version. Findings never block use; this agent-authored audit is not independent proof.",
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
    "prepare_examples": "Prepare examples",
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
        # The Cursor SDK runs each call on a fresh thread; no request_finished reaches it.
        close_old_connections()
        return result

    def serial_call(args: dict[str, Any], ctx: Any = None) -> Any:
        with tools.lock:
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
    return prompts.system(
        dataset.intent,
        capability=prompts.capability_section(dataset),
        libraries=libraries.describe(paths.library_cache(dataset.project_id)),
        sample=prompts.context_section(workshop_context(dataset)),
    )


def iter_turn(
    dataset_id: Any,
    message: str,
    *,
    display: str,
    user: Any = None,
    turn_key: str = "",
    automatic: bool = False,
) -> Iterator[dict[str, Any]]:
    dataset = _dataset(dataset_id)
    started = timezone.now().isoformat()
    # The turn task is acks_late; a redelivery must not land every cell twice.
    if turn_key and (
        dataset.agent_turn_key == turn_key
        or any(item.get("turn_key") == turn_key for item in dataset.chat or [])
    ):
        logger.warning("dataset %s: turn %s already ran, skipping the retry", dataset_id, turn_key)
        return

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
        chat=[*(dataset.chat or []), turn_user, draft], agent_turn_key=turn_key or ""
    )
    yield _emit(dataset_id, {"type": "chat_turn", **turn_user})

    pending: list[dict[str, Any]] = []
    tools = Tools(dataset_id, user, lambda event: pending.append(_emit(dataset_id, event)))
    tools.automatic = automatic
    tools.turn_id = turn_id
    tools.report_progress(
        "working",
        "Reading your request",
        "Checking the dataset and deciding the next step.",
        started_at=started,
    )
    engine = engines.select()
    outcome = engines.Outcome()
    turn_started = time.monotonic()
    tools.think()
    if engine is None:
        outcome.error = engines.NOT_CONFIGURED
    else:
        try:
            if automatic and dataset.intent in {"train", "eval"}:
                tools.handlers()["prepare_examples"]({})
                while pending:
                    yield pending.pop(0)
                dataset = _dataset(dataset_id)
            outcome = yield from engine.run(dataset, message, tools, pending)
        except Exception as exc:  # noqa: BLE001 — the turn must land on the page either way
            logger.warning("dataset %s: agent turn failed", dataset_id, exc_info=True)
            outcome.error = engine.describe_error(exc)
    tools.stop_thinking()
    generated = tools.progress.get("generated_rows", 0)
    awaiting_approval = Cell.objects.filter(
        dataset_id=dataset_id,
        pk__in=[ref["id"] for ref in tools.touched],
        state=Cell.State.PROPOSED,
    ).exists()
    if awaiting_approval and not outcome.error:
        tools.report_progress(
            "awaiting_approval", "Awaiting approval", "Choose Approve or Deny to continue."
        )
    elif tools.generation:
        requested = tools.progress["target_rows"] - tools.progress["rows_before"]
        if generated < requested and not outcome.error:
            outcome.error = (
                f"Generation stopped after saving {generated} of {requested} requested rows."
            )
        tools.report_progress(
            "partial" if outcome.error else "complete",
            "Generation incomplete" if outcome.error else "Generation complete",
            "Generated rows have been added to the dataset."
            if generated
            else "No generated rows were saved.",
        )
        saved_result = (
            f"{generated} generated rows added to the dataset."
            if generated
            else "No generated rows were added. The active dataset is unchanged."
        )
        outcome.text = "\n\n".join(part for part in (outcome.text.strip(), saved_result) if part)
    else:
        tools.report_progress(
            "error" if outcome.error else "complete",
            "Request stopped" if outcome.error else "Complete",
            outcome.error or "The agent has finished this request.",
        )
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
        "model": outcome.stats.get("served_model", "") if engine else "",
        "status": "error"
        if outcome.error
        else "awaiting_approval"
        if awaiting_approval
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
            automatic=True,
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
            user=user,
            turn_key=turn_key,
        )
    finally:
        settle(dataset_id)


def transcript(dataset: Dataset) -> str:
    return json.dumps(dataset.chat or [], indent=1, default=str)
