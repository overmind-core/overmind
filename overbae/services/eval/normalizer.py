"""Span trees, uploaded ChatML and dataset-run outputs converge on one
canonical shape, consumed by every evaluator::

    {
      "modality": "single_turn | multi_turn | tool_calling",
      "messages": [ {role, content, tool_calls?, tool_call_id?}, ... ],
      "tool_definitions": [ {name, description, parameters}, ... ],
      "final_output": "<last assistant text, else the terminal tool-call args>",
      "metadata": {model, cost, tokens, latency_ms, truncated, ...}
    }

Trajectories live inline in Postgres; clipping sets ``metadata.truncated``.
"""

from __future__ import annotations

import json
import re
from typing import Any

from overbae.models.traces import is_tool_operation
from overbae.services.eval import chatml
from overbae.services.eval import envelope as eval_envelope

_MAX_MESSAGE_CHARS = 100_000
_MAX_TOTAL_CHARS = 2_000_000

# Some fine-tuning datasets write ``[Func(args)]`` or ``[FuncA(args), FuncB(args)]``
# as assistant text instead of structured ``tool_calls``.
_TEXT_TOOL_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9 _]{1,80})\(([^)]{0,500})\)\]")

_MULTI_TOOLACE_RE = re.compile(
    r"^\["
    r"[A-Za-z][A-Za-z0-9 _]{1,80}\([^)]{0,2000}\)"
    r"(?:,\s*[A-Za-z][A-Za-z0-9 _]{1,80}\([^)]{0,2000}\))*"
    r"\]$",
    re.DOTALL,
)

_INNER_CALL_RE = re.compile(r"([A-Za-z][A-Za-z0-9 _]{1,80})\(([^)]{0,2000})\)")

# Qwen (and some Unsloth templates) emit tool calls as XML in assistant text
# instead of OpenAI ``tool_calls``. Unclosed blocks are common at max-tokens.
_QWEN_TOOL_BLOCK_RE = re.compile(
    r"<tool_call>(.*?)(?:</tool_call>|$)",
    re.DOTALL | re.IGNORECASE,
)
_QWEN_FUNCTION_RE = re.compile(r"<function=([^>\s]+)>", re.IGNORECASE)
_QWEN_PARAM_RE = re.compile(
    r"<parameter=([^>\s]+)>\s*(.*?)\s*</parameter>",
    re.DOTALL | re.IGNORECASE,
)


def _is_multi_toolace(content: str) -> bool:
    return bool(_MULTI_TOOLACE_RE.match(content.strip()))


def _parse_qwen_xml_tool_calls(content: str) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for block in _QWEN_TOOL_BLOCK_RE.findall(content or ""):
        fn = _QWEN_FUNCTION_RE.search(block)
        if fn:
            name = fn.group(1).strip()
            if not name:
                continue
            args: dict[str, Any] = {}
            for key, value in _QWEN_PARAM_RE.findall(block):
                args[key.strip()] = value.strip()
            calls.append({"id": f"xml_{len(calls)}", "name": name, "arguments": args})
            continue
        parsed = chatml.maybe_parse_json(block.strip())
        if isinstance(parsed, dict) and str(parsed.get("name") or "").strip():
            raw_args = (
                parsed.get("arguments") if "arguments" in parsed else parsed.get("parameters")
            )
            if isinstance(raw_args, str):
                decoded = chatml.maybe_parse_json(raw_args)
                args = decoded if isinstance(decoded, dict) else {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            calls.append(
                {
                    "id": f"xml_{len(calls)}",
                    "name": str(parsed["name"]).strip(),
                    "arguments": args,
                }
            )
    return calls


def parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Bracket-form and Qwen XML calls in the chatml ``{id, name, arguments}`` shape."""

    def _parse_args(args_raw: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        for part in re.split(r",\s*(?=[A-Za-z_]\w*\s*=)", args_raw):
            part = part.strip()
            if "=" not in part:
                continue
            k, _, v = part.partition("=")
            args[k.strip()] = v.strip().strip("\"'")
        return args

    xml_calls = _parse_qwen_xml_tool_calls(content)
    if xml_calls:
        return xml_calls

    calls: list[dict[str, Any]] = []

    if _is_multi_toolace(content or ""):
        inner = (content or "").strip()[1:-1]  # strip outer [ ]
        for match in _INNER_CALL_RE.finditer(inner):
            calls.append(
                {
                    "id": f"text_{len(calls)}",
                    "name": match.group(1).strip(),
                    "arguments": _parse_args(match.group(2).strip()),
                }
            )
        return calls

    for match in _TEXT_TOOL_RE.finditer(content or ""):
        calls.append(
            {
                "id": f"text_{len(calls)}",
                "name": match.group(1).strip(),
                "arguments": _parse_args(match.group(2).strip()),
            }
        )
    return calls


def _has_text_tool_calls(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        if m.get("role") != "assistant":
            continue
        content = m.get("content") or ""
        if (
            _TEXT_TOOL_RE.search(content)
            or _is_multi_toolace(content)
            or "<tool_call>" in content.lower()
        ):
            return True
    return False


def _truncate(text: str, limit: int, flag: dict[str, bool]) -> str:
    if len(text) > limit:
        flag["truncated"] = True
        return text[:limit] + "\n…[truncated]"
    return text


def _detect_modality(messages: list[dict[str, Any]]) -> str:
    has_struct_tool = any(m.get("tool_calls") or m.get("role") == "tool" for m in messages)
    if has_struct_tool:
        return "tool_calling"
    if _has_text_tool_calls(messages):
        return "tool_calling"
    user_turns = sum(1 for m in messages if m.get("role") == "user")
    return "multi_turn" if user_turns > 1 else "single_turn"


def _is_tool_call_only_content(content: str) -> bool:
    """A tool-call-only message is a dispatch step, not a final response."""
    if not content:
        return False
    if _is_multi_toolace(content):
        return True
    remaining = _TEXT_TOOL_RE.sub("", content)
    remaining = re.sub(
        r"<tool_call>.*?(?:</tool_call>|$)", "", remaining, flags=re.DOTALL | re.IGNORECASE
    )
    return not any(c.isalpha() for c in remaining)


def _final_output(messages: list[dict[str, Any]]) -> str:
    """A real response beats a pure tool-call dispatch, which is only a fallback."""
    tool_call_fallback = ""
    for m in reversed(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content") or ""
        if m.get("tool_calls") and not content:
            continue
        if content:
            if not _is_tool_call_only_content(content):
                return content
            if not tool_call_fallback:
                tool_call_fallback = content
    if tool_call_fallback:
        return tool_call_fallback
    # Structured-output agents deliver through the terminal call's ARGUMENTS;
    # a ``role=="tool"`` result is the environment's payload, never the answer.
    last_dispatch = next(
        (m for m in reversed(messages) if m.get("role") == "assistant" and m.get("tool_calls")),
        None,
    )
    if last_dispatch:
        args = (last_dispatch["tool_calls"][-1] or {}).get("arguments")
        if isinstance(args, str):
            return "" if args.strip().lower() in ("", "{}", "[]", "null", "none") else args
        if args not in (None, {}, []):
            return json.dumps(args, default=str)
    return ""


def _apply_size_guard(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    flag = {"truncated": False}
    total = 0
    guarded: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            content = _truncate(content, _MAX_MESSAGE_CHARS, flag)
        total += len(content) if isinstance(content, str) else 0
        new_m = dict(m)
        new_m["content"] = content
        if total > _MAX_TOTAL_CHARS:
            flag["truncated"] = True
            break
        guarded.append(new_m)
    return guarded, flag["truncated"]


def _build(
    messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]] | None,
    metadata: dict[str, Any] | None,
    output_start: int | None = None,
) -> dict[str, Any]:
    guarded, truncated = _apply_size_guard(messages)
    meta = dict(metadata or {})
    meta["truncated"] = bool(meta.get("truncated")) or truncated
    # Without ``output_start`` an earlier assistant message from the context
    # wins over the model's actual new response.
    if output_start is not None:
        meta["output_start"] = min(output_start, len(guarded))
        output_slice = guarded[output_start:]
    else:
        output_slice = guarded
    return {
        "modality": _detect_modality(guarded),
        "messages": guarded,
        "tool_definitions": tool_definitions or [],
        "final_output": _final_output(output_slice),
        "metadata": meta,
    }


def _span_attrs(span) -> dict[str, Any]:
    return span.attributes or {}


def _is_llm(span) -> bool:
    return getattr(span, "span_type", "") == "llm_call"


def _is_tool(span) -> bool:
    return is_tool_operation(getattr(span, "span_type", ""))


def _root_span(ordered: list):
    """No parent, or a parent outside this trace's spans; else the earliest span."""
    local_ids = {getattr(s, "span_id", None) for s in ordered}
    return next(
        (
            s
            for s in ordered
            if getattr(s, "parent_span_id", None) in (None, "")
            or getattr(s, "parent_span_id", None) not in local_ids
        ),
        ordered[0],
    )


def _is_entry_point(span) -> bool:
    return getattr(span, "span_type", "") == "entry_point"


def _has_io_attrs(span) -> bool:
    attrs = _span_attrs(span)
    return (
        chatml.pick(attrs, chatml.INPUT_KEYS) is not None
        or chatml.pick(attrs, chatml.OUTPUT_KEYS) is not None
    )


def _io_source_span(ordered: list):
    """The SDK ``entry_point`` wins: a wrapping root often carries only ids, and
    lifting from it leaves every output judge variable empty."""
    entry = next((s for s in ordered if _is_entry_point(s) and _has_io_attrs(s)), None)
    if entry is not None:
        return entry
    root = _root_span(ordered)
    if _has_io_attrs(root):
        return root
    return next((s for s in ordered if _has_io_attrs(s)), root)


def _harness_final_output(ordered: list, model_final: str) -> str | None:
    """The assembled record a wrapper span returns; ``None`` for a single-layer
    chat. Only the ``entry_point`` or structural root qualifies: an intermediate
    span carrying I/O would flag a plain chat as two-layer."""
    io_source = _io_source_span(ordered)
    if io_source is None or _is_llm(io_source):
        return None
    if not (_is_entry_point(io_source) or io_source is _root_span(ordered)):
        return None
    harness_obj = chatml.maybe_parse_json(chatml.pick(_span_attrs(io_source), chatml.OUTPUT_KEYS))
    if harness_obj in (None, ""):
        return None
    harness_text = _as_text(harness_obj)
    if harness_text == (model_final or ""):
        return None
    return harness_text


def capability_io(spans: list) -> tuple[Any, Any]:
    """The wrapper span's raw I/O, not an intermediate LLM lane."""
    if not spans:
        return None, None
    ordered = sorted(spans, key=lambda s: getattr(s, "start_time_ns", 0) or 0)
    attrs = _span_attrs(_io_source_span(ordered))
    raw_in = chatml.maybe_parse_json(chatml.pick(attrs, chatml.INPUT_KEYS))
    raw_out = chatml.maybe_parse_json(chatml.pick(attrs, chatml.OUTPUT_KEYS))
    return raw_in, raw_out


def _llm_metadata(attrs: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": chatml.pick(attrs, chatml.MODEL_KEYS),
        "prompt_tokens": chatml.to_number(chatml.pick(attrs, chatml.PROMPT_TOKEN_KEYS)),
        "completion_tokens": chatml.to_number(chatml.pick(attrs, chatml.COMPLETION_TOKEN_KEYS)),
        "total_tokens": chatml.to_number(chatml.pick(attrs, chatml.TOTAL_TOKEN_KEYS)),
        "cost": chatml.to_number(chatml.pick(attrs, chatml.COST_KEYS)),
    }


def normalize_spans(spans: list, *, promote_harness: bool = True) -> dict[str, Any]:
    """``promote_harness`` swaps a two-layer capability's delivered answer into
    ``final_output``; the split is recorded on metadata either way."""
    if not spans:
        return _build([], [], {"source": "otel_span", "empty": True})

    ordered = sorted(spans, key=lambda s: getattr(s, "start_time_ns", 0) or 0)
    llm_spans = [s for s in ordered if _is_llm(s)]
    tool_spans = [s for s in ordered if _is_tool(s)]

    tool_definitions: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {"source": "otel_span"}
    if ordered:
        metadata["trace_id"] = getattr(ordered[0], "trace_id", "")

    messages: list[dict[str, Any]] = []

    if llm_spans:
        # Each LLM call carries the full prior history, so the LAST input is complete.
        last = llm_spans[-1]
        attrs = _span_attrs(last)
        in_msgs = chatml.parse_messages(chatml.pick(attrs, chatml.INPUT_KEYS))
        out_msgs = chatml.parse_messages(chatml.pick(attrs, chatml.OUTPUT_KEYS))
        if not (in_msgs or out_msgs):
            # Both require input history: output-only is the multi-lane
            # signature where the root/entry_point lift must win.
            sem_in, sem_out = chatml.parse_genai_semconv_messages(attrs)
            if sem_in:
                in_msgs, out_msgs = sem_in, sem_out
            else:
                idx_in, idx_out = chatml.parse_indexed_genai_messages(attrs)
                if idx_in:
                    in_msgs, out_msgs = idx_in, idx_out
        metadata["extraction"] = {"path": "chatml_messages" if (in_msgs or out_msgs) else "root_io"}

        # Without message lists the last LLM span is an arbitrary lane, so the
        # binary I/O is lifted from the root instead.
        lift_attrs = _span_attrs(_io_source_span(ordered)) if not (in_msgs or out_msgs) else attrs

        if in_msgs:
            messages.extend(in_msgs)
        else:
            raw_in = chatml.maybe_parse_json(chatml.pick(lift_attrs, chatml.INPUT_KEYS))
            if raw_in not in (None, ""):
                messages.append({"role": "user", "content": _as_text(raw_in)})

        if out_msgs:
            messages.extend(out_msgs)
        else:
            raw_out = chatml.maybe_parse_json(chatml.pick(lift_attrs, chatml.OUTPUT_KEYS))
            if raw_out not in (None, ""):
                messages.append({"role": "assistant", "content": _as_text(raw_out)})

        for s in llm_spans:
            defs = chatml.parse_tool_definitions(chatml.pick(_span_attrs(s), chatml.TOOL_DEF_KEYS))
            if defs:
                tool_definitions = defs
                break

        metadata.update({k: v for k, v in _llm_metadata(attrs).items() if v is not None})
    else:
        root = _io_source_span(ordered)
        attrs = _span_attrs(root)
        metadata["extraction"] = {"path": "root_io"}
        raw_in = chatml.maybe_parse_json(chatml.pick(attrs, chatml.INPUT_KEYS))
        raw_out = chatml.maybe_parse_json(chatml.pick(attrs, chatml.OUTPUT_KEYS))
        if raw_in not in (None, ""):
            messages.append({"role": "user", "content": _as_text(raw_in)})
        if raw_out not in (None, ""):
            messages.append({"role": "assistant", "content": _as_text(raw_out)})

    raw_tool_calls = [_tool_span_to_record(s) for s in tool_spans]
    normalized = _build(messages, tool_definitions, metadata)
    # The trace→dataset path extracts both surfaces from metadata.
    if llm_spans:
        model_final = normalized.get("final_output") or ""
        harness_text = _harness_final_output(ordered, model_final)
        if harness_text is not None:
            normalized["metadata"]["two_layer"] = True
            normalized["metadata"]["capability_output"] = harness_text
            normalized["metadata"]["model_output"] = model_final
            if promote_harness:
                idx = _last_assistant_index(normalized["messages"])
                if idx is not None:
                    normalized["messages"][idx] = {
                        **normalized["messages"][idx],
                        "content": harness_text,
                    }
                else:
                    normalized["messages"].append({"role": "assistant", "content": harness_text})
                normalized["final_output"] = harness_text
    normalized["_raw_tool_spans"] = raw_tool_calls
    return normalized


# Proxy span names; the real tool lives in the call's arguments.
_TOOL_WRAPPER_NAMES = frozenset(
    {"mcp", "mcp_call", "call_tool", "use_mcp_tool", "tool", "tool_call", "invoke_tool", "act"}
)
_INNER_TOOL_NAME_KEYS = ("toolName", "tool_name", "tool", "name")


def unwrap_tool_call(name: Any, arguments: Any) -> tuple[str, Any]:
    """A generic dispatch span makes every tool look like ``mcp`` and destroys
    the overlap with the declared vocabulary."""
    n = str(name or "").strip()
    if isinstance(arguments, dict) and (not n or n.lower() in _TOOL_WRAPPER_NAMES):
        for key in _INNER_TOOL_NAME_KEYS:
            inner = arguments.get(key)
            if isinstance(inner, str) and inner.strip():
                inner_args = arguments.get("args")
                return inner.strip(), inner_args if inner_args is not None else arguments
        # browser_use shape: ``{"action": {"navigate": {...}}}``.
        action = arguments.get("action")
        if isinstance(action, dict) and len(action) == 1:
            real_name, real_args = next(iter(action.items()))
            if isinstance(real_name, str) and real_name.strip():
                return real_name.strip(), real_args if real_args is not None else {}
    return n, arguments


def _tool_span_to_record(span) -> dict[str, Any]:
    attrs = _span_attrs(span)
    return {
        "span_id": getattr(span, "span_id", ""),
        "parent_span_id": getattr(span, "parent_span_id", None),
        "name": attrs.get("tool.name") or getattr(span, "name", "") or "",
        "arguments": chatml.maybe_parse_json(chatml.pick(attrs, chatml.INPUT_KEYS)),
        "result": chatml.maybe_parse_json(chatml.pick(attrs, chatml.OUTPUT_KEYS)),
        "error": attrs.get("tool.error") or "",
        "status_code": getattr(span, "status_code", 0),
        "start_time_ns": getattr(span, "start_time_ns", 0),
    }


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


# Shared by the landing and eval paths.


def _last_assistant_index(messages: list[dict[str, Any]]) -> int | None:
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.get("role") == "assistant" and (m.get("content") or m.get("tool_calls")):
            return i
    return None


def _as_tool_content(value: Any) -> str:
    if value is None:
        return ""
    from overbae.services.eval.span_evidence import unwrap_payload

    peeled = unwrap_payload(value)
    if peeled is None:
        return ""
    if isinstance(peeled, str):
        return peeled
    return json.dumps(peeled, default=str)


def inline_tool_spans(trajectory: dict[str, Any], raw_tool_spans: list[dict[str, Any]]) -> None:
    """In place; a no-op when the messages already carry tool calls, so it
    never double-counts."""
    usable = [r for r in raw_tool_spans if str(r.get("name") or "").strip()]
    if not usable:
        return
    messages = trajectory.get("messages") or []
    if any(m.get("tool_calls") or m.get("role") == "tool" for m in messages):
        return

    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    for i, r in enumerate(usable):
        call_id = str(r.get("span_id") or f"call_{i}")
        real_name, real_args = unwrap_tool_call(r.get("name"), r.get("arguments"))
        tool_calls.append(
            {
                "id": call_id,
                "name": real_name,
                "arguments": real_args if real_args is not None else {},
            }
        )
        result = r.get("result")
        content = _as_tool_content(result if result is not None else r.get("error"))
        tool_results.append({"role": "tool", "tool_call_id": call_id, "content": content})

    block = [{"role": "assistant", "content": "", "tool_calls": tool_calls}, *tool_results]
    final_idx = _last_assistant_index(messages)
    if final_idx is None:
        messages = [*messages, *block]
    else:
        messages = [*messages[:final_idx], *block, *messages[final_idx:]]

    trajectory["messages"] = messages
    trajectory["modality"] = "tool_calling"


def _tool_def_from_args(name: str, arguments: Any) -> dict[str, Any]:
    props: dict[str, Any] = {}
    if isinstance(arguments, dict):
        props = {str(k): {} for k in arguments}
    return {"name": name, "description": "", "parameters": {"type": "object", "properties": props}}


def synthesize_tool_definitions(
    trajectory: dict[str, Any], raw_tool_spans: list[dict[str, Any]] | None = None
) -> None:
    """Without a synthesized schema the replay runner advertises no tools."""
    if trajectory.get("tool_definitions"):
        return
    seen: dict[str, dict[str, Any]] = {}
    for m in trajectory.get("messages") or []:
        for tc in m.get("tool_calls") or []:
            name = str(tc.get("name") or "").strip()
            if name and name not in seen:
                seen[name] = _tool_def_from_args(name, tc.get("arguments"))
    for r in raw_tool_spans or []:
        name, args = unwrap_tool_call(r.get("name"), r.get("arguments"))
        name = str(name or "").strip()
        if name and name not in seen:
            seen[name] = _tool_def_from_args(name, args)
    if seen:
        trajectory["tool_definitions"] = list(seen.values())


def reconstruct_spans(spans: list, *, promote_harness: bool = True) -> dict[str, Any]:
    """``_raw_tool_spans`` is preserved for ``structure_trajectory``."""
    trajectory = normalize_spans(spans, promote_harness=promote_harness)
    raw = trajectory.get("_raw_tool_spans") or []
    inline_tool_spans(trajectory, raw)
    synthesize_tool_definitions(trajectory, raw)
    attach_runtime(trajectory, spans)
    from overbae.services.eval.span_evidence import build_span_tree

    tree = build_span_tree(spans)
    if tree:
        trajectory["span_tree"] = tree
    return trajectory


def attach_runtime(trajectory: dict[str, Any], spans: list) -> None:
    """In place; the key is absent when there is nothing to attach, so
    envelope-less trajectories stay bit-for-bit identical."""
    ordered = sorted(spans or [], key=lambda s: getattr(s, "start_time_ns", 0) or 0)
    runtime = eval_envelope.runtime_block(
        eval_envelope.extract_envelope(ordered),
        eval_envelope.prompt_records([s for s in ordered if _is_llm(s)]),
    )
    if runtime:
        trajectory["runtime"] = runtime


def normalize_messages(
    value: Any,
    tool_definitions: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {"source": "upload", **(metadata or {})}
    msgs = chatml.parse_messages(value) or []
    defs = tool_definitions or []
    if not defs:
        obj = chatml.maybe_parse_json(value)
        if isinstance(obj, dict):
            defs = chatml.parse_tool_definitions(obj.get("tools"))
    return _build(msgs, defs, meta)


def normalize_generation(
    input_value: Any,
    output_messages: list[dict[str, Any]],
    tool_definitions: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = {"source": "dataset_run", **(metadata or {})}
    in_msgs = chatml.parse_messages(input_value)
    if in_msgs is None:
        raw = chatml.maybe_parse_json(input_value)
        in_msgs = [{"role": "user", "content": _as_text(raw)}] if raw not in (None, "") else []
    normalized_output = [chatml.normalize_message(m) for m in output_messages]
    messages = [*in_msgs, *normalized_output]
    normalized = _build(messages, tool_definitions or [], meta, output_start=len(in_msgs))
    if request:
        request_messages, truncated = _apply_size_guard(request.get("messages", []))
        normalized["model_request"] = {
            "messages": request_messages,
            "tools": request.get("tools", []),
            "truncated": truncated,
        }
    return normalized


def structure_trajectory(normalized: dict[str, Any]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = normalized.get("messages", [])
    raw_tool_spans: list[dict[str, Any]] = normalized.get("_raw_tool_spans", [])

    turns = _segment_turns(messages)
    nodes, edges = _tool_graph(messages, raw_tool_spans)
    salient = _salient_steps(messages, nodes)
    total_tokens = sum(chatml.approx_tokens(m.get("content", "") or "") for m in messages)
    for n in nodes:
        total_tokens += chatml.approx_tokens(json.dumps(n.get("arguments"), default=str))
        total_tokens += chatml.approx_tokens(json.dumps(n.get("result"), default=str))

    return {
        "turns": turns,
        "tool_graph": {"nodes": nodes, "edges": edges},
        "salient_steps": salient,
        "approx_tokens": total_tokens,
        "num_messages": len(messages),
        "num_tool_calls": len(nodes),
        "num_turns": len(turns),
    }


def _segment_turns(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for idx, m in enumerate(messages):
        role = m.get("role")
        if role == "user":
            if current:
                turns.append(current)
            current = {"index": len(turns), "start": idx, "messages": [idx], "tool_calls": 0}
        else:
            if current is None:
                current = {"index": 0, "start": idx, "messages": [], "tool_calls": 0}
            current["messages"].append(idx)
            current["tool_calls"] += len(m.get("tool_calls", []) or [])
    if current:
        turns.append(current)
    return turns


def _tool_graph(
    messages: list[dict[str, Any]], raw_tool_spans: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Edges link each call to the most recent prior one: a conservative DAG."""
    results_by_id: dict[str, Any] = {}
    for m in messages:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            results_by_id[m["tool_call_id"]] = m.get("content")

    # Some datasets never key a tool result to a call_id.
    orphan_tool_results: list[str | None] = [
        m.get("content") for m in messages if m.get("role") == "tool" and not m.get("tool_call_id")
    ]
    orphan_cursor = 0

    raw_by_name: dict[str, list[dict[str, Any]]] = {}
    for r in raw_tool_spans:
        raw_by_name.setdefault(r.get("name", ""), []).append(r)

    nodes: list[dict[str, Any]] = []
    for m in messages:
        tool_calls_in_msg = m.get("tool_calls", []) or []
        if not tool_calls_in_msg and m.get("role") == "assistant":
            tool_calls_in_msg = parse_text_tool_calls(m.get("content") or "")
        if not tool_calls_in_msg:
            continue
        # Calls in one assistant message are parallel: each depends on the node
        # BEFORE the batch, not on each other.
        parent_step_id = f"step_{len(nodes) - 1}" if nodes else None
        for tc in tool_calls_in_msg:
            call_id = tc.get("id", "")
            name = tc.get("name", "")
            result = results_by_id.get(call_id)
            error = ""
            if result is None and raw_by_name.get(name):
                rec = raw_by_name[name].pop(0)
                result = rec.get("result")
                error = rec.get("error", "")
            if result is None and not call_id and orphan_cursor < len(orphan_tool_results):
                result = orphan_tool_results[orphan_cursor]
                orphan_cursor += 1
            node = {
                "id": f"step_{len(nodes)}",
                "tool": name,
                "arguments": tc.get("arguments", {}),
                "result": result,
                "error": error,
                "caused_state_change": _is_state_changing(name),
                "depends_on": [parent_step_id] if parent_step_id else [],
            }
            nodes.append(node)

    # A caller that skipped ``inline_tool_spans`` would otherwise collapse a
    # tool-heavy trace to zero nodes.
    if not nodes and raw_tool_spans:
        for r in raw_tool_spans:
            name, args = unwrap_tool_call(r.get("name"), r.get("arguments"))
            if not name:
                continue
            nodes.append(
                {
                    "id": f"step_{len(nodes)}",
                    "tool": name,
                    "arguments": args if args is not None else {},
                    "result": r.get("result"),
                    "error": r.get("error", "") or "",
                    "caused_state_change": _is_state_changing(name),
                    "depends_on": [f"step_{len(nodes) - 1}"] if nodes else [],
                }
            )

    edges = [{"from": d, "to": n["id"]} for n in nodes for d in n["depends_on"]]
    return nodes, edges


_STATE_CHANGE_HINTS = (
    "create",
    "update",
    "delete",
    "write",
    "send",
    "book",
    "post",
    "set",
    "insert",
    "pay",
)


def _is_state_changing(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    return any(h in name for h in _STATE_CHANGE_HINTS)


def _salient_steps(
    messages: list[dict[str, Any]], nodes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    salient: list[dict[str, Any]] = []
    for n in nodes:
        if n.get("error"):
            salient.append({"type": "tool_error", "ref": n["id"], "tool": n["tool"]})
        elif n.get("caused_state_change"):
            salient.append({"type": "state_change", "ref": n["id"], "tool": n["tool"]})
    final = _final_output(messages)
    if final:
        salient.append({"type": "final_answer", "ref": "final", "preview": final[:200]})
    return salient
