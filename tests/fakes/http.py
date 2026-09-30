from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse


@dataclass
class Call:
    method: str
    url: str
    headers: dict[str, str]
    body: Any

    @property
    def path(self) -> str:
        return urlparse(self.url).path

    @property
    def params(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlparse(self.url).query).items()}

    @property
    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


@dataclass
class ScriptedAPI:
    """Answers each request with the next scripted step; the last step repeats."""

    host: str
    steps: list[Any] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)

    def reply(self, status: int = 200, *, json_body: Any = None, text: str = "", headers=None):
        content = json.dumps(json_body).encode() if json_body is not None else text.encode()
        kind = "application/json" if json_body is not None else "text/plain"
        self.steps.append((status, {"content-type": kind, **(headers or {})}, content))
        return self

    def fail(self, error: BaseException):
        self.steps.append(error)
        return self

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        raw = body.encode() if isinstance(body, str) else body
        self.calls.append(
            Call(method, url, {k.lower(): v for k, v in (headers or {}).items()}, raw)
        )
        step = self.steps[min(len(self.calls) - 1, len(self.steps) - 1)]
        if isinstance(step, BaseException):
            raise step
        return step
