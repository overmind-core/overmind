"""The default judge surface is the execution graph with envelopes peeled, so
MCP/Cursor wrappers do not hide payloads."""

from __future__ import annotations

import json
from typing import Any

from overbae.models.traces import is_tool_operation
from overbae.services.eval import chatml

# Tree plus rubric, messages and grounding context must fit every panel
# judge's window; ``funnel.fit_prompt`` is the last-resort guard.
_NODE_OUT_CAP = 20_000
_TREE_CHAR_CAP = 240_000
_UNWRAP_DEPTH = 8

# Span payloads have no size contract — a browser agent exports whole DOM
# snapshots as one 70MB+ attribute. Parsing that into objects and re-dumping
# it on every judge render multiplies it across judge threads and OOM-kills
# the scoring worker, so anything past what downstream consumers can use
# (render clips nodes at _NODE_OUT_CAP, the environment corpus at 40k) is
# clipped before it ever parses.
_RAW_PAYLOAD_CAP = 100_000


def _bounded_payload(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _RAW_PAYLOAD_CAP:
        return value[:_RAW_PAYLOAD_CAP] + "…[payload truncated]"
    return value


def unwrap_payload(value: Any, *, _depth: int = 0) -> Any:
    """Peel Cursor/MCP envelopes until a payload or the original value remains."""
    if _depth >= _UNWRAP_DEPTH:
        return value
    parsed = chatml.maybe_parse_json(value)
    if parsed is not value and parsed is not None:
        return unwrap_payload(parsed, _depth=_depth + 1)
    if not isinstance(parsed, dict):
        return parsed
    if "value" in parsed and ("status" in parsed or "isError" in parsed):
        return unwrap_payload(parsed.get("value"), _depth=_depth + 1)
    content = parsed.get("content")
    if isinstance(content, list) and content:
        blocks = [_block_text(b) for b in content]
        texts = [t for t in blocks if t not in (None, "")]
        if len(texts) == 1:
            return unwrap_payload(texts[0], _depth=_depth + 1)
        if texts:
            return [unwrap_payload(t, _depth=_depth + 1) for t in texts]
        # Handing back the envelope would read as a non-empty output.
        if any(t == "" for t in blocks):
            return ""
    if isinstance(content, str) and content:
        return unwrap_payload(content, _depth=_depth + 1)
    text = parsed.get("text")
    if isinstance(text, dict) and "text" in text:
        return unwrap_payload(text.get("text"), _depth=_depth + 1)
    if isinstance(text, str) and text and set(parsed) <= {"text", "type"}:
        return unwrap_payload(text, _depth=_depth + 1)
    return parsed


def _block_text(block: Any) -> Any:
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return None
    text = block.get("text")
    if isinstance(text, str):
        return text
    if isinstance(text, dict) and "text" in text:
        return text.get("text")
    if isinstance(block.get("content"), str):
        return block["content"]
    return None


def build_span_tree(spans: list) -> list[dict[str, Any]]:
    """Roots first."""
    from overbae.services.eval.normalizer import unwrap_tool_call

    nodes: dict[str, dict[str, Any]] = {}
    for span in spans or []:
        sid = str(getattr(span, "span_id", "") or "")
        if not sid:
            continue
        attrs = getattr(span, "attributes", None) or {}
        if not isinstance(attrs, dict):
            attrs = {}
        raw_name = str(attrs.get("tool.name") or getattr(span, "name", "") or "")
        args = chatml.maybe_parse_json(_bounded_payload(chatml.pick(attrs, chatml.INPUT_KEYS)))
        result = unwrap_payload(
            chatml.maybe_parse_json(_bounded_payload(chatml.pick(attrs, chatml.OUTPUT_KEYS)))
        )
        tool, inner_args = unwrap_tool_call(raw_name.split(".")[-1], args)
        dur_ns = getattr(span, "duration_ns", None) or 0
        start_ns = getattr(span, "start_time_ns", None) or 0
        nodes[sid] = {
            "id": sid,
            "name": str(getattr(span, "name", "") or raw_name),
            "tool": tool or None,
            "type": str(getattr(span, "span_type", "") or ""),
            "parent": getattr(span, "parent_span_id", None) or None,
            "start_time_ns": start_ns,
            "status": attrs.get("overmind.status") or getattr(span, "status_code", 0),
            "duration_ms": round(dur_ns / 1e6, 1) if dur_ns else 0,
            "code": {
                "namespace": attrs.get("code.namespace") or attrs.get("code.namespace.name"),
                "function": attrs.get("code.function") or attrs.get("code.function.name"),
            },
            "inputs": inner_args if inner_args is not None else args,
            "outputs": result,
            "children": [],
        }
    roots: list[dict[str, Any]] = []
    for node in nodes.values():
        parent = node["parent"]
        if parent and parent in nodes:
            nodes[parent]["children"].append(node)
        else:
            roots.append(node)

    def sort_children(node: dict[str, Any]) -> None:
        children = node.get("children") or []
        if children:
            children.sort(key=lambda c: c.get("start_time_ns") or 0)
            for child in children:
                sort_children(child)

    for root in roots:
        sort_children(root)
    roots.sort(key=lambda n: n.get("start_time_ns") or 0)
    return roots


def render_span_tree(tree: list[dict[str, Any]] | None, *, cap: int = _TREE_CHAR_CAP) -> str:
    if not tree:
        return ""
    lines: list[str] = []

    def walk(node: dict[str, Any], depth: int) -> None:
        pad = "  " * depth
        tool = node.get("tool")
        label = f"{node.get('name')}"
        if tool and tool not in (label, label.rsplit(".", 1)[-1]):
            label = f"{label} → {tool}"
        bits = [label, str(node.get("type") or "span")]
        if node.get("duration_ms"):
            bits.append(f"{node['duration_ms']}ms")
        if node.get("status") not in (None, "", 0, "success"):
            bits.append(str(node["status"]))
        lines.append(f"{pad}- {' · '.join(bits)}")
        inputs = node.get("inputs")
        outputs = node.get("outputs")
        if inputs not in (None, "", {}, []):
            lines.append(f"{pad}  in: {_clip_node(inputs)}")
        if outputs not in (None, "", {}, []):
            lines.append(f"{pad}  out: {_clip_node(outputs)}")
        elif outputs in ("", {}, []) and is_tool_operation(node.get("type")):
            lines.append(f"{pad}  out: (empty)")
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for root in tree:
        walk(root, 0)
    text = "\n".join(lines)
    if len(text) > cap:
        return text[: cap - 16] + "\n…[span tree truncated]"
    return text


_IDENTITY_KEYS = ("intent", "kind", "purpose")
_IDENTITY_NAME_KEYS = ("name", "id", "slug", "title")


def produced_identity_fields(tree: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Surfaced apart from the tree so kind/intent survives tree caps; no value
    vocabulary, the judge compares them to the ask."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def consider(obj: dict[str, Any]) -> None:
        ident = {k: obj[k] for k in _IDENTITY_KEYS if obj.get(k) not in (None, "")}
        if not ident:
            return
        for extra in _IDENTITY_NAME_KEYS:
            if obj.get(extra) not in (None, ""):
                ident[extra] = obj[extra]
                break
        key = json.dumps(ident, sort_keys=True, default=str)
        if key in seen:
            return
        seen.add(key)
        found.append(ident)

    def walk_value(value: Any, depth: int = 0) -> None:
        if depth > 6 or len(found) >= 8:
            return
        if isinstance(value, dict):
            consider(value)
            for item in value.values():
                walk_value(item, depth + 1)
        elif isinstance(value, list):
            for item in value[:12]:
                walk_value(item, depth + 1)

    def walk_node(node: dict[str, Any]) -> None:
        walk_value(node.get("outputs"))
        walk_value(node.get("inputs"))
        for child in node.get("children") or []:
            walk_node(child)

    for root in tree or []:
        if isinstance(root, dict):
            walk_node(root)
    return found


def _clip_node(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        rows = value["rows"]
        keys = list(rows[0]) if rows and isinstance(rows[0], dict) else []
        extra = {k: v for k, v in value.items() if k != "rows"}
        summary = {"n": len(rows), "keys": keys, **extra}
        value = summary
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) > _NODE_OUT_CAP:
        return text[: _NODE_OUT_CAP - 3] + "..."
    return text
