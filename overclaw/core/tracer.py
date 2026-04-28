"""Thin tracing shims layered on top of ``overmind``.

OverClaw used to ship a parallel in-process ``Tracer`` / ``Span`` /
``Trace`` implementation along with hand-rolled ``call_llm`` and
``call_tool`` helpers that recorded metadata into that local tracer.
That entire abstraction has been retired — every span we emit now
flows through ``overmind`` (OpenTelemetry under the hood) and LLM
spans are produced by the SDK's auto-instrumentation
(``OpenAIInstrumentor``, ``AnthropicInstrumentor``, …) enabled in
``overmind.init(...)``.

Two helpers remain because ``overclaw.utils.instrument`` rewrites them
into ``litellm.completion`` / direct function calls when an agent is
instrumented for an optimization run.  Pre-instrumentation, user agent
code may still ``from overclaw.core.tracer import call_llm, call_tool``
— those imports continue to work and now produce real OTel spans
(``SpanType.FUNCTION`` / ``SpanType.TOOL``) via the SDK.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import litellm

from overmind import SpanType, set_tag, start_span as _span

from overclaw import attrs
from overclaw.utils.llm import llm_completion

logger = logging.getLogger("overclaw.core.tracer")


def call_llm(
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    **kwargs: Any,
) -> Any:
    """Call ``litellm.completion`` inside an OTel span.

    Token / cost metadata is attached to the active span via
    :func:`overmind.set_tag` so it shows up in the trace UI without
    relying on a custom in-process tracer.  The SDK's
    ``OpenAIInstrumentor`` etc. still emit their own provider-level
    spans inside this one when enabled.
    """
    with _span(model, span_type=SpanType.FUNCTION):
        set_tag(attrs.LLM_MODEL, model)
        set_tag(attrs.LLM_MESSAGES_COUNT, len(messages))
        if tools:
            set_tag(
                attrs.LLM_TOOLS_PROVIDED,
                [t["function"]["name"] for t in tools],
            )

        try:
            response = llm_completion(model, messages, tools=tools, **kwargs)
        except Exception as exc:
            logger.exception("call_llm failed model=%s", model)
            set_tag(attrs.LLM_ERROR, str(exc))
            raise

        usage = getattr(response, "usage", None)
        if usage is not None:
            set_tag(attrs.LLM_PROMPT_TOKENS, getattr(usage, "prompt_tokens", 0) or 0)
            set_tag(
                attrs.LLM_COMPLETION_TOKENS,
                getattr(usage, "completion_tokens", 0) or 0,
            )
            set_tag(attrs.LLM_TOTAL_TOKENS, getattr(usage, "total_tokens", 0) or 0)

        try:
            cost = litellm.completion_cost(completion_response=response)
            set_tag(attrs.LLM_COST, float(cost))
        except Exception:
            pass

        msg = response.choices[0].message
        if getattr(msg, "tool_calls", None):
            set_tag(
                attrs.LLM_TOOL_CALLS,
                [tc.function.name for tc in msg.tool_calls],
            )

        return response


def call_tool(name: str, args: dict, fn: Callable[..., Any]) -> Any:
    """Invoke a tool function inside an OTel ``SpanType.TOOL`` span.

    Argument keys (not values) are surfaced as a span tag for triage;
    the actual argument values are intentionally not tagged because
    they may contain user data.
    """
    with _span(name, span_type=SpanType.TOOL):
        set_tag(attrs.TOOL_NAME, name)
        set_tag(attrs.TOOL_ARG_KEYS, sorted((args or {}).keys()))
        try:
            return fn(**args)
        except Exception as exc:
            set_tag(attrs.TOOL_ERROR, str(exc))
            logger.exception("call_tool failed name=%s", name)
            raise


__all__ = ["call_llm", "call_tool"]
