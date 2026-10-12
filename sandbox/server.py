from __future__ import annotations

import contextlib
import hmac
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from sandbox import execute
from sandbox.protocol import FramingError, HeaderReader, frame

INPUTS = {"cell": "input.parquet", "inspect": "input.parquet"}


def _authorized(request: Request) -> bool:
    expected = f"Bearer {os.environ['SANDBOX_TOKEN']}"
    given = request.headers.get("authorization", "")
    return hmac.compare_digest(given.encode(), expected.encode())


def _check(header: dict[str, Any]) -> None:
    mode = header.get("mode")
    if mode not in INPUTS:
        raise FramingError(f"Unknown mode {mode!r}.")
    if not isinstance(header.get("script"), str):
        raise FramingError("A script is required.")


async def _receive(request: Request, scratch: Path) -> dict[str, Any]:
    reader = HeaderReader()
    with contextlib.ExitStack() as stack:
        target = None
        async for chunk in request.stream():
            payload = reader.feed(chunk)
            if reader.header is None:
                continue
            if target is None:
                _check(reader.header)
                target = stack.enter_context(open(scratch / INPUTS[reader.header["mode"]], "wb"))
            target.write(payload)
    header = reader.finish()
    if target is None:
        _check(header)
        (scratch / INPUTS[header["mode"]]).touch()
    return header


def _execute(header: dict[str, Any], scratch: Path) -> execute.Outcome:
    return execute.run_script(header["script"], scratch, produce_frame=header["mode"] == "cell")


async def run(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    scratch = Path(tempfile.mkdtemp(prefix="run_"))
    cleanup = BackgroundTask(shutil.rmtree, scratch, ignore_errors=True)
    try:
        header = await _receive(request, scratch)
        outcome = await run_in_threadpool(_execute, header, scratch)
    except FramingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, background=cleanup)
    except execute.BusyError:
        return JSONResponse({"busy": True}, status_code=503, background=cleanup)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    reply = {
        "ok": outcome.ok,
        "error": outcome.error,
        "stdout": outcome.stdout,
        "seconds": outcome.seconds,
    }
    return StreamingResponse(
        frame(reply, outcome.output),
        media_type="application/octet-stream",
        background=cleanup,
    )


async def health(_request: Request) -> Response:
    return JSONResponse({"ok": True})


app = Starlette(
    routes=[Route("/v1/run", run, methods=["POST"]), Route("/health", health, methods=["GET"])]
)
