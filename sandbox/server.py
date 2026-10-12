from __future__ import annotations

import contextlib
import hmac
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
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

MODES = ("cell", "inspect", "extract")


def _authorized(request: Request) -> bool:
    expected = f"Bearer {os.environ['SANDBOX_TOKEN']}"
    given = request.headers.get("authorization", "")
    return hmac.compare_digest(given.encode(), expected.encode())


def _check(header: dict[str, Any]) -> None:
    mode = header.get("mode")
    if mode not in MODES:
        raise FramingError(f"Unknown mode {mode!r}.")
    declared = header.get("input_bytes")
    if not isinstance(declared, int) or isinstance(declared, bool) or declared < 0:
        raise FramingError("input_bytes is required.")
    if mode == "extract":
        if not isinstance(header.get("filename"), str) or not header["filename"].strip():
            raise FramingError("A filename is required.")
    elif not isinstance(header.get("script"), str):
        raise FramingError("A script is required.")


async def _header(chunks: AsyncIterator[bytes]) -> tuple[dict[str, Any], bytes]:
    reader = HeaderReader()
    async for chunk in chunks:
        rest = reader.feed(chunk)
        if reader.header is not None:
            return reader.header, rest
    return reader.finish(), b""


async def _drain(chunks: AsyncIterator[bytes]) -> None:
    # The client sends the whole body before it reads the reply.
    async for _ in chunks:
        pass


async def _store(chunks: AsyncIterator[bytes], first: bytes, target: Path, limit: int) -> None:
    written = len(first)
    with open(target, "wb") as fh:
        fh.write(first)
        async for chunk in chunks:
            written += len(chunk)
            if written > limit:
                await _drain(chunks)
                break
            fh.write(chunk)
    if written > limit:
        raise FramingError("The body is larger than input_bytes allows.")


def _reply(outcome: execute.Outcome) -> dict[str, Any]:
    reply = {
        "ok": outcome.ok,
        "error": outcome.error,
        "stdout": outcome.stdout,
        "seconds": outcome.seconds,
    }
    if outcome.extraction is not None:
        reply["extraction"] = outcome.extraction
    return reply


async def run(request: Request) -> Response:
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    chunks = request.stream()
    try:
        header, first = await _header(chunks)
        _check(header)
    except FramingError as exc:
        await _drain(chunks)
        return JSONResponse({"error": str(exc)}, status_code=400)
    need, budget = execute.need_for(header), execute.memory_budget()
    refused = execute.refusal(header, need, budget)
    if refused:
        await _drain(chunks)
        return StreamingResponse(
            frame(_reply(execute.Outcome(error=refused))), media_type="application/octet-stream"
        )
    # Reserve before the upload: a request waiting for memory holds no file on the tmpfs.
    try:
        slot = await run_in_threadpool(execute.reserve, need, budget)
    except execute.BusyError:
        await _drain(chunks)
        return JSONResponse({"busy": True}, status_code=503)
    scratch = Path(tempfile.mkdtemp(prefix="run_", dir=execute.runs_dir()))
    cleanup = BackgroundTask(shutil.rmtree, scratch, ignore_errors=True)
    source = scratch / ("document" if header["mode"] == "extract" else "input.parquet")
    try:
        # A frame's file is never larger than its uncompressed bytes plus the footer.
        limit = header["input_bytes"] + (0 if header["mode"] == "extract" else 64 * 1024**2)
        await _store(chunks, first, source, limit)
        if not execute.input_matches(header, source):
            raise FramingError("The body does not match input_bytes.")
        outcome = await run_in_threadpool(execute.run, header, scratch, source, slot)
    except FramingError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400, background=cleanup)
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    finally:
        await run_in_threadpool(execute.release, slot)
    return StreamingResponse(
        frame(_reply(outcome), outcome.output),
        media_type="application/octet-stream",
        background=cleanup,
    )


async def health(_request: Request) -> Response:
    return JSONResponse({"ok": True})


@contextlib.asynccontextmanager
async def lifespan(_app: Starlette) -> AsyncIterator[None]:
    execute.runs_dir()
    yield


app = Starlette(
    routes=[Route("/v1/run", run, methods=["POST"]), Route("/health", health, methods=["GET"])],
    lifespan=lifespan,
)
