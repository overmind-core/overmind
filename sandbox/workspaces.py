"""Project workspaces: a folder for each project that outlives its kernels, and one kernel
for each conversation in it. Versions a project reads are copied in once; a kernel is
disposable and its files stay.

    <root>/<project>/data/<name>                   read-only to every kernel of the project
    <root>/<project>/conversations/<conversation>/ a kernel's working directory, with
                                                   ``data`` linked to the project's data
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
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
KERNEL_BYTES = 2 * execute.GIB
KERNEL_CPUS = 2
IDLE_SECONDS = 15 * 60
# A new kernel waits this long for memory, then the conversation is told the sandbox is full.
MEMORY_WAIT_SECONDS = 20
MAX_DATA_BYTES = execute.GIB
MAX_FILE_BYTES = 64 * 1024**2
MAX_EXECUTION_SECONDS = 600
DATA_BUDGET_BYTES = 20 * execute.GIB
# What a kernel may answer: its bounded output and error, as JSON. The kernel runs the
# tenant's code, so the server holds it to this size itself.
MAX_REPLY_BYTES = 1024**2
START_SECONDS = 20
REPLY_GRACE_SECONDS = 15
WATCH_SECONDS = 1
FOOTPRINT_EVERY = 5
RENEW_EVERY = 60
CLEAN_EVERY = 300
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
KERNEL = Path(__file__).with_name("kernel.py")
RESTARTED = "The kernel restarted: variables from earlier cells are gone, files remain."


def root() -> Path:
    path = Path(os.environ.get("SANDBOX_WORKSPACES", "/workspaces"))
    path.mkdir(mode=0o711, parents=True, exist_ok=True)
    return path


class BusyError(Exception):
    """The kernel's cell or the sandbox's memory is taken now."""


class StartError(Exception):
    pass


@dataclass
class Kernel:
    slot: execute.Slot
    work: Path
    process: subprocess.Popen
    # The working directory, held open: the socket is reached through it.
    directory: int
    used: float = field(default_factory=time.monotonic)
    executing: threading.Lock = field(default_factory=threading.Lock)

    @property
    def socket_path(self) -> str:
        # A Unix socket path holds 107 bytes, and the working directory's path is as long
        # as the deployment makes it: the open directory names it in a few bytes.
        return f"/proc/self/fd/{self.directory}/.kernel.sock"


_kernels: dict[tuple[str, str], Kernel] = {}
_starting = threading.Lock()


def project_dir(project: str) -> Path:
    path = root() / project
    for part in (path, path / "data", path / "conversations"):
        with contextlib.suppress(FileExistsError):
            part.mkdir(mode=0o755)
    return path


def _answers(path: str) -> bool:
    with socket.socket(socket.AF_UNIX) as probe:
        try:
            probe.connect(path)
        except OSError:
            return False
    return True


def _start(project: str, conversation: str) -> tuple[Kernel, str | None]:
    """A kernel for the conversation, and why its variables are gone when an earlier
    kernel of the same conversation existed."""
    data = project_dir(project) / "data"
    work = data.parent / "conversations" / conversation
    restarted = RESTARTED if work.exists() else None
    work.mkdir(mode=0o700, exist_ok=True)
    # An earlier kernel's socket would refuse this one's.
    (work / ".kernel.sock").unlink(missing_ok=True)
    link = work / "data"
    if not link.is_symlink():
        link.unlink(missing_ok=True)
        link.symlink_to("../../data")
    slot = execute.reserve(KERNEL_BYTES, execute.memory_budget(), MEMORY_WAIT_SECONDS)
    process = None
    directory = os.open(work, os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        execute.hand_over(work, slot.uid)
        limits = {
            "cpus": KERNEL_CPUS,
            "readable": [str(data)],
            "rlimits": {
                "RLIMIT_FSIZE": execute.FILE_SIZE_BYTES,
                "RLIMIT_NOFILE": execute.FILE_DESCRIPTORS,
                **({} if slot.uid is None else {"RLIMIT_NPROC": execute.PROCESSES}),
            },
        }
        errors = work.parent / f".{conversation}.err"
        with open(errors, "wb") as stderr:
            process = subprocess.Popen(
                [sys.executable, "-I", str(KERNEL), "serve", ".kernel.sock", json.dumps(limits)],
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                **execute.confinement(work, slot.uid),
            )
        kernel = Kernel(slot, work, process, directory)
        deadline = time.monotonic() + START_SECONDS
        while not _answers(kernel.socket_path):
            code = process.poll()
            if code is not None or time.monotonic() > deadline:
                ended = (
                    f"it ended with {_ending(code)}"
                    if code is not None
                    else f"it did not answer within {START_SECONDS} s"
                )
                cause = errors.read_text(errors="replace").strip()[-400:]
                raise StartError(f"The kernel did not start: {ended}. {cause}".strip())
            time.sleep(0.02)
    except BaseException:
        if process is not None:
            execute.end(work, process.pid, slot.uid)
            process.wait()
        os.close(directory)
        execute.release(slot)
        raise
    return kernel, restarted


def _ending(code: int) -> str:
    if code < 0:
        with contextlib.suppress(ValueError):
            return signal.Signals(-code).name
    return f"exit status {code}"


def stop(project: str, conversation: str) -> None:
    kernel = _kernels.pop((project, conversation), None)
    if kernel is None:
        return
    execute.end(kernel.work, kernel.process.pid, kernel.slot.uid)
    kernel.process.wait()
    os.close(kernel.directory)
    execute.release(kernel.slot)


def _text(value) -> str:
    # A lone surrogate is valid in a Python string and in no response this server sends.
    return str(value).encode("utf-8", "replace").decode()


def _exchange(kernel: Kernel, request: dict) -> dict:
    """One cell and its reply, within one deadline and one size. The kernel runs the
    tenant's code: nothing it sends is taken on trust."""
    deadline = time.monotonic() + request["timeout"] + REPLY_GRACE_SECONDS
    received = bytearray()
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(REPLY_GRACE_SECONDS)
        connection.connect(kernel.socket_path)
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


def run(project: str, conversation: str, code: str, timeout: int) -> dict:
    """Run a cell in the conversation's kernel, starting one when it has none."""
    restarted = None
    with _starting:
        kernel = _kernels.get((project, conversation))
        if kernel is None:
            kernel, restarted = _start(project, conversation)
            _kernels[(project, conversation)] = kernel
    if not kernel.executing.acquire(blocking=False):
        raise BusyError("This conversation's kernel already runs a cell.")
    try:
        reply = _exchange(kernel, {"code": code, "timeout": timeout})
    except (OSError, ValueError, KeyError, TypeError):
        # The kernel's own deadline did not fire: it is stuck outside Python, or dead.
        stop(project, conversation)
        reply = {
            "ok": False,
            "output": "",
            "error": "The kernel ended during the cell: it ran out of memory or stopped "
            "answering. Variables are gone, files remain.",
            "truncated": False,
            "seconds": 0.0,
        }
    finally:
        kernel.used = time.monotonic()
        kernel.executing.release()
    return {**reply, "restarted": restarted}


def _clean_data() -> None:
    """Above the budget, the versions no conversation asked for longest go first. A kernel
    that still reads one keeps its open file; the next cell copies it in again."""
    files = [path for path in root().glob("*/data/*") if path.is_file()]
    total = sum(path.stat().st_size for path in files)
    for path in sorted(files, key=lambda path: path.stat().st_mtime):
        if total <= DATA_BUDGET_BYTES:
            return
        total -= path.stat().st_size
        path.unlink(missing_ok=True)


def _check(key: tuple[str, str], kernel: Kernel, tick: int) -> None:
    ended = kernel.process.poll() is not None or (
        not kernel.executing.locked() and time.monotonic() - kernel.used > IDLE_SECONDS
    )
    if not ended and tick % FOOTPRINT_EVERY == 0:
        try:
            used = execute.footprint(kernel.work, kernel.process.pid, kernel.slot.uid)
        except execute.TooManyFilesError:
            used = kernel.slot.limit + 1
        ended = used > kernel.slot.limit
    if ended:
        stop(*key)
    elif tick % RENEW_EVERY == 0:
        execute.renew(kernel.slot)


def watch(stop_event: threading.Event) -> None:
    tick = 0
    while not stop_event.wait(WATCH_SECONDS):
        tick += 1
        for key, kernel in list(_kernels.items()):
            try:
                _check(key, kernel, tick)
            except Exception:
                # One kernel's failure must not end the watch of all the others.
                logger.exception("kernel %s could not be checked", key)
        if tick % CLEAN_EVERY == 0:
            try:
                _clean_data()
            except OSError:
                logger.exception("workspace data could not be cleaned")
    for key in list(_kernels):
        stop(*key)


def _ids(request: Request) -> tuple[str, str] | None:
    project = request.path_params["project"]
    conversation = request.path_params.get("conversation", "x")
    return (project, conversation) if ID.fullmatch(project) and ID.fullmatch(conversation) else None


def _invalid() -> Response:
    return JSONResponse({"error": "Invalid project, conversation or file name."}, status_code=400)


async def has_data(request: Request) -> Response:
    ids, name = _ids(request), request.path_params["name"]
    if ids is None or not NAME.fullmatch(name):
        return _invalid()
    path = root() / ids[0] / "data" / name
    if not path.is_file():
        return Response(status_code=404)
    # Use keeps a version from the cleanup.
    os.utime(path)
    return Response(status_code=204)


async def put_data(request: Request) -> Response:
    ids, name = _ids(request), request.path_params["name"]
    if ids is None or not NAME.fullmatch(name):
        return _invalid()
    data = project_dir(ids[0]) / "data"
    partial = data / f".{uuid.uuid4().hex}"
    written = 0
    try:
        with open(partial, "xb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > MAX_DATA_BYTES:
                    return JSONResponse({"error": "The file is too large."}, status_code=413)
                fh.write(chunk)
        partial.chmod(0o444)
        partial.replace(data / name)
    finally:
        with contextlib.suppress(FileNotFoundError):
            partial.unlink()
    return Response(status_code=204)


async def run_cell(request: Request) -> Response:
    ids = _ids(request)
    body = await request.json()
    timeout = body.get("timeout")
    if (
        ids is None
        or not isinstance(body.get("code"), str)
        or type(timeout) is not int
        or not 1 <= timeout <= MAX_EXECUTION_SECONDS
    ):
        return JSONResponse({"error": "code and timeout are required."}, status_code=400)
    try:
        return JSONResponse(await run_in_threadpool(run, *ids, body["code"], timeout))
    except (BusyError, execute.BusyError):
        return JSONResponse({"error": "The sandbox has no memory free now."}, status_code=503)
    except StartError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


async def get_file(request: Request) -> Response:
    ids, name = _ids(request), request.path_params["name"]
    if ids is None or not NAME.fullmatch(name):
        return _invalid()
    # A flat name under a parent the kernel cannot rename: no path it controls is followed.
    work = root() / ids[0] / "conversations" / ids[1]
    opened = execute.claim(work / name, MAX_FILE_BYTES)
    if opened is None:
        return JSONResponse({"error": "No readable regular file has that name."}, status_code=404)
    with opened:
        return Response(await run_in_threadpool(opened.read), media_type="application/octet-stream")


def forget(project: str, data: list[str], conversations: list[str]) -> None:
    for conversation in conversations:
        stop(project, conversation)
        shutil.rmtree(root() / project / "conversations" / conversation, ignore_errors=True)
        (root() / project / "conversations" / f".{conversation}.err").unlink(missing_ok=True)
    for name in data:
        (root() / project / "data" / name).unlink(missing_ok=True)


def remove(project: str) -> None:
    for key in [key for key in _kernels if key[0] == project]:
        stop(*key)
    shutil.rmtree(root() / project, ignore_errors=True)


async def forget_files(request: Request) -> Response:
    body = await request.json()
    project = request.path_params["project"]
    data, conversations = body.get("data", []), body.get("conversations", [])
    if (
        not ID.fullmatch(project)
        or not all(isinstance(n, str) and NAME.fullmatch(n) for n in data)
        or not all(isinstance(c, str) and ID.fullmatch(c) for c in conversations)
    ):
        return _invalid()
    await run_in_threadpool(forget, project, data, conversations)
    return Response(status_code=204)


async def remove_workspace(request: Request) -> Response:
    project = request.path_params["project"]
    if not ID.fullmatch(project):
        return _invalid()
    await run_in_threadpool(remove, project)
    return Response(status_code=204)


async def delete(request: Request) -> Response:
    ids = _ids(request)
    if ids is None:
        return _invalid()
    await run_in_threadpool(stop, *ids)
    return Response(status_code=204)


ROUTES = [
    ("/v1/workspaces/{project}", remove_workspace, ["DELETE"]),
    ("/v1/workspaces/{project}/forget", forget_files, ["POST"]),
    ("/v1/workspaces/{project}/data/{name}", has_data, ["HEAD"]),
    ("/v1/workspaces/{project}/data/{name}", put_data, ["PUT"]),
    ("/v1/workspaces/{project}/kernels/{conversation}", delete, ["DELETE"]),
    ("/v1/workspaces/{project}/kernels/{conversation}/run", run_cell, ["POST"]),
    ("/v1/workspaces/{project}/kernels/{conversation}/files/{name}", get_file, ["GET"]),
]
