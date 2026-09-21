from __future__ import annotations

import json
import logging
import time
from collections.abc import Generator, Iterator
from typing import Any

import openai

from overbae.core.llms import RETRY_DEADLINE_INTERACTIVE, ToolStreamResult, stream_llm_tools
from overbae.core.model_registry import Engine as EngineChoice
from overbae.models import Dataset
from overbae.services.datasets.notebook.engines import Outcome

logger = logging.getLogger(__name__)

MAX_ROUNDS = 60
MAX_TOKENS = 8000
REASONING_EFFORT = "medium"
STREAM_ATTEMPTS = 3
CONTEXT_CHARS = 240_000
HISTORY_CHARS = 120_000
COMPACT_RESULT_CHARS = 600
MAX_RESULT_CHARS = 16_000
DELTA_FLUSH_SECONDS = 0.06
WRAP_UP = (
    "The tool budget for this turn is used up. Stop calling tools and write the result "
    'now, in the shape "How you write" gives. Say in one line what is left undone.'
)


def parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        from overbae.core.llms import try_json_parsing

        try:
            parsed = try_json_parsing(raw or "{}")
        except ValueError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _chars(message: dict[str, Any]) -> int:
    return len(json.dumps(message, ensure_ascii=False, default=str))


def _compact(message: dict[str, Any], chars: int) -> dict[str, Any]:
    content = message.get("content")
    if message.get("role") != "tool" or not isinstance(content, str) or len(content) <= chars:
        return message
    return {**message, "content": content[:chars] + f"…[+{len(content) - chars} chars compacted]"}


def fit(exchange: list[dict[str, Any]], *, keep_from: int, budget: int) -> list[dict[str, Any]]:
    out = list(exchange)
    sizes = [_chars(m) for m in out]
    total = sum(sizes)
    if total <= budget:
        return out
    for i, message in enumerate(out):
        if message.get("role") == "tool":
            out[i] = _compact(message, COMPACT_RESULT_CHARS)
            total += _chars(out[i]) - sizes[i]
            sizes[i] = _chars(out[i])
            if total <= budget:
                return out
    # Rounds drop whole: a tool result without its call is a 400.
    while keep_from > 0 and total > budget:
        drop = 1
        if out[0].get("role") == "assistant" and out[0].get("tool_calls"):
            while drop < len(out) and out[drop].get("role") == "tool":
                drop += 1
        drop = min(drop, keep_from)
        total -= sum(sizes[:drop])
        out, sizes, keep_from = out[drop:], sizes[drop:], keep_from - drop
    return out


def _without_reasoning(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {k: v for k, v in m.items() if k != "reasoning_details"} if "reasoning_details" in m else m
        for m in messages
    ]


def storable(exchange: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stripped = _without_reasoning(exchange)
    return fit(stripped, keep_from=len(stripped), budget=HISTORY_CHARS)


def result_payload(result: Any) -> str:
    payload = json.dumps(result, ensure_ascii=False, default=str)
    if len(payload) <= MAX_RESULT_CHARS or not isinstance(result, dict):
        return payload[:MAX_RESULT_CHARS]
    trimmed = dict(result)
    while len(payload) > MAX_RESULT_CHARS:
        key = max(
            (k for k, v in trimmed.items() if isinstance(v, list) and len(v) > 1),
            key=lambda k: len(json.dumps(trimmed[k], default=str)),
            default=None,
        )
        if key is None:
            break
        items = trimmed[key]
        kept = max(1, len(items) // 2)
        trimmed[key] = items[:kept]
        trimmed["truncated"] = f"{key}: {kept} of {len(result[key])} shown"
        payload = json.dumps(trimmed, ensure_ascii=False, default=str)
    return payload[:MAX_RESULT_CHARS]


def system_message(text: str) -> dict[str, Any]:
    return {
        "role": "system",
        "content": [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}],
    }


class _Attempt:
    def __init__(self, engine: NativeEngine, tools: Any, pending: list[dict[str, Any]]) -> None:
        self.engine = engine
        self.tools = tools
        self.pending = pending
        self.flushed = False
        self.buffer: list[str] = []
        self.thoughts: list[str] = []
        self.flushed_at = time.monotonic()

    def flush(self) -> Iterator[dict[str, Any]]:
        self.flushed_at = time.monotonic()
        if self.thoughts:
            self.tools.thought("".join(self.thoughts))
            self.thoughts.clear()
        if self.buffer:
            self.flushed = True
            self.tools.respond("".join(self.buffer))
            self.buffer.clear()
        while self.pending:
            yield self.pending.pop(0)

    def run(
        self,
        system: dict[str, Any],
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
    ) -> Generator[dict[str, Any], None, ToolStreamResult]:
        choice = self.engine.choice
        for item in stream_llm_tools(
            [system, *messages],
            schemas,
            model=choice.model,
            fallback_models=list(choice.models),
            max_tokens=MAX_TOKENS,
            retry_deadline=RETRY_DEADLINE_INTERACTIVE,
            reasoning_effort=REASONING_EFFORT if schemas else None,
            provider=choice.provider,
        ):
            if isinstance(item, ToolStreamResult):
                yield from self.flush()
                return item
            if item.kind == "reasoning":
                self.thoughts.append(item.text)
            else:
                if self.thoughts:
                    self.tools.thought("".join(self.thoughts))
                    self.thoughts.clear()
                self.tools.stop_thinking()
                self.buffer.append(item.text)
            if time.monotonic() - self.flushed_at >= DELTA_FLUSH_SECONDS:
                yield from self.flush()
        raise RuntimeError("The stream ended without a result.")


def _add_stats(totals: dict[str, Any], stats: dict[str, Any]) -> None:
    totals["served_model"] = stats.get("served_model") or totals.get("served_model")
    for key, value in stats.items():
        if isinstance(value, int | float):
            totals[key] = totals.get(key, 0) + value


class NativeEngine:
    def __init__(self, choice: EngineChoice) -> None:
        self.choice = choice
        self.name = choice.provider.name

    def _round(
        self,
        system: dict[str, Any],
        messages: list[dict[str, Any]],
        schemas: list[dict[str, Any]],
        tools: Any,
        pending: list[dict[str, Any]],
    ) -> Generator[dict[str, Any], None, ToolStreamResult]:
        for attempt in range(1, STREAM_ATTEMPTS + 1):
            current = _Attempt(self, tools, pending)
            try:
                result = yield from current.run(system, messages, schemas)
                if result.text and not current.flushed:
                    tools.respond(result.text)
                    while pending:
                        yield pending.pop(0)
                return result
            except Exception as exc:
                yield from current.flush()
                carried_reasoning = any("reasoning_details" in m for m in messages)
                if carried_reasoning:
                    messages[:] = _without_reasoning(messages)
                if not (carried_reasoning or not current.flushed) or attempt == STREAM_ATTEMPTS:
                    raise exc
                logger.warning("native engine: stream attempt %d failed", attempt, exc_info=True)
        raise RuntimeError("unreachable")

    def run(
        self, dataset: Dataset, message: str, tools: Any, pending: list[dict[str, Any]]
    ) -> Generator[dict[str, Any], None, Outcome]:
        from overbae.services.datasets.notebook.agent import system_prompt, tool_schemas

        handlers = tools.handlers()
        schemas = tool_schemas()
        system = system_message(system_prompt(dataset))
        history = list(dataset.agent_messages) if isinstance(dataset.agent_messages, list) else []
        messages = [*history, {"role": "user", "content": message}]
        keep_from = len(history)
        outcome = Outcome()

        for _ in range(MAX_ROUNDS):
            tools.think()
            messages[:] = fit(messages, keep_from=keep_from, budget=CONTEXT_CHARS)
            result = yield from self._round(system, messages, schemas, tools, pending)
            _add_stats(outcome.stats, result.stats)
            messages.append(result.assistant_message())
            if not result.tool_calls:
                break
            for call in result.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                handler = handlers.get(name)
                if handler is None:
                    result_value: Any = {"ok": False, "error": f"No tool {name}."}
                else:
                    result_value = handler(parse_args(function.get("arguments")))
                while pending:
                    yield pending.pop(0)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "content": result_payload(result_value),
                    }
                )
        else:
            messages.append({"role": "user", "content": WRAP_UP})
            tools.think()
            try:
                result = yield from self._round(system, messages, [], tools, pending)
                _add_stats(outcome.stats, result.stats)
                messages.append(result.assistant_message())
            except Exception:  # noqa: BLE001
                logger.warning("dataset %s: wrap-up failed", dataset.id, exc_info=True)
            outcome.error = "The agent used its whole tool budget for this turn."

        Dataset.objects.filter(pk=dataset.pk).update(agent_messages=storable(messages))
        outcome.text = tools.text
        return outcome

    def describe_error(self, exc: Exception) -> str:
        cause = exc
        while cause is not None and not isinstance(cause, openai.APIError):
            cause = cause.__cause__
        if isinstance(cause, openai.RateLimitError):
            return "Rate limited by the model provider. Try again in a moment."
        if isinstance(cause, openai.APITimeoutError):
            return "The model provider timed out."
        if isinstance(cause, openai.AuthenticationError | openai.PermissionDeniedError):
            logger.error("native engine: %s rejected the key", self.name, exc_info=exc)
            return f"The model provider rejected this server's {self.choice.provider.key_env}."
        if isinstance(cause, openai.APIStatusError):
            logger.error("native engine: %s refused the request", self.name, exc_info=exc)
            return f"The model provider refused the request (HTTP {cause.status_code})."
        return f"The agent could not finish: {exc}"[:400]
