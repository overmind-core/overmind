"""Long-lived kernels. A session holds one memory reservation and one uid for its whole
life, under the same limits as a run. Files the caller puts are read-only to the kernel."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from sandbox import execute

logger = logging.getLogger(__name__)
SESSION_BYTES = 2 * execute.GIB
# A session keeps a kernel within the cores a hosted provider gives one.
SESSION_CPUS = 2
IDLE_SECONDS = 15 * 60
MAX_INPUT_BYTES = execute.GIB
MAX_OUTPUT_BYTES = 64 * 1024**2
MAX_EXECUTION_SECONDS = 600
# What a kernel may answer: its bounded output and error, as JSON. The kernel runs the
# tenant's code, so the server holds it to this size itself.
MAX_REPLY_BYTES = 1024**2
START_SECONDS = 20
REPLY_GRACE_SECONDS = 15
WATCH_SECONDS = 1
FOOTPRINT_EVERY = 5
RENEW_EVERY = 60
REMEMBERED_ENDS = 256
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
KERNEL = Path(__file__).with_name("kernel.py")


class GoneError(Exception):
    pass


class KernelError(Exception):
    """A kernel that could not start."""


class ExecutingError(Exception):
    """The session already runs a cell."""


@dataclass
class Session:
    slot: execute.Slot
    root: Path
    process: subprocess.Popen
    used: float = field(default_factory=time.monotonic)
    executing: threading.Lock = field(default_factory=threading.Lock)

    @property
    def socket_path(self) -> str:
        return str(self.root / "work" / "kernel.sock")


_sessions: dict[str, Session] = {}
_ended: dict[str, str] = {}


def _answers(path: str) -> bool:
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(path)
        except OSError:
            return False
    return True


def start() -> str:
    slot = execute.reserve(SESSION_BYTES, execute.memory_budget())
    root = process = None
    try:
        root = Path(tempfile.mkdtemp(prefix="session_", dir=execute.runs_dir()))
        inputs, work = root / "inputs", root / "work"
        inputs.mkdir()
        work.mkdir()
        if slot.uid is not None:
            # Every uid can traverse the runs directory: only this session's group may enter.
            for path in (root, inputs):
                os.chown(path, 0, slot.uid)
                path.chmod(0o750)
            os.chown(work, slot.uid, slot.uid)
        limits = {
            "cpus": SESSION_CPUS,
            "rlimits": {
                "RLIMIT_FSIZE": execute.FILE_SIZE_BYTES,
                "RLIMIT_NOFILE": execute.FILE_DESCRIPTORS,
                **({} if slot.uid is None else {"RLIMIT_NPROC": execute.PROCESSES}),
            },
        }
        # Why a kernel died before it answered is the only thing its stderr is kept for.
        with open(root / "kernel.err", "wb") as errors:
            process = subprocess.Popen(
                [sys.executable, "-I", str(KERNEL), "serve", "kernel.sock", json.dumps(limits)],
                stdout=subprocess.DEVNULL,
                stderr=errors,
                **execute.confinement(work, slot.uid),
            )
        session = Session(slot, root, process)
        deadline = time.monotonic() + START_SECONDS
        while not _answers(session.socket_path):
            if process.poll() is not None or time.monotonic() > deadline:
                cause = (root / "kernel.err").read_text(errors="replace").strip()[-400:]
                raise KernelError(f"The kernel did not start. {cause}".strip())
            time.sleep(0.02)
    except BaseException:
        _destroy(slot, root, process)
        raise
    session_id = uuid.uuid4().hex
    _sessions[session_id] = session
    return session_id


def _destroy(slot: execute.Slot, root: Path | None, process: subprocess.Popen | None) -> None:
    if process is not None:
        execute.end(root, process.pid, slot.uid)
        process.wait()
    if root is not None:
        shutil.rmtree(root, ignore_errors=True)
    execute.release(slot)


def end(session_id: str, reason: str) -> None:
    session = _sessions.pop(session_id, None)
    if session is None:
        return
    _ended[session_id] = reason
    if len(_ended) > REMEMBERED_ENDS:
        del _ended[next(iter(_ended))]
    _destroy(session.slot, session.root, session.process)


def _find(session_id: str) -> Session:
    session = _sessions.get(session_id)
    if session is None:
        raise GoneError(_ended.get(session_id, "The session does not exist."))
    return session


def _text(value) -> str:
    # A lone surrogate is valid in a Python string and in no response this server sends.
    return str(value).encode("utf-8", "replace").decode()


def _exchange(session: Session, request: dict) -> dict:
    """One request to the kernel and its reply, within one deadline and one size. The
    kernel runs the tenant's code: nothing it sends is taken on trust."""
    deadline = time.monotonic() + request["timeout"] + REPLY_GRACE_SECONDS
    received = bytearray()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(REPLY_GRACE_SECONDS)
        connection.connect(session.socket_path)
        connection.sendall(json.dumps(request).encode() + b"\n")
        while True:
            connection.settimeout(max(0.01, deadline - time.monotonic()))
            chunk = connection.recv(65536)
            if not chunk:
                break
            received += chunk
            if len(received) > MAX_REPLY_BYTES or time.monotonic() > deadline:
                raise ValueError("The kernel's reply passed its limits.")
    reply = json.loads(received)
    return {
        "ok": reply["ok"] is True,
        "output": _text(reply["output"]),
        "error": _text(reply["error"]),
        "truncated": reply["truncated"] is True,
        "seconds": float(reply["seconds"]),
    }


def run(session_id: str, request: dict) -> dict:
    session = _find(session_id)
    if not session.executing.acquire(blocking=False):
        raise ExecutingError
    try:
        return _exchange(session, request)
    except (OSError, ValueError, KeyError, TypeError):
        # The kernel's own deadline did not fire: it is stuck outside Python, or dead.
        end(session_id, "The kernel stopped answering and the session was closed.")
        raise GoneError(_ended[session_id]) from None
    finally:
        session.used = time.monotonic()
        session.executing.release()


def _check(session_id: str, session: Session, tick: int) -> None:
    reason = ""
    if session.process.poll() is not None:
        reason = "The kernel exited."
    elif not session.executing.locked() and time.monotonic() - session.used > IDLE_SECONDS:
        reason = f"The session was idle for {IDLE_SECONDS // 60} minutes and was closed."
    elif tick % FOOTPRINT_EVERY == 0:
        try:
            used = execute.footprint(session.root, session.process.pid, session.slot.uid)
        except execute.TooManyFilesError:
            used = session.slot.limit + 1
        if used > session.slot.limit:
            reason = (
                f"The session used more than {execute.gib(session.slot.limit)} GiB "
                "of memory and files and was closed."
            )
    if reason:
        end(session_id, reason)
    elif tick % RENEW_EVERY == 0:
        execute.renew(session.slot)


def watch(stop: threading.Event) -> None:
    tick = 0
    while not stop.wait(WATCH_SECONDS):
        tick += 1
        for session_id, session in list(_sessions.items()):
            try:
                _check(session_id, session, tick)
            except Exception:
                # One session's failure must not end the watch of all the others.
                logger.exception("session %s could not be checked", session_id)
    for session_id in list(_sessions):
        end(session_id, "The sandbox restarted.")


def _gone(exc: GoneError) -> Response:
    return JSONResponse({"gone": True, "reason": str(exc)}, status_code=404)


async def create(_request: Request) -> Response:
    try:
        session_id = await run_in_threadpool(start)
    except execute.BusyError:
        return JSONResponse({"error": "The sandbox has no memory free."}, status_code=503)
    except KernelError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    # With several sandbox servers, later calls must reach the one that holds the session.
    advertised = os.environ.get("SANDBOX_ADVERTISE_URL", "").rstrip("/")
    return JSONResponse({"id": session_id, "url": advertised}, status_code=201)


async def execute_code(request: Request) -> Response:
    body = await request.json()
    timeout = body.get("timeout")
    if (
        not isinstance(body.get("id"), str)
        or not isinstance(body.get("code"), str)
        or type(timeout) is not int
        or not 1 <= timeout <= MAX_EXECUTION_SECONDS
    ):
        return JSONResponse({"error": "id, code and timeout are required."}, status_code=400)
    request_body = {"id": body["id"], "code": body["code"], "timeout": timeout}
    try:
        return JSONResponse(
            await run_in_threadpool(run, request.path_params["session"], request_body)
        )
    except GoneError as exc:
        return _gone(exc)
    except ExecutingError:
        return JSONResponse({"error": "The session already runs a cell."}, status_code=409)


async def put_file(request: Request) -> Response:
    name = request.path_params["name"]
    try:
        session = _find(request.path_params["session"])
    except GoneError as exc:
        return _gone(exc)
    if not NAME.fullmatch(name):
        return JSONResponse({"error": "Invalid file name."}, status_code=400)
    inputs = session.root / "inputs"
    partial = inputs / f".{uuid.uuid4().hex}"
    written = 0
    # A long upload is use: the watch must not close the session under it.
    session.used = time.monotonic()
    try:
        with open(partial, "xb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_INPUT_BYTES:
                    return JSONResponse({"error": "The file is too large."}, status_code=413)
                fh.write(chunk)
        if session.slot.uid is not None:
            os.chown(partial, 0, session.slot.uid)
        partial.chmod(0o640)
        partial.replace(inputs / name)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
    session.used = time.monotonic()
    return Response(status_code=204)


async def get_file(request: Request) -> Response:
    name = request.path_params["name"]
    try:
        session = _find(request.path_params["session"])
    except GoneError as exc:
        return _gone(exc)
    # A flat name under a parent the kernel cannot rename: no path it controls is followed.
    opened = (
        execute.claim(session.root / "work" / name, MAX_OUTPUT_BYTES)
        if NAME.fullmatch(name)
        else None
    )
    if opened is None:
        return JSONResponse({"error": "No readable regular file has that name."}, status_code=404)
    with opened:
        return Response(await run_in_threadpool(opened.read), media_type="application/octet-stream")


async def delete(request: Request) -> Response:
    await run_in_threadpool(end, request.path_params["session"], "The session was stopped.")
    return Response(status_code=204)


ROUTES = [
    ("/v1/sessions", create, ["POST"]),
    ("/v1/sessions/{session}", delete, ["DELETE"]),
    ("/v1/sessions/{session}/execute", execute_code, ["POST"]),
    ("/v1/sessions/{session}/files/{name}", put_file, ["PUT"]),
    ("/v1/sessions/{session}/files/{name}", get_file, ["GET"]),
]
