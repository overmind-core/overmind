"""Turning Python values into span payloads: JSON normalisation with secret
redaction, the per-attribute byte budget, chat-message normalisation, and
the fold that turns a streamed generator into one assistant message.

Leaf module: imports nothing else from ``overmind`` so both ``tracing`` and
``evals`` can depend on it.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
from collections.abc import Iterable
from pathlib import PurePath
from typing import Any

# One ``inputs`` / ``outputs`` attribute above this is replaced by a preview:
# an oversized attribute drops the whole OTLP batch at export, and the server
# refuses to parse a span whose raw payload exceeds 100 KB.
MAX_PAYLOAD_BYTES = 32 * 1024
_PREVIEW_BYTES = 1024

# Guards against cyclic / mock-heavy inputs that recurse forever.
_MAX_DEPTH = 10

_DATA_URL_RE = re.compile(r"^data:[\w.+-]+/[\w.+-]+;base64,")
_BASE64ISH_RE = re.compile(r"^[A-Za-z0-9+/=_-]{512,}$")
# Substring match on lowered dict keys, so provider-prefixed names
# (``openai_api_key``, ``access_token``, ``sensitive_data``) are caught too.
_SECRET_KEY_MARKERS = (
    "password",
    "secret",
    "token",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "sensitive",
)
_extra_redact_keys: frozenset[str] = frozenset()

# Bytes payloads longer than this (screenshots, audio) become a placeholder
# instead of a hex dump.
_MAX_BYTES_HEX = 256

# Type names never serialised; matched by name to avoid importing rich /
# opentelemetry for an isinstance check.
_SKIP_TYPES = frozenset({"Console", "Progress", "Live", "Table", "Panel", "TracerProvider", "Tracer", "Span"})


def add_redact_keys(keys: Iterable[str]) -> None:
    """Extend the exact-match redacted dict keys (``init(redact_keys=...)``)."""
    global _extra_redact_keys
    _extra_redact_keys = _extra_redact_keys | {str(key).lower() for key in keys}


def should_skip(value: Any) -> bool:
    return type(value).__name__ in _SKIP_TYPES


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _extra_redact_keys or any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def _scrub_text(value: str) -> str:
    if _DATA_URL_RE.match(value) or (len(value) >= 512 and _BASE64ISH_RE.match(value)):
        return f"<base64 {len(value)} chars>"
    return value


def _normalize_items(items: Iterable[tuple[Any, Any]], depth: int) -> dict[str, Any]:
    return {str(k): "<redacted>" if _is_secret_key(str(k)) else normalize(v, _depth=depth) for k, v in items}


def normalize(obj: Any, *, _depth: int = 0) -> Any:
    """Recursively convert *obj* into JSON-serialisable primitives, redacting
    secrets and binary blobs on the way; never raises."""
    if _depth > _MAX_DEPTH:
        return f"<truncated:{type(obj).__name__}>"
    if isinstance(obj, str):
        return _scrub_text(obj)
    if isinstance(obj, (int, float, bool, type(None))):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: normalize(getattr(obj, f.name), _depth=_depth + 1) for f in dataclasses.fields(obj)}
    if should_skip(obj):
        return f"<{type(obj).__name__}>"
    if isinstance(obj, dict):
        return _normalize_items(obj.items(), _depth + 1)
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [normalize(item, _depth=_depth + 1) for item in obj]
    if isinstance(obj, bytes):
        return obj.hex() if len(obj) <= _MAX_BYTES_HEX else f"<bytes {len(obj)}>"
    if isinstance(obj, PurePath):
        return str(obj)
    # model_dump last: MagicMock exposes a callable one that returns more mocks.
    dumper = getattr(obj, "model_dump", None)
    if callable(dumper):
        try:
            dumped = dumper(exclude_none=True, mode="json")
        except TypeError:
            try:
                dumped = dumper()
            except Exception:
                return str(obj)
        except Exception:
            return str(obj)
        return normalize(dumped, _depth=_depth + 1) if isinstance(dumped, dict) else str(obj)
    if hasattr(obj, "__dict__"):
        try:
            items = vars(obj).items()
        except TypeError:
            return str(obj)
        return _normalize_items(((k, v) for k, v in items if not k.startswith("_")), _depth + 1)
    return str(obj)


def serialize(obj: Any) -> str:
    """JSON text of :func:`normalize`; unbounded; never raises."""
    try:
        return json.dumps(normalize(obj), ensure_ascii=False)
    except (TypeError, ValueError, OverflowError):
        return repr(obj)


def serialize_payload(obj: Any) -> tuple[str, bool]:
    """:func:`serialize` capped at :data:`MAX_PAYLOAD_BYTES`. Over budget the
    text is still valid JSON — the server parses payloads — holding the size
    and a preview. Returns ``(text, truncated)``."""
    text = serialize(obj)
    encoded = text.encode()
    if len(encoded) <= MAX_PAYLOAD_BYTES:
        return text, False
    preview = encoded[:_PREVIEW_BYTES].decode(errors="ignore")
    return json.dumps({"truncated": True, "bytes": len(encoded), "preview": preview}, ensure_ascii=False), True


def to_attribute(value: Any) -> bool | str | int | float | list[str]:
    """Coerce *value* to an OTel-legal attribute; rich values become bounded JSON."""
    if value is None:
        return ""
    if isinstance(value, (bool, str, int, float)):
        return value
    if isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value):
        return list(value)
    return serialize_payload(value)[0]


_MESSAGE_ROLE_MAP = {"human": "user", "ai": "assistant"}


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and part.get("text"):
                parts.append(str(part["text"]))
            elif getattr(part, "text", None):
                parts.append(str(part.text))
        return "\n".join(parts)
    return str(content)


def _field(obj: Any, *names: str) -> Any:
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def _parse_arguments(arguments: Any) -> Any:
    """Tool arguments arrive as a JSON string on the OpenAI wire; a string
    that is not JSON is kept as the model wrote it."""
    if isinstance(arguments, str):
        with contextlib.suppress(ValueError):
            return json.loads(arguments)
    return arguments


def _normalize_tool_call(call: Any) -> dict[str, Any]:
    """``{"id", "name", "arguments"}`` — the keys the server pairs tool
    results by — from the OpenAI ``function.{name,arguments}`` nesting, the
    flat LangChain ``{name,args}`` shape, or an Anthropic ``tool_use`` block."""
    function = _field(call, "function")
    if function is not None:
        name, arguments = _field(function, "name"), _field(function, "arguments")
    else:
        name, arguments = _field(call, "name"), _field(call, "arguments", "args", "input")
    return {"id": _field(call, "id"), "name": name, "arguments": _parse_arguments(arguments)}


def normalize_messages(messages: Any) -> list[dict[str, Any]]:
    """Normalise a chat-message list (dicts, OpenAI/Anthropic/LangChain
    objects) into ``{"role", "content"}`` entries with full text. Image and
    base64 parts are dropped; tool calls carried as ``tool_calls`` /
    ``tool_call_id`` when present."""
    out: list[dict[str, Any]] = []
    for message in messages or []:
        role = str(_field(message, "role", "type") or "user")
        content = _field(message, "text", "content") if not isinstance(message, dict) else message.get("content")
        entry: dict[str, Any] = {"role": _MESSAGE_ROLE_MAP.get(role, role), "content": _message_text(content)}
        if tool_calls := _field(message, "tool_calls"):
            entry["tool_calls"] = [_normalize_tool_call(call) for call in tool_calls]
        if tool_call_id := _field(message, "tool_call_id"):
            entry["tool_call_id"] = str(tool_call_id)
        out.append(entry)
    return out


class StreamedMessage:
    """Folds a generator's yielded items into the one assistant message they
    spell. Understands ``str`` deltas, anything :func:`normalize_messages`
    reads (dicts or objects with ``role``/``content``/``tool_calls``), and
    OpenAI-shaped chunks (``choices[0].delta``). Other items are counted.
    Text stops accumulating at the payload budget; ``count`` keeps going."""

    __slots__ = ("count", "truncated", "_text", "_text_bytes", "_tool_calls")

    def __init__(self) -> None:
        self.count = 0
        self.truncated = False
        self._text: list[str] = []
        self._text_bytes = 0
        self._tool_calls: dict[int | str, dict[str, Any]] = {}

    def add(self, item: Any) -> list[str]:
        """Fold one item; returns the tool-call ids first seen in it."""
        self.count += 1
        if isinstance(item, str):
            self._add_text(item)
            return []
        if (choices := _field(item, "choices")) and (delta := _field(choices[0], "delta")) is not None:
            return self._add_delta(delta)
        if _field(item, "content") is not None or _field(item, "tool_calls"):
            (message,) = normalize_messages([item])
            self._add_text(message["content"])
            return self._add_tool_calls(message.get("tool_calls", []))
        return []

    def message(self) -> dict[str, Any] | None:
        """``{"role": "assistant", "content", "tool_calls"?}``, or None when
        nothing recognisable was streamed."""
        if not self._text and not self._tool_calls:
            return None
        message: dict[str, Any] = {"role": "assistant", "content": "".join(self._text)}
        if self._tool_calls:
            message["tool_calls"] = [self._finished_call(call) for call in self._tool_calls.values()]
        return message

    def _add_text(self, text: str) -> None:
        if not text:
            return
        self._text_bytes += len(text.encode())
        if self._text_bytes > MAX_PAYLOAD_BYTES:
            self.truncated = True
            return
        self._text.append(text)

    def _add_delta(self, delta: Any) -> list[str]:
        self._add_text(_field(delta, "content") or "")
        new_ids: list[str] = []
        # Streamed tool calls arrive as fragments keyed by ``index``: the id
        # and name on the first fragment, the arguments spread across the rest.
        for fragment in _field(delta, "tool_calls") or []:
            key = _field(fragment, "index")
            call = self._tool_calls.setdefault(key if key is not None else len(self._tool_calls), {"arguments": ""})
            if (call_id := _field(fragment, "id")) and not call.get("id"):
                call["id"] = str(call_id)
                new_ids.append(str(call_id))
            function = _field(fragment, "function")
            if function is not None:
                if name := _field(function, "name"):
                    call["name"] = name
                call["arguments"] += _field(function, "arguments") or ""
        return new_ids

    def _add_tool_calls(self, calls: list[dict[str, Any]]) -> list[str]:
        new_ids: list[str] = []
        for call in calls:
            key = call["id"] or len(self._tool_calls)
            if key not in self._tool_calls:
                self._tool_calls[key] = call
                if call["id"]:
                    new_ids.append(str(call["id"]))
        return new_ids

    @staticmethod
    def _finished_call(call: dict[str, Any]) -> dict[str, Any]:
        return {"id": call.get("id"), "name": call.get("name"), "arguments": _parse_arguments(call.get("arguments"))}


def requested_tool_call_ids(output: Any) -> list[str]:
    """The tool-call ids an LLM call's *output* requested: a message list, a
    single message, or an OpenAI-shaped completion (``choices[0].message``)."""
    if (choices := _field(output, "choices")) and (message := _field(choices[0], "message")) is not None:
        output = message
    messages = output if isinstance(output, list) else [output]
    return [
        str(call["id"])
        for message in normalize_messages(messages)
        for call in message.get("tool_calls", [])
        if call.get("id")
    ]


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "StreamedMessage",
    "add_redact_keys",
    "normalize",
    "normalize_messages",
    "requested_tool_call_ids",
    "serialize",
    "serialize_payload",
    "should_skip",
    "to_attribute",
]
