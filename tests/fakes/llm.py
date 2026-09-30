from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    total_tokens: int = 0
    reply: dict[str, Any] = field(default_factory=dict)

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

    @property
    def system(self) -> str:
        first = self.messages[0] if self.messages else {}
        return str(first.get("content") or "") if first.get("role") == "system" else ""

    @property
    def schema(self) -> dict[str, Any] | None:
        response_format = self.body.get("response_format") or {}
        return (response_format.get("json_schema") or {}).get("schema")

    @property
    def schema_name(self) -> str:
        response_format = self.body.get("response_format") or {}
        return str((response_format.get("json_schema") or {}).get("name") or "")


def instance(schema: dict[str, Any], defs: dict[str, Any] | None = None) -> Any:
    defs = defs if defs is not None else schema.get("$defs", {})
    if "$ref" in schema:
        return instance(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return instance(options[0], defs)
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {name: instance(sub, defs) for name, sub in (schema.get("properties") or {}).items()}
    if kind == "array":
        return []
    if kind == "string":
        return ""
    if kind in ("integer", "number"):
        return schema.get("minimum", 0)
    if kind == "boolean":
        return False
    return None


@dataclass
class FakeLLM:
    requests: list[LLMRequest] = field(default_factory=list)
    unscripted: list[LLMRequest] = field(default_factory=list)
    tokens_served: int = 0
    _scripts: list[tuple[Callable[[LLMRequest], bool], Reply]] = field(default_factory=list)
    _failures: list[tuple[Callable[[LLMRequest], bool], int]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def on(self, match: str | Callable[[LLMRequest], bool], reply: Reply) -> None:
        predicate = (
            match if callable(match) else (lambda request, needle=match: needle in request.system)
        )
        self._scripts.append((predicate, reply))

    def on_json(
        self,
        match: Callable[[LLMRequest], bool],
        fields: Callable[[LLMRequest], dict[str, Any]],
    ) -> None:
        def reply(request: LLMRequest) -> str:
            return json.dumps({**instance(request.schema or {}), **fields(request)})

        self._scripts.append((match, reply))

    extra_models: list[str] = field(default_factory=list)
    catalog_models: list[str] | None = None
    prices: dict[str, dict[str, str]] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)

    def catalog(self) -> list[dict[str, Any]]:
        from overbae.core.model_registry import OPENROUTER_MODEL_SLUGS

        listed = (
            OPENROUTER_MODEL_SLUGS.values() if self.catalog_models is None else self.catalog_models
        )
        slugs = sorted(set(listed) | set(self.extra_models) | set(self.prices) | set(self.limits))
        return [
            {
                "id": slug,
                "name": slug,
                "context_length": self.limits.get(slug, 128_000),
                "top_provider": {"max_completion_tokens": 16_384},
                "supported_parameters": ["tools", "response_format", "structured_outputs"],
                "pricing": self.prices.get(slug, {"prompt": "0.000001", "completion": "0.000002"}),
                "architecture": {"modality": "text->text"},
            }
            for slug in slugs
        ]

    decisions: list[dict[str, Any]] = field(default_factory=list)
    decide: Callable[[str, dict[str, Any]], str] | None = None

    def _decide(self, key: str, question: dict[str, Any]) -> dict[str, Any]:
        options = list(question.get("criteria") or {})
        choice = self.decide(key, question) if self.decide else options[0]
        return {
            "type": "choice",
            "choice": choice,
            "probabilities": {option: 1.0 if option == choice else 0.0 for option in options},
            "confidence": 0.9,
        }

    def fail(self, match: Callable[[LLMRequest], bool], status: int = 500) -> None:
        self._failures.append((match, status))

    def _failure(self, request: LLMRequest) -> int:
        for predicate, status in self._failures:
            if predicate(request):
                with self._lock:
                    self.requests.append(request)
                return status
        return 0

    def _message(self, request: LLMRequest) -> dict[str, Any]:
        for predicate, reply in reversed(self._scripts):
            if predicate(request):
                value = reply(request) if callable(reply) else reply
                if isinstance(value, str):
                    return {"role": "assistant", "content": value}
                return {"role": "assistant", **value}
        self.unscripted.append(request)
        if request.schema is not None:
            return {"role": "assistant", "content": json.dumps(instance(request.schema))}
        return {"role": "assistant", "content": ""}

    def completion(self, request: LLMRequest) -> dict[str, Any]:
        message = self._message(request)
        usage = message.pop("usage", {})
        finish = message.pop("finish_reason", None)
        request.reply = message
        prompt_tokens = max(1, len(request.text) // 4)
        completion_tokens = max(1, len(json.dumps(message)) // 4)
        request.total_tokens = prompt_tokens + completion_tokens
        with self._lock:
            self.requests.append(request)
            self.tokens_served += request.total_tokens
        return {
            "id": f"fake-{len(self.requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish
                    or ("tool_calls" if message.get("tool_calls") else "stop"),
                }
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                **usage,
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

    @contextmanager
    def serve(self):
        llm = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, method: str) -> None:
                length = int(self.headers.get("content-length") or 0)
                status, headers, body = llm.handle(
                    method, f"http://fake-llm{self.path}", self.rfile.read(length)
                )
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                self._reply("POST")

            def do_GET(self) -> None:
                self._reply("GET")

            def log_message(self, *args) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}/v1"
        finally:
            server.shutdown()
            server.server_close()

    def handle(self, method: str, url: str, raw: bytes | str | None) -> tuple[int, dict, bytes]:
        failure = self._failure(LLMRequest(url=url, body=_json_body(raw)))
        if failure:
            return (
                failure,
                {"content-type": "application/json"},
                b'{"error": {"message": "fake outage"}}',
            )
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
        if method == "POST" and url.rstrip("/").endswith("/systemone"):
            body = json.loads(raw or b"{}")
            with self._lock:
                self.decisions.append(body)
            answers = {
                key: self._decide(key, question)
                for key, question in (body.get("questions") or {}).items()
            }
            payload = {
                "id": f"fake-decision-{len(self.decisions)}",
                "model": body.get("model"),
                "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 1, "cost": 0.0},
            }
            return 200, {"content-type": "application/json"}, json.dumps(payload).encode()
        if method == "GET" and url.rstrip("/").endswith("/models"):
            return (
                200,
                {"content-type": "application/json"},
                json.dumps({"data": self.catalog()}).encode(),
            )
        return (
            599,
            {"content-type": "application/json"},
            json.dumps({"error": f"unrouted {method} {url}"}).encode(),
        )


def _json_body(raw: bytes | str | None) -> dict[str, Any]:
    try:
        body = json.loads(raw or b"{}")
    except (TypeError, ValueError):
        return {}
    return body if isinstance(body, dict) else {}


class Network:
    def __init__(self, llm: FakeLLM) -> None:
        self.llm = llm
        self.refused: list[str] = []
        self.vendors: list[Any] = []
        self._respx = respx.mock(assert_all_called=False, assert_all_mocked=True)
        self._responses = responses.RequestsMock(assert_all_requests_are_fired=False)

    def _httpx(self, request: httpx.Request) -> httpx.Response:
        status, headers, body = self._route(
            request.method, str(request.url), request.content, dict(request.headers)
        )
        return httpx.Response(status, headers=headers, content=body)

    def _requests(self, request) -> tuple[int, dict, bytes]:
        return self._route(request.method, request.url, request.body, dict(request.headers))

    def _route(self, method: str, url: str, body, headers: dict) -> tuple[int, dict, bytes]:
        for vendor in self.vendors:
            answer = vendor.handle(method, url, body, headers)
            if answer is not None:
                return answer
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
