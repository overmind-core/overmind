from __future__ import annotations

import json
import struct
from collections.abc import Iterator
from typing import Any, BinaryIO

PROTOCOL = 1
MAX_HEADER_BYTES = 1024**2
CHUNK_BYTES = 1024**2


class FramingError(ValueError):
    pass


def header_bytes(header: dict[str, Any]) -> bytes:
    body = json.dumps({"protocol": PROTOCOL, **header}).encode("utf-8")
    return struct.pack(">I", len(body)) + body


class HeaderReader:
    def __init__(self) -> None:
        self.header: dict[str, Any] | None = None
        self._buffer = b""

    def feed(self, chunk: bytes) -> bytes:
        if self.header is not None:
            return chunk
        self._buffer += chunk
        if len(self._buffer) < 4:
            return b""
        size = struct.unpack(">I", self._buffer[:4])[0]
        if size > MAX_HEADER_BYTES:
            raise FramingError("The header is too large.")
        if len(self._buffer) < 4 + size:
            return b""
        try:
            header = json.loads(self._buffer[4 : 4 + size])
        except ValueError as exc:
            raise FramingError("The header is not JSON.") from exc
        if not isinstance(header, dict) or header.get("protocol") != PROTOCOL:
            raise FramingError(f"Protocol {PROTOCOL} is required.")
        self.header = header
        rest, self._buffer = self._buffer[4 + size :], b""
        return rest

    def finish(self) -> dict[str, Any]:
        if self.header is None:
            raise FramingError("The body ended before the header.")
        return self.header


def frame(header: dict[str, Any], payload: BinaryIO | None = None) -> Iterator[bytes]:
    yield header_bytes(header)
    if payload is None:
        return
    with payload:
        while chunk := payload.read(CHUNK_BYTES):
            yield chunk
