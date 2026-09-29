from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import responses
import respx

LOCAL = re.compile(r"^https?://(127\.0\.0\.1|localhost)(:\d+)?/")
OUTSIDE = re.compile(r"^(?!https?://(127\.0\.0\.1|localhost)(:\d+)?/).*")

Reply = str | dict[str, Any] | Callable[["LLMRequest"], "str | dict[str, Any]"]


@dataclass
class LLMRequest:
    url: str
    body: dict[str, Any]

    @property
    def model(self) -> str:
        return str(self.body.get("model", ""))

    @property
    def messages(self) -> list[dict[str, Any]]:
        return list(self.body.get("messages") or [])

    @property
    def text(self) -> str:
        parts = []
        for message in self.messages:
            content = message.get("content")
            if isinstance(content, list):
                parts.extend(str(p.get("text", "")) for p in content if isinstance(p, dict))
            elif content:
                parts.append(str(content))
        return "\n".join(parts)


@dataclass
class FakeLLM:
    requests: list[LLMRequest] = field(default_factory=list)
    unscripted: list[LLMRequest] = field(default_factory=list)
    tokens_served: int = 0
    _scripts: list[tuple[Callable[[LLMRequest], bool], Reply]] = field(default_factory=list)

    def on(self, match: str | Callable[[LLMRequest], bool], reply: Reply) -> None:
        predicate = (
            match if callable(match) else (lambda request, needle=match: needle in request.text)
        )
        self._scripts.append((predicate, reply))

    def _message(self, request: LLMRequest) -> dict[str, Any]:
        for predicate, reply in reversed(self._scripts):
            if predicate(request):
                value = reply(request) if callable(reply) else reply
                if isinstance(value, str):
                    return {"role": "assistant", "content": value}
                return {"role": "assistant", **value}
        self.unscripted.append(request)
        return {"role": "assistant", "content": ""}

    def completion(self, request: LLMRequest) -> dict[str, Any]:
        self.requests.append(request)
        message = self._message(request)
        prompt_tokens = max(1, len(request.text) // 4)
        completion_tokens = max(1, len(json.dumps(message)) // 4)
        self.tokens_served += prompt_tokens + completion_tokens
        return {
            "id": f"fake-{len(self.requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    def stream(self, request: LLMRequest) -> bytes:
        body = self.completion(request)
        choice = body["choices"][0]
        delta = {"role": "assistant", **{k: v for k, v in choice["message"].items() if k != "role"}}
        for index, call in enumerate(delta.get("tool_calls") or []):
            call["index"] = index
        chunks = [
            {**body, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta}]},
            {
                **body,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
                "usage": body["usage"],
            },
        ]
        lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
        return ("".join(lines) + "data: [DONE]\n\n").encode()

    def handle(self, method: str, url: str, raw: bytes | str | None) -> tuple[int, dict, bytes]:
        if method == "POST" and url.rstrip("/").endswith("/chat/completions"):
            body = json.loads(raw or b"{}")
            request = LLMRequest(url=url, body=body)
            if body.get("stream"):
                return 200, {"content-type": "text/event-stream"}, self.stream(request)
            return (
                200,
                {"content-type": "application/json"},
                json.dumps(self.completion(request)).encode(),
            )
        if method == "GET" and url.rstrip("/").endswith("/models"):
            return 200, {"content-type": "application/json"}, b'{"data": []}'
        return (
            599,
            {"content-type": "application/json"},
            json.dumps({"error": f"unrouted {method} {url}"}).encode(),
        )


class Network:
    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.refused: list[str] = []
        self._respx = respx.mock(assert_all_called=False, assert_all_mocked=True)
        self._responses = responses.RequestsMock(assert_all_requests_are_fired=False)

    def _httpx(self, request: httpx.Request) -> httpx.Response:
        status, headers, body = self._route(request.method, str(request.url), request.content)
        return httpx.Response(status, headers=headers, content=body)

    def _requests(self, request) -> tuple[int, dict, bytes]:
        return self._route(request.method, request.url, request.body)

    def _route(self, method: str, url: str, body) -> tuple[int, dict, bytes]:
        status, headers, content = self.llm.handle(method, url, body)
        if status == 599:
            self.refused.append(f"{method} {url}")
        return status, headers, content

    def __enter__(self) -> Network:
        self._respx.__enter__()
        self._respx.route(url__regex=LOCAL.pattern).pass_through()
        self._respx.route().mock(side_effect=self._httpx)
        self._responses.__enter__()
        self._responses.add_passthru(LOCAL)
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            self._responses.add_callback(method, OUTSIDE, callback=self._requests)
        return self

    def __exit__(self, *exc) -> None:
        self._responses.__exit__(*exc)
        self._respx.__exit__(*exc)
