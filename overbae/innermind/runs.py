"""An evaluation run's lifecycle: create, launch, generate, score, aggregate, finish."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import timedelta
from itertools import batched
from statistics import mean
from typing import Any

from celery import current_app
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from overbae.core import async_loop
from overbae.innermind import classes, comparison, dataset, generation
from overbae.innermind.metrics import FAIL_BELOW
from overbae.models import EvalMatrix, EvalRun, EvalRunRow, EvalSet, FinetuningJob
from overbae.services.activity import update_task
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.alignment import system_prompt
from overbae.services.datasets.sampling import select_rows

logger = logging.getLogger(__name__)

ROW_BATCH = 500
# A running run whose rows have not moved for this long is failed.
STALL = timedelta(minutes=30)
# Scoring writes nothing until it ends; it matches the scoring task's hard limit.
SCORING_LIMIT = timedelta(hours=2)


class RunError(ValueError):
    pass


def freeze_matrix(*, eval_set: EvalSet | None = None, capability=None, project=None) -> list[dict]:
    """The set's enabled rows, else the enabled rows that apply to the capability."""
    if eval_set is not None:
        return eval_set.matrix_snapshot()
    project = project or (capability.project if capability is not None else None)
    if project is None:
        return []
    rows = EvalMatrix.objects.filter(project=project, enabled=True)
    rows = rows.filter(Q(capability__isnull=True) | Q(capability=capability))
    return [row.snapshot() for row in rows.order_by("name")]


def create_run(*, cell, mode: str, matrix: list[dict], **fields: Any) -> EvalRun:
    if cell is None:
        raise RunError("A run needs a pinned dataset version.")
    if not matrix:
        raise RunError("A run needs at least one enabled eval matrix row.")
    if mode == EvalRun.Mode.GENERATE:
        from overbae.services.datasets.contract import evaluation_generation_error

        if reason := evaluation_generation_error(cell.intent_report):
            raise RunError(reason)
    if mode == EvalRun.Mode.GENERATE and not (fields.get("model_ref") or fields.get("model_name")):
        raise RunError("A generate run needs a model.")
    fields.setdefault("dataset", cell.dataset)
    params = dict(fields.pop("params", None) or {})
    capability = fields["dataset"].capability if fields["dataset"] else None
    if mode == EvalRun.Mode.GENERATE and "system_prompt" not in params and capability:
        params["system_prompt"] = system_prompt(capability)
    return EvalRun.objects.create(cell=cell, mode=mode, matrix=matrix, params=params, **fields)


def provide_outputs(run: EvalRun, outputs: dict[int, tuple[Any, str]]) -> None:
    """Posts a ``provided`` run's outputs, ``{row_index: (output, source_trace_id)}``.
    A missing output stays visible as a degraded row."""
    now = timezone.now()
    EvalRunRow.objects.bulk_create(
        [
            EvalRunRow(
                run=run,
                row_index=index,
                source_trace_id=trace_id or "",
                output=output,
                degraded=output is None,
                degraded_reason="no_output: the caller provided no output"
                if output is None
                else "",
                state=EvalRunRow.State.DONE,
                finished_at=now,
            )
            for index, (output, trace_id) in sorted(outputs.items())
        ],
        batch_size=ROW_BATCH,
    )


def launch(run: EvalRun) -> None:
    # tasks.innermind imports this module.
    from overbae.tasks.innermind import run_eval_run  # noqa: PLC0415

    def enqueue() -> None:
        try:
            result = run_eval_run.delay(eval_run_id=str(run.pk))
        except Exception:
            logger.exception("Queueing run %s failed", run.pk)
            fail(run.pk, "Could not queue the run. Re-launch it.")
            return
        EvalRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)

    # The worker must see the committed row, or it exits "not found" and orphans the run.
    transaction.on_commit(enqueue)


def relaunch(run: EvalRun) -> bool:
    """Re-runs a terminal run from its frozen inputs; a provided run keeps its outputs."""
    if run.mode == EvalRun.Mode.PROVIDED:
        raise RunError("A run with provided outputs cannot be re-launched.")
    with transaction.atomic():
        if not EvalRun.objects.filter(pk=run.pk, status__in=EvalRun.TERMINAL).update(
            status=EvalRun.Status.PENDING,
            summary={},
            error="",
            completed_at=None,
            scoring_task_id="",
            scoring_started_at=None,
            updated_at=timezone.now(),
        ):
            return False
        launch(run)
    return True


def selected_rows(run: EvalRun) -> list[row_store.DatasetRow]:
    fraction = run.sampling if 0 < run.sampling <= 1 else 1.0
    return select_rows(run.cell, limit=run.max_items or None, fraction=fraction)


def seed_rows(run: EvalRun) -> int:
    """Creates the run's rows: recorded outputs are done at once, generated ones wait
    for admission, and provided ones already exist."""
    if run.mode == EvalRun.Mode.PROVIDED:
        return run.rows.count()
    run.rows.all().delete()
    run.scores.all().delete()
    now = timezone.now()
    created = 0
    for chunk in batched(selected_rows(run), ROW_BATCH, strict=False):
        rows = []
        for row in chunk:
            entry = EvalRunRow(run=run, row_index=row.index, source_trace_id=row.source_trace_id)
            if run.mode == EvalRun.Mode.RECORDED:
                entry.output = generation.recorded_output(generation.prepared(row))
                entry.state = EvalRunRow.State.DONE
                entry.finished_at = now
                if entry.output in (None, "", []):
                    entry.degraded = True
                    entry.degraded_reason = "no_output: the row has no recorded output"
            rows.append(entry)
        EvalRunRow.objects.bulk_create(rows)
        created += len(rows)
    return created


def start(run_id) -> str:
    """Claims a pending run and seeds its rows. Returns ``"generate"`` when rows wait
    for generation, ``"score"`` when the run is ready to score, or ``""`` when skipped."""
    try:
        # Admission must never observe a running run with an incomplete row inventory.
        with transaction.atomic():
            if not EvalRun.objects.filter(pk=run_id, status=EvalRun.Status.PENDING).update(
                status=EvalRun.Status.RUNNING, error="", updated_at=timezone.now()
            ):
                return ""
            run = EvalRun.objects.select_related("cell", "model_ref").get(pk=run_id)
            row_store.verify(run.cell)
            if not seed_rows(run):
                raise RunError("Nothing to score: the dataset version has no rows.")
    except (RunError, row_store.RowStoreError, OSError) as exc:
        fail(run_id, str(exc))
        return ""
    if run.mode == EvalRun.Mode.GENERATE:
        generation.warm(run)
        return "generate"
    return "score"


def hand_to_scoring(run_id) -> str:
    """Stamps the run's one scoring token, so a duplicate hand-off cannot score twice;
    blank when the run was already handed off."""
    task_id = str(uuid.uuid4())
    if not EvalRun.objects.filter(pk=run_id, scoring_task_id="").update(scoring_task_id=task_id):
        return ""
    return task_id


def score(run_id, task_id) -> bool:
    if not generation.begin_scoring(run_id, task_id):
        return False
    run = EvalRun.objects.select_related("dataset__capability", "cell", "model_ref").get(pk=run_id)
    try:
        async_loop.run(dataset.score_rows(run))
    except Exception as exc:
        logger.exception("Scoring run %s failed", run_id)
        fail(run_id, f"scoring failed: {exc}")
        return False
    finish(run)
    return True


def aggregate(run: EvalRun) -> dict:
    rows = list(run.rows.values("row_index", "error", "degraded"))
    scores = comparison.latest_scores(run).values()
    by_metric: dict[str, list[dict]] = defaultdict(list)
    for row_score in scores:
        for name, metric in row_score.metrics.items():
            by_metric[name].append(metric)
    metrics = {}
    for name, results in sorted(by_metric.items()):
        scored = [m["score"] for m in results if m.get("score") is not None]
        metrics[name] = {
            "type": results[0].get("type", ""),
            "mean": mean(scored) if scored else None,
            "n": len(scored),
            "failed": sum(value < FAIL_BELOW for value in scored),
            "errors": sum(bool(m.get("error")) for m in results),
        }
    row_scores = [s.score for s in scores if s.score is not None]
    errored = sum(bool(row["error"]) for row in rows)
    degraded = sum(row["degraded"] for row in rows)
    return {
        "score": mean(row_scores) if row_scores else None,
        "metrics": metrics,
        "rows": len(rows),
        "scored": len(row_scores),
        "errored": errored,
        "degraded": degraded,
        "trust": {
            "trusted": errored == 0 and degraded == 0 and bool(row_scores),
            "errored": errored,
            "degraded": degraded,
        },
    }


def finish(run: EvalRun) -> None:
    """Completes the run, then wires its consumers explicitly on fresh rows: the
    status flip is a queryset update, so no model signal fires."""
    summary = aggregate(run)
    if found := classes.class_metrics(run):
        summary["class_metrics"] = found
    if (
        not EvalRun.objects.filter(pk=run.pk)
        .exclude(status__in=EvalRun.TERMINAL)
        .update(
            status=EvalRun.Status.COMPLETED,
            summary=summary,
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
    ):
        return
    on_terminal(EvalRun.objects.get(pk=run.pk))


def on_terminal(run: EvalRun) -> None:

    update_task(run.project_id, "eval_run", run.pk, run.status)
    # Both consumers import this module to create their runs.
    from overbae.models.optimizer import on_candidate_eval_finished  # noqa: PLC0415
    from overbae.services.finetuning_eval import sync_eval_scores  # noqa: PLC0415

    if run.status == EvalRun.Status.COMPLETED:
        comparison.baseline_comparison(run)
    for job in FinetuningJob.objects.filter(job_evals__eval_run_id=run.pk).distinct():
        sync_eval_scores(job)
    on_candidate_eval_finished(run)


def fail(run_id, reason: str) -> bool:
    now = timezone.now()
    if (
        not EvalRun.objects.filter(pk=run_id)
        .exclude(status__in=EvalRun.TERMINAL)
        .update(status=EvalRun.Status.FAILED, error=reason, completed_at=now, updated_at=now)
    ):
        return False
    run = EvalRun.objects.get(pk=run_id)
    revoke(run)
    on_terminal(run)
    return True


def revoke(run: EvalRun) -> int:
    """Under prefork ``terminate`` kills a running generation; queued ones never start."""
    task_ids = [run.celery_task_id] if run.celery_task_id else []
    task_ids += list(
        run.rows.filter(state__in=generation.IN_FLIGHT)
        .exclude(task_id="")
        .values_list("task_id", flat=True)
    )
    run.rows.filter(state__in=generation.UNFINISHED).update(
        state=EvalRunRow.State.CANCELLED, finished_at=timezone.now()
    )
    if task_ids:
        current_app.control.revoke(task_ids, terminate=True, signal="SIGTERM")
    return len(task_ids)


def cancel(run: EvalRun) -> int:
    now = timezone.now()
    if (
        not EvalRun.objects.filter(pk=run.pk)
        .exclude(status__in=EvalRun.TERMINAL)
        .update(status=EvalRun.Status.CANCELLED, completed_at=now, updated_at=now)
    ):
        return 0
    run.refresh_from_db()
    revoked = revoke(run)
    on_terminal(run)
    return revoked


def row_scores(run: EvalRun) -> dict[int, float | None]:
    return {
        int(source_id.rsplit(":", 1)[1]): row_score.score
        for source_id, row_score in comparison.latest_scores(run).items()
    }


def reap_stalled() -> int:
    now = timezone.now()
    reaped = 0
    for run_id in (
        EvalRun.objects.filter(status=EvalRun.Status.RUNNING, updated_at__lt=now - STALL)
        .exclude(scoring_started_at__gte=now - SCORING_LIMIT)
        .values_list("pk", flat=True)
    ):
        if generation.wait_is_healthy(run_id):
            continue
        reaped += fail(
            run_id, f"evaluation stalled for over {int(STALL.total_seconds() // 60)} minutes"
        )
    return reaped
