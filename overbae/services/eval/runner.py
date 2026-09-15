"""Replay-based capability runner.

The platform holds no user tool implementations or credentials, so the default
re-run strategy is replay: real model turns, but tool calls answered from a
source trace's recorded results. Deterministic and side-effect-free while still
exercising the model's decision-making. :class:`ToolProvider` is the seam for
live execution; those adapters are stubbed.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Protocol

from celery.exceptions import SoftTimeLimitExceeded

from overbae.core.llms import ModelSpec, call_llm
from overbae.services.eval import chatml, normalizer

logger = logging.getLogger(__name__)

# One step is one model turn, so reproducing N recorded tool calls needs at
# least N steps plus a final-answer turn. A flat budget starves long traces, so
# it is derived from the recorded tool-call count and clamped to a ceiling that
# stops a pathological loop running unbounded.
_DEFAULT_MAX_STEPS = 12  # fallback/floor when no tool calls were recorded
_MAX_STEPS_CEILING = 100  # hard upper bound on model turns per sample
_STEP_HEADROOM = 1.5  # multiplier on recorded tool calls (path divergence slack)
_STEP_SLACK = 5  # extra turns for the final answer + minor re-tries


def resolve_max_steps(*, recorded_tool_calls: int = 0, override: int | None = None) -> int:
    """An explicit ``override`` wins but is still clamped to the ceiling;
    otherwise ``ceil(recorded * headroom) + slack``, floored and capped."""
    if override is not None:
        try:
            override = int(override)
        except (TypeError, ValueError):
            override = None
    if override and override > 0:
        return min(override, _MAX_STEPS_CEILING)
    if recorded_tool_calls <= 0:
        return _DEFAULT_MAX_STEPS
    derived = math.ceil(recorded_tool_calls * _STEP_HEADROOM) + _STEP_SLACK
    return max(_DEFAULT_MAX_STEPS, min(derived, _MAX_STEPS_CEILING))


@dataclass
class ToolResult:
    content: str
    error: str = ""
    matched: bool = True  # False => no recorded result found (replay miss)


class ToolProvider(Protocol):
    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...

    def tool_definitions(self) -> list[dict[str, Any]]:
        """OpenAI-style tool schemas to advertise to the model."""
        ...


@dataclass
class ReplayToolProvider:
    """Matching tiers: exact (name + arguments), then name + argument subset,
    then first-unused call of the same name (fuzzy, flagged). A miss returns an
    error result so the loop can still terminate."""

    recorded_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_defs: list[dict[str, Any]] = field(default_factory=list)
    _used: set[int] = field(default_factory=set)
    fuzzy_hits: int = 0
    misses: int = 0

    @classmethod
    def from_structured(
        cls, structured: dict[str, Any], tool_defs: list[dict[str, Any]] | None = None
    ):
        nodes = (structured.get("tool_graph") or {}).get("nodes", [])
        # Keyed by the SANITIZED name, which is what gets advertised and so what
        # the model will call; the original is kept for evaluator visibility.
        recorded = [
            {
                "name": chatml.sanitize_tool_name(n.get("tool", "")),
                "original_name": n.get("tool", ""),
                "arguments": n.get("arguments", {}),
                "result": n.get("result"),
                "error": n.get("error", ""),
            }
            for n in nodes
        ]
        return cls(recorded_calls=recorded, tool_defs=tool_defs or [])

    def tool_definitions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in self.tool_defs:
            # The single choke point where tool names reach the provider, so
            # sanitizing here covers every origin. Stored tool_defs and
            # tool_graph keep their real names for evaluators.
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": chatml.sanitize_tool_name(t.get("name", "")),
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {})
                        or {"type": "object", "properties": {}},
                    },
                }
            )
        return out

    def original_name(self, advertised: str) -> str:
        """The model calls tools by the sanitized alias, but the durable
        trajectory must record the REAL name (``run_clustering.py``, not
        ``run_clustering_py``) so name-equality evaluators compare like for like.
        Best-effort: tool defs, then recorded calls, then the alias itself."""
        for t in self.tool_defs:
            raw = t.get("name", "")
            if chatml.sanitize_tool_name(raw) == advertised:
                return raw
        for rec in self.recorded_calls:
            if rec.get("name") == advertised and rec.get("original_name"):
                return rec["original_name"]
        return advertised

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        idx = self._match_exact(name, arguments)
        if idx is not None:
            self._used.add(idx)
            return self._serve(idx)
        # Checked BEFORE the fuzzy tiers so an exact recorded result wins over a
        # same-name fuzzy slot.
        reidx = self._reserve_idempotent(name, arguments)
        if reidx is not None:
            return self._serve(reidx)
        idx = self._match_fuzzy(name, arguments)
        if idx is not None:
            self.fuzzy_hits += 1
            self._used.add(idx)
            return self._serve(idx)
        self.misses += 1
        return ToolResult(content="", error=f"No recorded result for tool '{name}'", matched=False)

    def _serve(self, idx: int) -> ToolResult:
        rec = self.recorded_calls[idx]
        result = rec.get("result")
        content = result if isinstance(result, str) else json.dumps(result, default=str)
        return ToolResult(content=content, error=rec.get("error", "") or "")

    @staticmethod
    def _name_matches(rec: dict[str, Any], name: str) -> bool:
        # The original name and a re-sanitized original are accepted too, so
        # replay resolves regardless of how the call name was produced.
        if name in (rec.get("name"), rec.get("original_name")):
            return True
        return chatml.sanitize_tool_name(rec.get("original_name") or rec.get("name") or "") == name

    def _match_exact(self, name: str, arguments: dict[str, Any]) -> int | None:
        """First unused recorded call matching exactly on name + arguments."""
        for i, rec in enumerate(self.recorded_calls):
            if i in self._used:
                continue
            if self._name_matches(rec, name) and _args_equal(rec.get("arguments"), arguments):
                return i
        return None

    def _match_fuzzy(self, name: str, arguments: dict[str, Any]) -> int | None:
        """Best-effort fallback: argument subset, then first unused same name."""
        for i, rec in enumerate(self.recorded_calls):
            if i in self._used:
                continue
            if self._name_matches(rec, name) and _args_subset(arguments, rec.get("arguments")):
                return i
        for i, rec in enumerate(self.recorded_calls):
            if i in self._used:
                continue
            if self._name_matches(rec, name):
                return i
        return None

    def _reserve_idempotent(self, name: str, arguments: dict[str, Any]) -> int | None:
        """Lets a repeated read-only call re-serve an already-consumed recorded
        result — replaying a read N+1 times is not genuine divergence. Gated on
        :func:`normalizer._is_state_changing` so a repeated write/create/delete
        still consumes once."""
        if normalizer._is_state_changing(self.original_name(name)):
            return None
        for i, rec in enumerate(self.recorded_calls):
            if i not in self._used:
                continue
            if self._name_matches(rec, name) and _args_equal(rec.get("arguments"), arguments):
                return i
        return None


def _restore_original_tool_names(
    messages: list[dict[str, Any]], provider: ToolProvider
) -> list[dict[str, Any]]:
    """Apply ONLY to the messages persisted/returned — the conversation re-sent
    to the provider must keep the sanitized aliases."""
    if not isinstance(provider, ReplayToolProvider):
        return messages
    out: list[dict[str, Any]] = []
    for m in messages:
        m2 = dict(m)
        if m2.get("role") == "assistant" and m2.get("tool_calls"):
            new_calls = []
            for tc in m2["tool_calls"]:
                tc2 = dict(tc)
                if isinstance(tc2.get("function"), dict):
                    fn = dict(tc2["function"])
                    fn["name"] = provider.original_name(fn.get("name", ""))
                    tc2["function"] = fn
                elif tc2.get("name"):
                    tc2["name"] = provider.original_name(tc2["name"])
                new_calls.append(tc2)
            m2["tool_calls"] = new_calls
        elif m2.get("role") == "tool" and m2.get("name"):
            m2["name"] = provider.original_name(m2["name"])
        out.append(m2)
    return out


def _args_equal(a: Any, b: Any) -> bool:
    try:
        return json.dumps(a, sort_keys=True, default=str) == json.dumps(
            b, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        return a == b


def _args_subset(sub: Any, sup: Any) -> bool:
    if not isinstance(sub, dict) or not isinstance(sup, dict):
        return False
    return all(sup.get(k) == v for k, v in sub.items())


@dataclass
class RunResult:
    output_messages: list[dict[str, Any]]
    steps: int
    cost: float
    fuzzy_tool_hits: int
    tool_misses: int
    error: str = ""
    # Summed across the loop's model turns. Zero when the provider reports no
    # usage or timing.
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


def generate_decision(
    *,
    input_messages: list[dict[str, Any]],
    tool_provider: ToolProvider,
    model: str | None = None,
    model_spec: ModelSpec | None = None,
    system_prompt: str | None = None,
    reasoning_effort: str | None = None,
) -> RunResult:
    messages = list(input_messages)
    if system_prompt and not any(m.get("role") == "system" for m in messages):
        messages = [{"role": "system", "content": system_prompt}, *messages]
    try:
        raw, stats = call_llm(
            input_text="",
            model=model,
            model_spec=model_spec,
            messages=messages,
            tools=tool_provider.tool_definitions() or None,
            reasoning_effort=reasoning_effort,
        )
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        return RunResult([], 0, 0.0, 0, 0, error=str(exc))

    assistant, _ = _parse_assistant(raw)
    # Teacher forcing evaluates the decision itself; executing its tools would
    # introduce replay misses and additional model turns before the next fixed seed.
    return RunResult(
        _restore_original_tool_names([assistant], tool_provider),
        1,
        float(stats.get("response_cost", 0) or 0),
        0,
        0,
        latency_ms=float(stats.get("response_ms", 0) or 0),
        prompt_tokens=int(stats.get("prompt_tokens", 0) or 0),
        completion_tokens=int(stats.get("completion_tokens", 0) or 0),
    )


def run_capability(
    *,
    input_messages: list[dict[str, Any]],
    tool_provider: ToolProvider,
    model: str | None = None,
    model_spec: ModelSpec | None = None,
    system_prompt: str | None = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
    reasoning_effort: str | None = None,
) -> RunResult:
    """``input_messages`` seeds the conversation; ``output_messages`` carries
    only the NEW assistant/tool messages produced."""
    tool_defs = tool_provider.tool_definitions()
    working: list[dict[str, Any]] = list(input_messages)
    if system_prompt and not any(m.get("role") == "system" for m in working):
        working = [{"role": "system", "content": system_prompt}, *working]

    produced: list[dict[str, Any]] = []
    total_cost = 0.0
    total_latency_ms = 0.0
    prompt_tokens = 0
    completion_tokens = 0
    fuzzy = 0
    misses = 0

    for step in range(max_steps):
        # Force a final-answer turn at the budget edge: a divergent replay that
        # is still mid-tool-loop would otherwise be cut off with nothing
        # gradable. Well-behaved replays stop emitting tool calls earlier and
        # never reach this turn.
        is_final_turn = step == max_steps - 1 and step > 0
        if is_final_turn:
            working.append(
                {
                    "role": "user",
                    "content": (
                        "You have reached the step budget for this task and may not "
                        "call any more tools. Using everything you have gathered so "
                        "far, give your best final answer now."
                    ),
                }
            )
        try:
            raw, stats = call_llm(
                input_text="",
                model=model,
                model_spec=model_spec,
                messages=working,
                tools=None if is_final_turn else (tool_defs or None),
                reasoning_effort=reasoning_effort,
            )
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Capability run failed at step %d: %s", step, exc)
            return RunResult(
                _restore_original_tool_names(produced, tool_provider),
                step,
                total_cost,
                fuzzy,
                misses,
                error=str(exc),
                latency_ms=total_latency_ms,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )

        total_cost += float(stats.get("response_cost", 0) or 0)
        total_latency_ms += float(stats.get("response_ms", 0) or 0)
        prompt_tokens += int(stats.get("prompt_tokens", 0) or 0)
        completion_tokens += int(stats.get("completion_tokens", 0) or 0)
        assistant_msg, tool_calls = _parse_assistant(raw)
        working.append(assistant_msg)
        produced.append(assistant_msg)

        if not tool_calls:
            break

        for tc in tool_calls:
            res = tool_provider.execute(tc["name"], tc.get("arguments", {}))
            if not res.matched:
                misses += 1
            content = res.content if not res.error else f"ERROR: {res.error}"
            tool_msg = {
                "role": "tool",
                "tool_call_id": tc["id"],
                "name": tc["name"],
                "content": content,
            }
            working.append(tool_msg)
            produced.append(tool_msg)

        if isinstance(tool_provider, ReplayToolProvider):
            fuzzy = tool_provider.fuzzy_hits
            misses = tool_provider.misses

    return RunResult(
        _restore_original_tool_names(produced, tool_provider),
        step + 1,
        total_cost,
        fuzzy,
        misses,
        latency_ms=total_latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )


def _assistant_with_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    # OpenAI wire format, so this can be appended to `working` and re-sent
    # next step. Do NOT route through chatml.normalize_message: it strips
    # `type`, and OpenAI then rejects the request with "Missing required
    # parameter: messages[N].tool_calls[0].type".
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": c["id"],
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c["arguments"], default=str),
                },
            }
            for c in calls
        ],
    }


def _parse_assistant(raw: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``call_llm`` returns text, a JSON string ``{"tool_calls": [...]}``, or
    Qwen ``<tool_call>`` XML when the model emitted tool calls."""
    parsed = chatml.maybe_parse_json(raw)
    if isinstance(parsed, dict) and "tool_calls" in parsed:
        calls = chatml.normalize_tool_calls(parsed["tool_calls"])
        return _assistant_with_calls(calls), calls
    calls = normalizer.parse_text_tool_calls(raw)
    if calls:
        return _assistant_with_calls(calls), calls
    return {"role": "assistant", "content": raw}, []
