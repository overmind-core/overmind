import fcntl
import functools
import json
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import redis
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from overbae.models import DatasetPipelineRunner
from overbae.services.datasets import store, workbench
from sandbox import packages
from sandbox.protocol import FramingError, HeaderReader, header_bytes

QUEUE_SECONDS = 15 * 60
TASK_SECONDS = 1560
_TAKE_SLOT = """
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[1])
if redis.call('ZCARD', KEYS[1]) < tonumber(ARGV[4]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[3])
  redis.call('EXPIRE', KEYS[1], ARGV[5])
  return 1
end
return 0
"""


class RuntimeUnavailableError(ValueError):
    pass


@contextmanager
def scratch():
    root = Path(tempfile.gettempdir())
    for stale in root.glob("workshop-*"):
        try:
            if stale.is_symlink() or not stale.is_dir() or time.time() - stale.stat().st_mtime < 60:
                continue
            with (stale / ".lease").open("a") as lease:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                shutil.rmtree(stale)
        except (BlockingIOError, FileNotFoundError):
            continue
    with (
        tempfile.TemporaryDirectory(prefix="workshop-") as directory,
        (Path(directory) / ".lease").open("w") as lease,
    ):
        fcntl.flock(lease, fcntl.LOCK_EX)
        yield Path(directory)


@functools.cache
def _client(url):
    return redis.from_url(url, decode_responses=True, socket_connect_timeout=5, socket_timeout=5)


def take_slot(project_id, owner):
    if settings.CELERY_BROKER_URL == "memory://":
        return True
    now = time.time()
    return bool(
        _client(settings.CELERY_BROKER_URL).eval(
            _TAKE_SLOT,
            1,
            f"workshop:project:{project_id}:slots",
            now,
            now + TASK_SECONDS + 60,
            owner,
            settings.WORKSHOP_PROJECT_CONCURRENCY,
            TASK_SECONDS + 120,
        )
    )


def release_slot(project_id, owner):
    if settings.CELERY_BROKER_URL != "memory://":
        _client(settings.CELERY_BROKER_URL).zrem(f"workshop:project:{project_id}:slots", owner)


def heartbeat():
    status, images = "unavailable", []
    try:
        response = httpx.get(f"{settings.SANDBOX_URL}/health", timeout=5, follow_redirects=False)
        response.raise_for_status()
        report = response.json()
        if report.get("ok") and report.get("runtime") == packages.runtime_id():
            status, images = "ready", [report["runtime"]]
    except (httpx.HTTPError, ValueError):
        pass
    DatasetPipelineRunner.objects.update_or_create(
        pk="sandbox",
        defaults={"images": images, "heartbeat_at": timezone.now(), "status": status},
    )
    return status


def execute_script(run, index, source, directory, step, files, progress):
    if not settings.SANDBOX_URL or not settings.SANDBOX_TOKEN:
        raise RuntimeUnavailableError("The isolated sandbox is not configured.")
    manifest = run.pipeline.package.manifest
    if manifest["runtime"] != packages.runtime_id():
        raise RuntimeUnavailableError(
            "The pinned runtime is unavailable. Save a new package revision with the advertised sandbox runtime identity."
        )
    receipt = run.result["steps"][index]
    started, checkpoint, stage = time.monotonic(), time.monotonic(), None
    timings = {}

    def phase(next_stage):
        nonlocal stage, checkpoint
        now = time.monotonic()
        if stage:
            timings[stage] = round(timings.get(stage, 0) + now - checkpoint, 4)
        checkpoint, stage = now, next_stage
        receipt["runtime"] = {
            **receipt.get("runtime", {}),
            "backend": "sandbox",
            "identity": manifest["runtime"],
            "stage": stage,
            "stage_seconds": dict(timings),
            "seconds": round(now - started, 4),
        }
        if not progress():
            raise RuntimeUnavailableError(
                "Publication was cancelled; the sandbox request is closing."
            )

    with tempfile.TemporaryDirectory(dir=directory, prefix=f"script-{index}-") as scratch:
        root = Path(scratch)
        payload = root / "package.json"
        payload.write_text(json.dumps(files, ensure_ascii=False))
        if payload.stat().st_size > packages.MAX_PACKAGE_BYTES:
            raise RuntimeUnavailableError("The encoded package exceeds 20 MiB.")
        incoming, output = root / "input.jsonl", root / "output.jsonl"
        phase("input_transfer")
        with incoming.open("wb") as stream:
            for row in store.iter_rows(source):
                row.pop("_overmind_parent_rows", None)
                stream.write(
                    (
                        json.dumps(row, ensure_ascii=False, allow_nan=False, cls=DjangoJSONEncoder)
                        + "\n"
                    ).encode()
                )
                if stream.tell() > packages.MAX_OUTPUT_BYTES:
                    raise RuntimeUnavailableError(
                        "The input exceeds 2 GiB. Declare batch_rows for independent rows."
                    )
        header = {
            "mode": "package",
            "input_bytes": incoming.stat().st_size,
            "package_bytes": payload.stat().st_size,
            "runtime": manifest["runtime"],
            "entrypoint": step["entrypoint"],
            "parameters": run.specification["parameters"],
            "limits": manifest.get("limits", {}),
        }

        def body():
            yield header_bytes(header)
            for path in (payload, incoming):
                with path.open("rb") as stream:
                    while chunk := stream.read(64 * 1024):
                        yield chunk

        reader = HeaderReader()
        try:
            receipt["submission_started_at"] = timezone.now().isoformat()
            if not progress():
                raise RuntimeUnavailableError(
                    "Publication was cancelled before sandbox submission."
                )
            with (
                httpx.stream(
                    "POST",
                    f"{settings.SANDBOX_URL}/v1/run",
                    content=body(),
                    headers={"Authorization": f"Bearer {settings.SANDBOX_TOKEN}"},
                    timeout=httpx.Timeout(1350, connect=5),
                    follow_redirects=False,
                ) as response,
                output.open("wb") as stream,
            ):
                if response.status_code != 200:
                    raise RuntimeUnavailableError(
                        f"The sandbox returned HTTP {response.status_code}. No script was replayed."
                    )
                for chunk in response.iter_bytes():
                    pending = reader.feed(chunk)
                    while reader.header is not None and "progress" in reader.header:
                        phase("script_execution")
                        reader = HeaderReader()
                        pending = reader.feed(pending)
                    if stream.tell() + len(pending) > packages.MAX_OUTPUT_BYTES:
                        raise RuntimeUnavailableError("The sandbox output exceeds 2 GiB.")
                    stream.write(pending)
                result = reader.finish()
            receipt["exit_code"] = result.get("exit_code")
            receipt["runtime"].update(result.get("runtime") or {})
            receipt["runtime"]["execution_seconds"] = result.get("seconds")
            (directory / f"{index}-stdout.log").write_text(result.get("stdout", ""))
            (directory / f"{index}-stderr.log").write_text(result.get("error", ""))
            if not result.get("ok"):
                progress()
                raise RuntimeUnavailableError(result.get("error") or "The sandbox package failed.")
            phase("output_reading")
            with output.open("rb") as rows:
                for line in iter(lambda: rows.readline(4 * 1024 * 1024 + 1), b""):
                    if len(line) > 4 * 1024 * 1024:
                        raise RuntimeUnavailableError("An output row exceeds 4 MiB.")
                    row = json.loads(line)
                    if not isinstance(row, dict) or len(row) > 200:
                        raise RuntimeUnavailableError(
                            "Every output line must be an object with at most 200 columns."
                        )
                    workbench.digest(row)
                    yield row
            phase("completed")
        except (httpx.TransportError, FramingError) as exc:
            raise RuntimeUnavailableError(
                "The sandbox result is unconfirmed. No script was replayed."
            ) from exc
