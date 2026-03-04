"""
Base mock agent that uses the real Overmind SDK with httpx transport caching.

OTel instrumentation wraps at the SDK method level (e.g. Completions.create),
so intercepting at the httpx transport layer still produces real OTel spans
that flow to the Overmind API.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from opentelemetry import trace

from helpers.llm_cache import CachingTransport, LLMCache

logger = logging.getLogger(__name__)


def _flush_otel():
    """Force-flush the OTel BatchSpanProcessor so traces are sent immediately."""
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush(timeout_millis=10_000)


def create_openai_client(
    api_token: str,
    cache: LLMCache,
    base_url: str,
):
    """Create an Overmind-wrapped OpenAI client with caching transport."""
    import os

    from overmind.clients import OpenAI

    if not os.environ.get("OPENAI_API_KEY"):
        os.environ["OPENAI_API_KEY"] = "sk-cached-dummy-key"

    real_transport = httpx.HTTPTransport()
    transport = CachingTransport(real_transport, cache, "openai")
    http_client = httpx.Client(transport=transport)
    return OpenAI(
        overmind_api_key=api_token,
        overmind_base_url=base_url,
        http_client=http_client,
    )


def create_anthropic_client(
    api_token: str,
    cache: LLMCache,
    base_url: str,
):
    """Create an Overmind-wrapped Anthropic client with caching transport."""
    import os

    from overmind.clients import Anthropic

    if not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-cached-dummy-key"

    real_transport = httpx.HTTPTransport()
    transport = CachingTransport(real_transport, cache, "anthropic")
    http_client = httpx.Client(transport=transport)
    return Anthropic(
        overmind_api_key=api_token,
        overmind_base_url=base_url,
        http_client=http_client,
    )


def create_gemini_client(
    api_token: str,
    cache: LLMCache,
    base_url: str,
):
    """Create an Overmind-wrapped Gemini client with caching transport."""
    import os

    from overmind.clients.google import Client as GoogleClient

    if not os.environ.get("GOOGLE_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = "gemini-cached-dummy-key"

    real_transport = httpx.HTTPTransport()
    transport = CachingTransport(real_transport, cache, "gemini")
    http_client = httpx.Client(transport=transport)
    return GoogleClient(
        overmind_api_key=api_token,
        overmind_base_url=base_url,
        http_options={"httpx_client": http_client},
    )


class BaseMockAgent:
    """
    Runs a batch of LLM queries and returns after OTel spans have been flushed.

    Subclasses set SYSTEM_PROMPT, QUERIES, MODEL, and optionally TOOLS.
    """

    SYSTEM_PROMPT: str = ""
    QUERIES: list[str] = []
    MODEL: str = "gemini-3.1-flash-lite-preview"
    TOOLS: list[dict[str, Any]] | None = None

    def __init__(
        self,
        provider: str,
        api_token: str,
        cache: LLMCache,
        base_url: str,
    ):
        self.provider = provider
        self.api_token = api_token
        self.cache = cache
        self.base_url = base_url
        self._client = self._create_client()

    def _create_client(self):
        factories = {
            "openai": create_openai_client,
            "anthropic": create_anthropic_client,
            "gemini": create_gemini_client,
        }
        factory = factories[self.provider]
        return factory(self.api_token, self.cache, self.base_url)

    def run(self) -> int:
        """
        Execute all queries against the LLM client.
        Returns the number of successful calls.
        """
        success_count = 0
        total = len(self.QUERIES)

        for i, query in enumerate(self.QUERIES):
            try:
                logger.info(
                    "[%s/%s] %s query: %.60s…",
                    i + 1,
                    total,
                    self.provider,
                    query,
                )
                self._call(query)
                success_count += 1
            except Exception:
                logger.exception("Query %d failed", i + 1)

            if (i + 1) % 10 == 0:
                time.sleep(0.5)

        _flush_otel()
        time.sleep(2)
        _flush_otel()

        logger.info(
            "%s agent done: %d/%d succeeded", self.provider, success_count, total
        )
        return success_count

    def _call(self, query: str):
        if self.provider == "openai":
            self._call_openai(query)
        elif self.provider == "anthropic":
            self._call_anthropic(query)
        elif self.provider == "gemini":
            self._call_gemini(query)

    def _call_openai(self, query: str):
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ]
        kwargs: dict[str, Any] = {"model": self.MODEL, "messages": messages}
        if self.TOOLS:
            kwargs["tools"] = self.TOOLS
        self._client.chat.completions.create(**kwargs)

    def _call_anthropic(self, query: str):
        kwargs: dict[str, Any] = {
            "model": self.MODEL,
            "max_tokens": 1024,
            "system": self.SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": query}],
        }
        if self.TOOLS:
            anthropic_tools = []
            for t in self.TOOLS:
                fn = t["function"]
                anthropic_tools.append(
                    {
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "input_schema": fn.get(
                            "parameters", {"type": "object", "properties": {}}
                        ),
                    }
                )
            kwargs["tools"] = anthropic_tools
        self._client.messages.create(**kwargs)

    def _call_gemini(self, query: str):
        prompt = f"{self.SYSTEM_PROMPT}\n\nUser: {query}"
        self._client.models.generate_content(
            model=self.MODEL,
            contents=prompt,
        )
