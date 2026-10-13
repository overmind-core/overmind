"""Shared ChatML parsing helpers.

Key priority and tool-call parsing must match what the trace UI and OTLP ingest
do (``api/views.py::_pick``, ``api/otlp.py``, the frontend ``transformSpan`` and
span message parsing), so the normalizer reconstructs exactly what users see.
"""

from __future__ import annotations

import json
import re
from typing import Any

# OpenAI, Anthropic and OpenRouter all enforce ``^[a-zA-Z0-9_-]{1,128}$`` on
# ``tools[].name``, but reconstructed/MCP names routinely carry dots, colons,
# spaces or slashes — so any name advertised to a model goes through
# :func:`sanitize_tool_name` first.
_TOOL_NAME_INVALID = re.compile(r"[^A-Za-z0-9_-]")
_TOOL_NAME_MAX_LEN = 128


def sanitize_tool_name(raw: Any) -> str:
    """Idempotent — an already-valid name is returned unchanged."""
    cleaned = _TOOL_NAME_INVALID.sub("_", str(raw or "").strip())[:_TOOL_NAME_MAX_LEN]
    return cleaned or "tool"


# Attribute keys carrying span input / output, in resolution priority order.
INPUT_KEYS = (
    "overmind.input.data",
    "overmind.input_data",
    "traceloop.entity.input",
    "inputs",
)
OUTPUT_KEYS = (
    "overmind.output.data",
    "overmind.output_data",
    "traceloop.entity.output",
    "outputs",
)

# OTel GenAI semconv (opentelemetry-instrumentation-* ≥0.6x): one JSON attr each.
GENAI_SEMCONV_INPUT_KEY = "gen_ai.input.messages"
GENAI_SEMCONV_OUTPUT_KEY = "gen_ai.output.messages"

# Model / usage / cost keys (primary then fallback families).
MODEL_KEYS = ("genai.model", "gen_ai.request.model", "gen_ai.response.model", "llm.model")
PROMPT_TOKEN_KEYS = (
    "genai.usage.prompt_tokens",
    "genai.prompt_tokens",
    "gen_ai.usage.input_tokens",
    "llm.usage.prompt_tokens",
)
COMPLETION_TOKEN_KEYS = (
    "genai.usage.completion_tokens",
    "genai.completion_tokens",
    "gen_ai.usage.output_tokens",
    "llm.usage.completion_tokens",
)
TOTAL_TOKEN_KEYS = ("genai.usage.total_tokens", "genai.total_tokens", "llm.usage.total_tokens")
COST_KEYS = ("genai.cost", "cost", "response_cost", "gen_ai.usage.cost")
TOOL_DEF_KEYS = ("available_tools", "genai.tools", "tools")


def maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in '{["':
        return value
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return value


def pick(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """First NON-EMPTY value among *keys*, in order."""
    for k in keys:
        if k in attrs and attrs[k] not in (None, ""):
            return attrs[k]
    return None


def to_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def is_message_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and isinstance(value[0], dict)
        and "role" in value[0]
    )


def parse_messages(value: Any) -> list[dict[str, Any]] | None:
    parsed = maybe_parse_json(value)
    if is_message_list(parsed):
        return [normalize_message(m) for m in parsed]
    # Some producers wrap messages under a "messages" key.
    if isinstance(parsed, dict) and is_message_list(parsed.get("messages")):
        return [normalize_message(m) for m in parsed["messages"]]
    # OpenAI response envelopes ({choices: [{message|delta}]}, incl. streaming).
    envelope = parse_response_envelope(parsed)
    if envelope is not None:
        return envelope
    return None


def parse_response_envelope(value: Any) -> list[dict[str, Any]] | None:
    """Handles ``{"choices": [{"message": {...}}]}`` and streamed chunk lists
    whose choices carry ``delta`` fragments — deltas accumulate by concatenating
    content and merging tool_calls by index."""
    chunks = value if isinstance(value, list) else [value]
    if not chunks or not all(
        isinstance(c, dict) and isinstance(c.get("choices"), list) and c["choices"] for c in chunks
    ):
        return None

    first_choice = chunks[0]["choices"][0]
    if isinstance(first_choice, dict) and isinstance(first_choice.get("message"), dict):
        return [normalize_message(chunks[0]["choices"][0]["message"])]

    if not (isinstance(first_choice, dict) and isinstance(first_choice.get("delta"), dict)):
        return None
    role = "assistant"
    content_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}
    for chunk in chunks:
        delta = chunk["choices"][0].get("delta")
        if not isinstance(delta, dict):
            return None
        if isinstance(delta.get("role"), str):
            role = delta["role"]
        if isinstance(delta.get("content"), str):
            content_parts.append(delta["content"])
        for fragment in delta.get("tool_calls") or []:
            if not isinstance(fragment, dict):
                continue
            index = fragment.get("index", 0)
            slot = tool_calls.setdefault(
                index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
            )
            if fragment.get("id"):
                slot["id"] = fragment["id"]
            fn = fragment.get("function") or {}
            if fn.get("name"):
                slot["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                slot["function"]["arguments"] += fn["arguments"]
    message: dict[str, Any] = {"role": role, "content": "".join(content_parts)}
    if tool_calls:
        message["tool_calls"] = [tool_calls[i] for i in sorted(tool_calls)]
    return [normalize_message(message)]


def parse_indexed_genai_messages(
    attrs: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """OpenLLMetry indexed attributes: ``gen_ai.prompt.{N}.role/.content`` (plus
    ``.tool_calls.{M}.*``) and ``gen_ai.completion.{N}.*``, sorted by index.
    ``(None, None)`` when the span carries no indexed message attributes."""

    def _collect(prefix: str) -> list[dict[str, Any]] | None:
        by_index: dict[int, dict[str, Any]] = {}
        calls: dict[int, dict[int, dict[str, Any]]] = {}
        for key, value in attrs.items():
            if not isinstance(key, str) or not key.startswith(prefix):
                continue
            parts = key[len(prefix) :].split(".")
            if not parts or not parts[0].isdigit():
                continue
            index = int(parts[0])
            field = parts[1] if len(parts) > 1 else ""
            if field in ("role", "content"):
                by_index.setdefault(index, {})[field] = value
            elif field == "tool_calls" and len(parts) >= 4 and parts[2].isdigit():
                call = calls.setdefault(index, {}).setdefault(
                    int(parts[2]), {"type": "function", "function": {}}
                )
                leaf = parts[3]
                if leaf == "id":
                    call["id"] = value
                elif leaf in ("name", "arguments"):
                    call["function"][leaf] = value
        if not by_index and not calls:
            return None
        messages = []
        for index in sorted(set(by_index) | set(calls)):
            msg = dict(by_index.get(index, {}))
            msg.setdefault(
                "role", "assistant" if prefix.startswith("gen_ai.completion") else "user"
            )
            if index in calls:
                msg["tool_calls"] = [calls[index][i] for i in sorted(calls[index])]
            messages.append(normalize_message(msg))
        return messages

    return _collect("gen_ai.prompt."), _collect("gen_ai.completion.")


def _parts_to_message(role: str, parts: list[Any]) -> dict[str, Any]:
    """OTel GenAI ``parts`` list → a single ChatML message."""
    content_chunks: list[str] = []
    tool_calls_raw: list[dict[str, Any]] = []
    tool_call_id: str | None = None
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text" or ptype in ("refusal", "reasoning"):
            if isinstance(part.get("content"), str):
                content_chunks.append(part["content"])
        elif ptype == "tool_call":
            tool_calls_raw.append(
                {
                    "id": part.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": part.get("name", ""),
                        "arguments": part.get("arguments", {}),
                    },
                }
            )
        elif ptype == "tool_call_response":
            tool_call_id = str(part.get("id") or tool_call_id or "")
            response = part.get("response")
            if response is not None:
                content_chunks.append(
                    response if isinstance(response, str) else json.dumps(response, default=str)
                )
    msg: dict[str, Any] = {
        "role": role,
        "content": "\n".join(content_chunks) if content_chunks else "",
    }
    if tool_calls_raw:
        msg["tool_calls"] = tool_calls_raw
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    return normalize_message(msg)


def _parse_semconv_payload(value: Any) -> list[dict[str, Any]] | None:
    """Parse ``gen_ai.input.messages`` / ``gen_ai.output.messages`` JSON."""
    parsed = maybe_parse_json(value)
    if not isinstance(parsed, list) or not parsed:
        return None
    messages: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        messages.append(_parts_to_message(str(item.get("role", "user")), parts))
    return messages or None


def parse_genai_semconv_messages(
    attrs: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]] | None]:
    """The parts-based schema current ``opentelemetry-instrumentation-*``
    packages emit. ``(None, None)`` when neither key is present."""
    in_msgs = _parse_semconv_payload(attrs.get(GENAI_SEMCONV_INPUT_KEY))
    out_msgs = _parse_semconv_payload(attrs.get(GENAI_SEMCONV_OUTPUT_KEY))
    return in_msgs, out_msgs


def normalize_tool_calls(raw: Any) -> list[dict[str, Any]]:
    """Flattens OpenAI/Anthropic shapes into ``{id, name, arguments}``, with
    ``arguments`` parsed into an object whenever possible."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for i, tc in enumerate(raw):
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
        name = (fn or {}).get("name") or tc.get("name") or ""
        args = (fn or {}).get("arguments") if fn else tc.get("arguments")
        # Anthropic tool_use blocks carry args under "input".
        if args is None and tc.get("type") == "tool_use":
            args = tc.get("input")
        parsed_args = maybe_parse_json(args) if args is not None else {}
        out.append(
            {
                "id": tc.get("id") or tc.get("tool_call_id") or f"call_{i}",
                "name": name,
                "arguments": parsed_args,
            }
        )
    return out


def _wire_arguments(args: Any) -> str:
    """Tool-call arguments as the JSON string chat templates index as a mapping. A
    bare string the model emitted (not JSON) is kept under ``input``."""
    if isinstance(args, str):
        parsed = maybe_parse_json(args)
        if isinstance(parsed, (dict, list)):
            return args
        return json.dumps({"input": args}, ensure_ascii=False)
    if args is None:
        return "{}"
    if isinstance(args, (dict, list)):
        return json.dumps(args, ensure_ascii=False, default=str)
    return json.dumps({"input": args}, ensure_ascii=False, default=str)


def openai_wire_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalised messages in the OpenAI wire shape fine-tuning validates: every tool
    call as ``{id, type: "function", function: {name, arguments}}`` with ``arguments``
    a JSON string. Text turns pass through unchanged."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        calls = m.get("tool_calls")
        if isinstance(calls, list) and calls:
            wired = []
            for i, tc in enumerate(calls):
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
                name = (fn or {}).get("name") or tc.get("name") or ""
                args = (fn or {}).get("arguments") if fn else tc.get("arguments")
                wired.append(
                    {
                        "id": str(tc.get("id") or f"call_{i}"),
                        "type": "function",
                        "function": {"name": str(name), "arguments": _wire_arguments(args)},
                    }
                )
            m = {**m, "tool_calls": wired}
            if m.get("content") is None:
                m["content"] = ""
        out.append(m)
    return out


def openai_wire_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tool definitions as ``{type: "function", function: {name, description, parameters}}``."""
    out: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        if not fn.get("name"):
            continue
        out.append(
            {
                "type": "function",
                "function": {
                    "name": str(fn["name"]),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters")
                    if isinstance(fn.get("parameters"), dict)
                    else {"type": "object", "properties": {}},
                },
            }
        )
    return out


def normalize_message(msg: Any) -> dict[str, Any]:
    if not isinstance(msg, dict):
        return {"role": "user", "content": str(msg)}
    out: dict[str, Any] = {
        "role": msg.get("role", "user"),
        "content": _stringify_content(msg.get("content")),
    }
    if msg.get("tool_calls"):
        out["tool_calls"] = normalize_tool_calls(msg["tool_calls"])
    # Anthropic-style content blocks may embed tool_use entries.
    elif isinstance(msg.get("content"), list):
        tool_uses = [
            c for c in msg["content"] if isinstance(c, dict) and c.get("type") == "tool_use"
        ]
        if tool_uses:
            out["tool_calls"] = normalize_tool_calls(tool_uses)
    if msg.get("tool_call_id"):
        out["tool_call_id"] = msg["tool_call_id"]
    if msg.get("name"):
        out["name"] = msg["name"]
    return out


def _stringify_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    # Multimodal / content-block arrays: concatenate text parts.
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif block.get("type") == "text" and isinstance(block.get("content"), str):
                    parts.append(block["content"])
        if parts:
            return "\n".join(parts)
    return json.dumps(content, default=str)


def parse_tool_definitions(value: Any) -> list[dict[str, Any]]:
    """Normalize a tool-definition list into ``[{name, description, parameters}]``."""
    parsed = maybe_parse_json(value)
    if not isinstance(parsed, list):
        return []
    out: list[dict[str, Any]] = []
    for t in parsed:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        name = fn.get("name") or ""
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {}),
            }
        )
    return out


def approx_tokens(text: str) -> int:
    """~4 chars/token — avoids a tokenizer dependency."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def messages_text(messages: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content", "")
        chunks.append(f"{role}: {content}")
        for tc in m.get("tool_calls", []) or []:
            chunks.append(
                f"  tool_call {tc.get('name')}({json.dumps(tc.get('arguments'), default=str)})"
            )
    return "\n".join(chunks)
