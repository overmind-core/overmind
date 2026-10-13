"""Script runs: a durable ``Execution`` row per run, per-project slots in Redis, and the
sandbox client. The sandbox executes every script; the worker only moves frames."""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import shutil
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import redis
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from overbae.models import Cell, Dataset, Execution
from overbae.services.datasets import events, measure, paths, review, store, synthetic
from overbae.services.datasets.lifecycle import DatasetError
from sandbox import tables
from sandbox.protocol import CHUNK_BYTES, HeaderReader, header_bytes

logger = logging.getLogger(__name__)

KEEP_DAYS = 30
RESULT_SECONDS = 60 * 60
WAIT_SECONDS = 25
POLL_SECONDS = 0.5
STDOUT_CHARS = 4000
SLOT_LEASE_SECONDS = 600
QUEUE_LIMIT_SECONDS = 15 * 60
SLOT_RETRY_SECONDS = 2
BUSY_RETRY_SECONDS = 5
UNREACHABLE_RETRY_SECONDS = (2, 4, 8)
STALE_RUNNING_SECONDS = 11 * 60
STALE_QUEUED_SECONDS = 20 * 60
PREVIEW_ROWS = 3
MAX_PREVIEW_COLUMNS = 200
VALUE_CHARS = 400

STOPPED = "The run stopped without a result."
UNAVAILABLE = "Scripts cannot run right now."
REFUSED = "The run could not start. Run it again later."
NO_MEMORY = "Not enough memory was free to start the run."
NO_SLOT = "The run waited 15 minutes behind other runs in this project."
FAILED_ON_WORKER = "The run failed for a reason outside the script. Run it again."

_TAKE_SLOT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZSCORE', KEYS[1], ARGV[3]) or redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[4]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  return 1
end
return 0
"""


class SandboxBusyError(Exception):
    pass


class SandboxUnavailableError(Exception):
    pass


class SandboxRefusedError(Exception):
    """A 4xx reply: a wrong token or a request the sandbox cannot read. Retrying the
    same request cannot help."""


@dataclass
class Retry:
    countdown: float
    unreachable: int = 0


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def key(mode: str, dataset_id: Any, parent_id: Any, script: str) -> str:
    return sha256(json.dumps([mode, str(dataset_id), str(parent_id or ""), script]))


@functools.cache
def _client(url: str):
    # A queued run asks for a slot every 2 s; one pool per worker process serves them all.
    return redis.from_url(url, decode_responses=True)


def _redis():
    url = settings.CELERY_BROKER_URL
    if not url.startswith(("redis://", "rediss://", "unix://")):
        return None
    return _client(url)


def _slots_key(project_id: Any) -> str:
    return f"workshop:project:{project_id}:slots"


def take_slot(run: Execution) -> bool:
    client = _redis()
    if client is None:
        return True
    now = time.time()
    return bool(
        client.eval(
            _TAKE_SLOT,
            1,
            _slots_key(run.project_id),
            now,
            now + SLOT_LEASE_SECONDS,
            str(run.pk),
            settings.WORKSHOP_PROJECT_CONCURRENCY,
            SLOT_LEASE_SECONDS * 2,
        )
    )


def release_slot(run: Execution) -> None:
    client = _redis()
    if client is not None:
        client.zrem(_slots_key(run.project_id), str(run.pk))


def _emit(run: Execution, kind: str) -> None:
    events.publish(
        run.dataset_id,
        {
            "dataset_id": str(run.dataset_id),
            "type": kind,
            "execution_id": str(run.pk),
            "mode": run.mode,
            "parent_id": str(run.parent_id) if run.parent_id else None,
            "state": run.state,
            "cell_id": str(run.cell_id) if run.cell_id else None,
        },
    )


def _move(run: Execution, expected: str, **fields: Any) -> bool:
    moved = Execution.objects.filter(pk=run.pk, state=expected).update(**fields)
    if moved:
        for name, value in fields.items():
            setattr(run, name, value)
    return bool(moved)


def finish(
    run: Execution,
    state: str,
    *,
    error: str = "",
    transient: bool = False,
    expected: str = Execution.State.RUNNING,
    **fields: Any,
) -> bool:
    if not _move(
        run,
        expected,
        state=state,
        error=error[:4000],
        transient=transient,
        finished_at=timezone.now(),
        **fields,
    ):
        return False
    transaction.on_commit(lambda: _emit(run, "execution_finished"))
    return True


def _stale(run: Execution, now) -> bool:
    if run.state == Execution.State.RUNNING:
        return run.started_at is not None and run.started_at < now - timedelta(
            seconds=STALE_RUNNING_SECONDS
        )
    if run.state == Execution.State.QUEUED:
        return run.queued_at < now - timedelta(seconds=STALE_QUEUED_SECONDS)
    return False


def _reusable(run: Execution, now) -> bool:
    if run.state in (Execution.State.QUEUED, Execution.State.RUNNING):
        return True
    recent = run.finished_at is not None and run.finished_at > now - timedelta(
        seconds=RESULT_SECONDS
    )
    if run.state == Execution.State.OK:
        return recent
    return recent and not run.transient


@dataclass
class Submitted:
    run: Execution | None = None
    cell: Cell | None = None


def submit(
    dataset: Dataset,
    *,
    mode: str,
    parent: Cell,
    script: str,
    title: str = "",
    user: Any = None,
    enqueue: bool = True,
) -> Submitted:
    if parent.dataset_id != dataset.pk:
        raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
    run_key = key(mode, dataset.pk, parent.pk, script)
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if mode in (Execution.Mode.CELL, Execution.Mode.SYNTHETIC):
            existing = Cell.objects.filter(parent=parent, script_sha256=sha256(script)).first()
            if existing is not None:
                return Submitted(cell=existing)
        now = timezone.now()
        newest = Execution.objects.filter(key=run_key).order_by("-queued_at").first()
        if newest is not None and _stale(newest, now):
            finish(
                newest, Execution.State.FAILED, error=STOPPED, transient=True, expected=newest.state
            )
        if newest is not None and _reusable(newest, now):
            return Submitted(run=newest)
        run = Execution.objects.create(
            key=run_key,
            project_id=locked.project_id,
            dataset=locked,
            parent=parent,
            mode=mode,
            title=title.strip()[:120],
            script=script,
            created_by=user if getattr(user, "pk", None) else None,
            queued_at=now,
        )
        transaction.on_commit(lambda: _emit(run, "execution_queued"))
        if enqueue:
            transaction.on_commit(lambda: _enqueue(run))
    return Submitted(run=run)


def _enqueue(run: Execution) -> None:
    # The task module imports this service.
    from overbae.tasks.datasets import execute

    execute.apply_async(kwargs={"execution_id": str(run.pk)}, task_id=str(run.pk))


def wait(run: Execution, seconds: float = WAIT_SECONDS) -> Execution:
    deadline = time.monotonic() + seconds
    while run.state in (Execution.State.QUEUED, Execution.State.RUNNING):
        if time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
        run.refresh_from_db()
    return run


def sandbox_call(
    header: dict[str, Any], payload: Path, output: Path, *, timeout: float = 1500
) -> dict[str, Any]:
    def body() -> Iterator[bytes]:
        yield header_bytes(header)
        with open(payload, "rb") as fh:
            while chunk := fh.read(CHUNK_BYTES):
                yield chunk

    try:
        with httpx.stream(
            "POST",
            f"{settings.SANDBOX_URL}/v1/run",
            content=body(),
            headers={"Authorization": f"Bearer {settings.SANDBOX_TOKEN}"},
            timeout=httpx.Timeout(timeout, connect=5),
        ) as response:
            if response.status_code == 503:
                response.read()
                try:
                    busy = response.json().get("busy") is True
                except ValueError:
                    busy = False
                if busy:
                    raise SandboxBusyError
                raise SandboxUnavailableError("503")
            if response.status_code != 200:
                response.read()
                detail = f"{response.status_code}: {response.text[:200]}"
                if 400 <= response.status_code < 500:
                    raise SandboxRefusedError(detail)
                raise SandboxUnavailableError(detail)
            reader = HeaderReader()
            with open(output, "wb") as out:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    out.write(reader.feed(chunk))
            return reader.finish()
    except httpx.TransportError as exc:
        raise SandboxUnavailableError(str(exc)) from exc


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > VALUE_CHARS:
        return value[:VALUE_CHARS] + f"…[+{len(value) - VALUE_CHARS}]"
    if isinstance(value, list):
        return [_clip(v) for v in value[:12]]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in list(value.items())[:30]}
    return value


HIDDEN_COLUMNS = frozenset({store.SOURCE_ROW, review.PROVENANCE_COLUMN})


def visible(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_clip({k: v for k, v in row.items() if k not in HIDDEN_COLUMNS}) for row in rows]


def visible_columns(names: list[str]) -> list[str]:
    return [name for name in names if name not in HIDDEN_COLUMNS]


def preview(path: Path) -> dict[str, Any]:
    names = [c["name"] for c in store.read_manifest(path)]
    # to_json writes NaN and NaT as null; json.dumps would write NaN, which jsonb refuses.
    head = store.frame_rows(path, range(PREVIEW_ROWS)).to_json(
        orient="records", date_format="iso", default_handler=str
    )
    return {
        "rows": store.row_count(path),
        "columns": visible_columns(names)[:MAX_PREVIEW_COLUMNS],
        "head": visible(json.loads(head)),
    }


def add_version(
    dataset: Dataset,
    parent: Cell,
    output: Path,
    *,
    title: str,
    script: str,
    identity: str | None = None,
    stdout: str = "",
    seconds: float = 0.0,
    user: Any = None,
) -> Cell:
    """Finalize ``output`` against ``parent`` and store it as a new child version.
    ``identity`` is what makes the version unique under its parent; it defaults to the
    script."""
    digest = sha256(script if identity is None else identity)
    parent_path = paths.cell_path(dataset.pk, parent.pk)
    with tempfile.TemporaryDirectory(prefix="version-") as scratch:
        final = Path(scratch) / "version.parquet"
        review.finalize(
            parent_path,
            output,
            final,
            group_by=(dataset.source_spec.get("split") or {}).get("group_by", []),
        )
        impact = review.impact(parent_path, final)
        measured = Dataset.objects.select_related("capability").get(pk=dataset.pk)
        fields = measure.describe_frame(measured, final)
        destination: Path | None = None
        try:
            with transaction.atomic():
                locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
                position = (locked.cells.aggregate(top=Max("position"))["top"] or 0) + 1
                cell = Cell.objects.create(
                    dataset=locked,
                    parent=parent,
                    position=position,
                    title=title.strip()[:255] or "Version",
                    script=script,
                    script_sha256=digest,
                    stdout=stdout[-STDOUT_CHARS:],
                    impact=impact,
                    input_fingerprint=parent.fingerprint,
                    seconds=round(seconds, 3),
                    created_by=user if getattr(user, "pk", None) else None,
                    **fields,
                )
                destination = paths.cell_path(dataset.pk, cell.pk)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(final, destination)
        except BaseException as exc:
            # The row rolled back; a file moved before the commit failed has no row.
            if destination is not None:
                destination.unlink(missing_ok=True)
            if isinstance(exc, IntegrityError):
                existing = Cell.objects.filter(parent=parent, script_sha256=digest).first()
                if existing is not None:
                    return existing
            raise
    transaction.on_commit(
        lambda: events.publish(
            dataset.pk,
            {
                "dataset_id": str(dataset.pk),
                "type": "version_added",
                "cell_id": str(cell.pk),
                "parent_id": str(parent.pk),
                "position": cell.position,
            },
        )
    )
    return cell


def _log(run: Execution, frame_bytes: int) -> None:
    logger.info(
        "workshop execution %s",
        json.dumps(
            {
                "execution_id": str(run.pk),
                "dataset_id": str(run.dataset_id),
                "mode": run.mode,
                "state": run.state,
                "transient": run.transient,
                "queue_seconds": run.queue_seconds,
                "transfer_seconds": run.transfer_seconds,
                "run_seconds": run.run_seconds,
                "frame_bytes": frame_bytes,
            }
        ),
    )


def _start(run: Execution) -> bool:
    now = timezone.now()
    if not _move(
        run,
        Execution.State.QUEUED,
        state=Execution.State.RUNNING,
        started_at=now,
        queue_seconds=round((now - run.queued_at).total_seconds(), 3),
    ):
        return False
    _emit(run, "execution_started")
    return True


def _requeue(run: Execution) -> None:
    _move(run, Execution.State.RUNNING, state=Execution.State.QUEUED, started_at=None)


def _waited_too_long(run: Execution) -> bool:
    # Slot and memory waits share one clock from submission, so a run gives up well
    # before the sweep fails a queued run as stale.
    return run.queued_at < timezone.now() - timedelta(seconds=QUEUE_LIMIT_SECONDS)


def perform(
    execution_id: Any,
    *,
    unreachable: int = 0,
    slots: bool = True,
    retries: bool = True,
) -> Retry | None:
    """Run one execution to a terminal state, or return when to try again. Any state
    update that matches no row means another worker owns the run."""
    run = (
        Execution.objects.select_related("dataset", "parent", "created_by")
        .filter(pk=execution_id)
        .first()
    )
    if run is None or run.state != Execution.State.QUEUED:
        return None
    if slots and not take_slot(run):
        if _waited_too_long(run):
            finish(
                run,
                Execution.State.FAILED,
                error=NO_SLOT,
                transient=True,
                expected=Execution.State.QUEUED,
            )
            return None
        return Retry(SLOT_RETRY_SECONDS, unreachable)
    if not _start(run):
        # A second delivery matches the slot the running delivery holds; leave it.
        if (
            slots
            and not Execution.objects.filter(pk=run.pk, state=Execution.State.RUNNING).exists()
        ):
            release_slot(run)
        return None
    try:
        return _execute(run, unreachable=unreachable, retries=retries)
    except SoftTimeLimitExceeded:
        raise
    except Exception:
        logger.exception("workshop execution %s failed on the worker", run.pk)
        finish(run, Execution.State.FAILED, error=FAILED_ON_WORKER, transient=True)
        return None
    finally:
        if slots:
            release_slot(run)


def _execute(run: Execution, *, unreachable: int, retries: bool) -> Retry | None:
    dataset, parent = run.dataset, run.parent
    if parent is None:
        finish(run, Execution.State.FAILED, error="The version this run read was deleted.")
        return None
    if run.mode == Execution.Mode.SYNTHETIC:
        _finish_synthetic(run)
        return None
    parent_path = paths.cell_path(dataset.pk, parent.pk)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="execution-") as scratch:
        output = Path(scratch) / "output.parquet"
        try:
            header = sandbox_call(
                {
                    "mode": "inspect" if run.mode == Execution.Mode.PROBE else "cell",
                    "script": run.script,
                    "input_bytes": tables.uncompressed_bytes(parent_path),
                },
                parent_path,
                output,
            )
        except SandboxBusyError:
            if retries and not _waited_too_long(run):
                _requeue(run)
                return Retry(BUSY_RETRY_SECONDS, unreachable)
            finish(run, Execution.State.FAILED, error=NO_MEMORY, transient=True)
            return None
        except SandboxUnavailableError as exc:
            logger.warning("sandbox unavailable for execution %s: %s", run.pk, exc)
            if retries and unreachable < len(UNREACHABLE_RETRY_SECONDS):
                _requeue(run)
                return Retry(UNREACHABLE_RETRY_SECONDS[unreachable], unreachable + 1)
            finish(run, Execution.State.FAILED, error=UNAVAILABLE, transient=True)
            return None
        except SandboxRefusedError as exc:
            logger.error("sandbox refused execution %s: %s", run.pk, exc)
            finish(run, Execution.State.FAILED, error=REFUSED, transient=True)
            return None
        elapsed = time.monotonic() - started
        run_seconds = float(header.get("seconds") or 0.0)
        timings = {
            "run_seconds": round(run_seconds, 3),
            "transfer_seconds": round(max(elapsed - run_seconds, 0.0), 3),
        }
        stdout = str(header.get("stdout") or "")[-STDOUT_CHARS:]
        if not header.get("ok"):
            finish(
                run,
                Execution.State.FAILED,
                error=str(header.get("error") or "The script failed."),
                stdout=stdout,
                **timings,
            )
        elif run.mode == Execution.Mode.TRY:
            finish(run, Execution.State.OK, stdout=stdout, result=preview(output), **timings)
        elif run.mode == Execution.Mode.PROBE:
            finish(run, Execution.State.OK, stdout=stdout, **timings)
        else:
            cell = add_version(
                dataset,
                parent,
                output,
                title=run.title,
                script=run.script,
                stdout=stdout,
                seconds=run_seconds,
                user=run.created_by,
            )
            finish(run, Execution.State.OK, stdout=stdout, cell=cell, **timings)
    _log(run, parent_path.stat().st_size if parent_path.exists() else 0)
    return None


def _finish_synthetic(run: Execution) -> None:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="synthetic-") as scratch:
        output = Path(scratch) / "output.parquet"
        try:
            synthetic.write(run.dataset, run.parent, run.script, output)
        except DatasetError as exc:
            finish(run, Execution.State.FAILED, error=exc.detail)
            return
        cell = add_version(
            run.dataset,
            run.parent,
            output,
            title=run.title,
            script="",
            identity=run.script,
            user=run.created_by,
        )
    finish(
        run,
        Execution.State.OK,
        cell=cell,
        run_seconds=round(time.monotonic() - started, 3),
        transfer_seconds=0.0,
    )


def run_now(
    dataset: Dataset,
    *,
    mode: str,
    parent: Cell,
    script: str,
    title: str = "",
    user: Any = None,
) -> Submitted:
    submitted = submit(
        dataset, mode=mode, parent=parent, script=script, title=title, user=user, enqueue=False
    )
    if submitted.run is not None and submitted.run.state == Execution.State.QUEUED:
        perform(submitted.run.pk, slots=False, retries=False)
        submitted.run.refresh_from_db()
        if submitted.run.cell_id:
            submitted.cell = submitted.run.cell
    return submitted


def sweep() -> dict[str, int]:
    now = timezone.now()
    stopped = 0
    stale = Execution.objects.filter(
        state=Execution.State.RUNNING,
        started_at__lt=now - timedelta(seconds=STALE_RUNNING_SECONDS),
    ) | Execution.objects.filter(
        state=Execution.State.QUEUED,
        queued_at__lt=now - timedelta(seconds=STALE_QUEUED_SECONDS),
    )
    for run in stale.iterator():
        with transaction.atomic():
            if finish(
                run, Execution.State.FAILED, error=STOPPED, transient=True, expected=run.state
            ):
                stopped += 1
    deleted, _ = Execution.objects.filter(queued_at__lt=now - timedelta(days=KEEP_DAYS)).delete()
    return {"stopped": stopped, "deleted": deleted}


def on_soft_limit(execution_id: Any) -> None:
    run = Execution.objects.filter(pk=execution_id).first()
    if run is not None and run.state == Execution.State.RUNNING:
        finish(run, Execution.State.FAILED, error=STOPPED, transient=True)
