"""Runtime eval envelope: parse ``overmind.eval.*`` span events into one
canonical dict per scoring unit.

Trust boundary — event payloads are user-code output. Malformed entries land in
``envelope["errors"]`` and never raise into ingest or scoring.
"""

from __future__ import annotations

import json
from typing import Any

from overbae.api import overmind_attrs as oc_attrs
from overbae.services.eval import chatml

SCHEMA_VERSION = 1
MAX_EXPECTATIONS = 64
MAX_PAYLOAD_BYTES = 16 * 1024

EXPECTATION_KINDS = frozenset({"contains", "regex", "schema", "constraint", "checkpoints"})
EXPECTATION_SCOPES = frozenset({"span", "trace", "conversation"})

_ENVELOPE_EVENT_NAMES = frozenset(
    {
        oc_attrs.EVAL_EVENT_EXPECTATION,
        oc_attrs.EVAL_EVENT_CONTEXT,
        oc_attrs.EVAL_EVENT_CHECKPOINT,
        oc_attrs.EVAL_EVENT_CONVERSATION_END,
        oc_attrs.EVAL_EVENT_INTENT,
    }
)

# Persisted onto ``EvalSample.envelope``.
ENVELOPE_KEYS = ("expectations", "context", "checkpoints", "conversation_end", "intent")


def empty_envelope() -> dict[str, Any]:
    return {
        "expectations": [],
        "context": {},
        "checkpoints": [],
        "conversation_end": False,
        "intent": None,
        "errors": [],
    }


def _error(out: dict[str, Any], span_id: str, event: str, reason: str) -> None:
    out["errors"].append({"span_id": span_id, "event": event, "reason": reason})


def _parse_payload(attrs: dict[str, Any]) -> tuple[Any, str | None]:
    version = attrs.get(oc_attrs.EVAL_SCHEMA_VERSION)
    try:
        if int(version) != SCHEMA_VERSION:
            return None, f"unsupported schema_version {version!r}"
    except (TypeError, ValueError):
        return None, f"unsupported schema_version {version!r}"

    raw = attrs.get(oc_attrs.EVAL_PAYLOAD)
    if not isinstance(raw, str):
        return None, "payload missing or not a string"
    if len(raw.encode("utf-8", errors="replace")) > MAX_PAYLOAD_BYTES:
        return None, f"payload exceeds {MAX_PAYLOAD_BYTES} bytes"
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None, "payload is not valid JSON"
    if not isinstance(payload, dict):
        return None, "payload is not a JSON object"
    return payload, None


def _validate_expectation(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    exp_id = payload.get("id")
    if not isinstance(exp_id, str) or not exp_id.strip():
        return None, "expectation missing id"
    kind = payload.get("kind")
    if kind not in EXPECTATION_KINDS:
        return None, f"unknown expectation kind {kind!r}"
    spec = payload.get("spec")
    if not isinstance(spec, (str, dict, list)) or spec in ("", [], {}):
        return None, "expectation missing spec"
    scope = payload.get("scope", "trace")
    if scope not in EXPECTATION_SCOPES:
        return None, f"unknown expectation scope {scope!r}"
    return {
        "id": exp_id.strip(),
        "kind": kind,
        "spec": spec,
        "scope": scope,
        "gate": bool(payload.get("gate", False)),
    }, None


def extract_envelope(spans: list) -> dict[str, Any]:
    """Pure over any objects carrying ``span_id``/``events``."""
    out = empty_envelope()

    events: list[tuple[int, str, dict[str, Any]]] = []
    for span in spans or []:
        span_id = str(getattr(span, "span_id", "") or "")
        for event in getattr(span, "events", None) or []:
            if not isinstance(event, dict):
                continue
            name = event.get("name")
            if name not in _ENVELOPE_EVENT_NAMES:
                continue
            ts = event.get("time_unix_nano") or 0
            try:
                ts = int(ts)
            except (TypeError, ValueError):
                ts = 0
            events.append((ts, span_id, event))
    events.sort(key=lambda item: item[0])

    seen_ids: set[str] = set()
    for _ts, span_id, event in events:
        name = event.get("name")
        attrs = event.get("attributes")
        if not isinstance(attrs, dict):
            _error(out, span_id, name, "event has no attributes")
            continue
        payload, reason = _parse_payload(attrs)
        if reason is not None:
            _error(out, span_id, name, reason)
            continue

        if name == oc_attrs.EVAL_EVENT_EXPECTATION:
            expectation, reason = _validate_expectation(payload)
            if reason is not None:
                _error(out, span_id, name, reason)
                continue
            if expectation["id"] in seen_ids:
                continue
            if len(out["expectations"]) >= MAX_EXPECTATIONS:
                _error(out, span_id, name, f"expectation cap ({MAX_EXPECTATIONS}) exceeded")
                continue
            seen_ids.add(expectation["id"])
            expectation["span_id"] = span_id
            out["expectations"].append(expectation)
        elif name == oc_attrs.EVAL_EVENT_CONTEXT:
            facts = payload.get("facts")
            if not isinstance(facts, dict):
                _error(out, span_id, name, "context missing facts object")
                continue
            # Later events win per key.
            out["context"].update({str(k): v for k, v in facts.items()})
        elif name == oc_attrs.EVAL_EVENT_CHECKPOINT:
            checkpoint = payload.get("name")
            if not isinstance(checkpoint, str) or not checkpoint.strip():
                _error(out, span_id, name, "checkpoint missing name")
                continue
            out["checkpoints"].append({"name": checkpoint.strip(), "span_id": span_id})
        elif name == oc_attrs.EVAL_EVENT_CONVERSATION_END:
            out["conversation_end"] = True
        elif name == oc_attrs.EVAL_EVENT_INTENT:
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                _error(out, span_id, name, "intent missing text")
                continue
            # Later declarations win.
            out["intent"] = {
                "text": text.strip(),
                "source": str(payload.get("source") or "declared"),
            }

    return out


def _parse_prompt_kwargs(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _rendered_prompt(template: str, kwargs: dict[str, Any], span) -> str:
    # PromptString renders via template.format(**kwargs), so re-rendering is exact.
    if template:
        try:
            return template.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            pass
    attrs = getattr(span, "attributes", None) or {}
    messages = chatml.parse_messages(chatml.pick(attrs, chatml.INPUT_KEYS)) or []
    system = next((m.get("content") or "" for m in messages if m.get("role") == "system"), "")
    if system:
        return system
    return next((m.get("content") or "" for m in messages if m.get("content")), "")


def prompt_records(llm_spans: list) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for span in llm_spans or []:
        attrs = getattr(span, "attributes", None) or {}
        template = attrs.get(oc_attrs.PROMPT_TEMPLATE)
        raw_kwargs = attrs.get(oc_attrs.PROMPT_KWARGS)
        if not isinstance(template, str) or not template:
            continue
        kwargs = _parse_prompt_kwargs(raw_kwargs)
        records.append(
            {
                "span_id": str(getattr(span, "span_id", "") or ""),
                "template": template,
                "kwargs": kwargs,
                "rendered": _rendered_prompt(template, kwargs, span),
            }
        )
    return records


def runtime_block(envelope: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Empty parts are omitted so envelope-less trajectories stay bit-for-bit unchanged."""
    runtime: dict[str, Any] = {}
    if envelope:
        for key in ENVELOPE_KEYS:
            value = envelope.get(key)
            if value:
                runtime[key] = value
        if envelope.get("errors"):
            runtime["envelope_errors"] = envelope["errors"]
    if records:
        runtime["prompt_records"] = records
    return runtime


# Stamped as eval_context facts; the judge reads them as grounding, not a contract.
CONVERSATION_CONTEXT_KEYS = ("conversation_id", "running_intent", "conversation_so_far")
_TURN_TEXT_CAP = 240
_MAX_PRIOR_TURNS = 12
_RUNNING_INTENT_CAP = 2000


def clip_text(text: str, cap: int = _TURN_TEXT_CAP) -> str:
    text = (text or "").strip()
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def _tools_from_parts(parts: list) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "activity" and part.get("phase") == "tool_done":
            name = str(part.get("tool") or "").strip()
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names[:8]


def compact_message_turn(message: Any) -> dict[str, Any] | None:
    role = str(getattr(message, "role", "") or "").strip() or "unknown"
    text = clip_text(str(getattr(message, "content", "") or ""))
    tools = _tools_from_parts(getattr(message, "parts", None) or [])
    if not text and not tools:
        return None
    turn: dict[str, Any] = {"role": role, "text": text}
    if tools:
        turn["tools"] = tools
    return turn


def compact_conversation(messages: Any, *, exclude_ids: Any = ()) -> list[dict[str, Any]]:
    """*messages* is ordered oldest-first."""
    skip = {str(i) for i in (exclude_ids or ())}
    turns: list[dict[str, Any]] = []
    for message in messages or []:
        pk = getattr(message, "pk", None)
        if pk is not None and str(pk) in skip:
            continue
        turn = compact_message_turn(message)
        if turn:
            turns.append(turn)
    if len(turns) <= _MAX_PRIOR_TURNS:
        return turns
    first_user = next((t for t in turns if t.get("role") == "user"), None)
    tail = turns[-(_MAX_PRIOR_TURNS - 1) :]
    if first_user is None or first_user in tail:
        return turns[-_MAX_PRIOR_TURNS:]
    return [first_user, *tail]


def parse_conversation_context(
    context: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], str, str]:
    context = context if isinstance(context, dict) else {}
    raw = context.get("conversation_so_far")
    if isinstance(raw, str) and raw.strip():
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = None
    turns: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and (item.get("text") or item.get("tools")):
                role = str(item.get("role") or "unknown")
                turn: dict[str, Any] = {
                    "role": role,
                    "text": clip_text(str(item.get("text") or "")),
                }
                tools = [str(t) for t in (item.get("tools") or []) if str(t).strip()]
                if tools:
                    turn["tools"] = tools[:8]
                if turn["text"] or turn.get("tools"):
                    turns.append(turn)
            elif isinstance(item, str) and item.strip():
                turns.append({"role": "unknown", "text": clip_text(item)})
    running = str(context.get("running_intent") or "").strip()
    conv_id = str(context.get("conversation_id") or "").strip()
    return turns[-_MAX_PRIOR_TURNS:], running, conv_id


def conversation_context_facts(
    *,
    conversation_id: str = "",
    running_intent: str = "",
    prior_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Strings only: OTel attribute values."""
    facts: dict[str, Any] = {}
    if conversation_id:
        facts["conversation_id"] = str(conversation_id)
    if running_intent:
        facts["running_intent"] = running_intent[:_RUNNING_INTENT_CAP]
    if prior_turns:
        facts["conversation_so_far"] = json.dumps(prior_turns, ensure_ascii=False)
    return facts
