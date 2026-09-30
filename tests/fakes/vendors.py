from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

from fakes.http import Call

BAD_REPLY = "Your refund is on its way. The order was delivered."
MODEL = "openai/gpt-4.1-mini"


@dataclass
class Step:
    name: str
    kind: str
    parent: str | None
    input: Any = None
    output: Any = None
    tokens: tuple[int, int] = (0, 0)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    start: datetime | None = None
    end: datetime | None = None


def support_desk_trace(started: datetime) -> tuple[str, list[Step]]:
    trace_id = uuid.uuid4().hex
    root = Step("handle_ticket", "span", None, input="Refund order 42 please", output=BAD_REPLY)
    triage = Step(
        "triage",
        "generation",
        root.id,
        input=[{"role": "user", "content": "Refund order 42 please"}],
        output="refund",
        tokens=(20, 1),
    )
    answer = Step("answer", "span", root.id, input="Refund order 42 please", output=BAD_REPLY)
    decide = Step(
        "chat",
        "generation",
        answer.id,
        input=[{"role": "user", "content": "[refund] Refund order 42 please"}],
        output={"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "42"}}]},
        tokens=(30, 10),
    )
    tool = Step(
        "lookup_order", "tool", answer.id, input={"order_id": "42"}, output={"status": "delivered"}
    )
    reply = Step(
        "chat",
        "generation",
        answer.id,
        input=[{"role": "tool", "content": '{"status": "delivered"}'}],
        output=BAD_REPLY,
        tokens=(40, 12),
    )
    steps = [root, triage, answer, decide, tool, reply]
    for index, step in enumerate(steps):
        step.start = started + timedelta(seconds=index)
        step.end = started + timedelta(seconds=index + 1)
    root.end = started + timedelta(seconds=len(steps))
    return trace_id, steps


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json(body: Any, status: int = 200) -> tuple[int, dict, bytes]:
    return status, {"content-type": "application/json"}, json.dumps(body).encode()


@dataclass
class LangfuseAPI:
    host: str = "https://langfuse.fake"
    trace_id: str = ""
    steps: list[Step] = field(default_factory=list)
    requests: list[Call] = field(default_factory=list)

    def record(self, trace_id: str, steps: list[Step]) -> None:
        self.trace_id, self.steps = trace_id, steps

    def _observation(self, step: Step) -> dict[str, Any]:
        kind = {"generation": "GENERATION", "tool": "SPAN", "span": "SPAN"}[step.kind]
        prompt, completion = step.tokens
        return {
            "id": step.id,
            "traceId": self.trace_id,
            "parentObservationId": step.parent,
            "type": kind,
            "name": step.name,
            "startTime": _iso(step.start),
            "endTime": _iso(step.end),
            "input": step.input,
            "output": step.output,
            "metadata": {},
            "model": MODEL if step.kind == "generation" else None,
            "usageDetails": {"input": prompt, "output": completion, "total": prompt + completion}
            if step.kind == "generation"
            else {},
            "costDetails": {},
            "traceName": self.steps[0].name,
            "isRootObservation": step.parent is None,
        }

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        parsed = urlparse(url)
        self.requests.append(Call(method, url, headers or {}, body))
        if parsed.path == "/api/public/projects":
            return _json({"data": [{"id": "lf-project", "name": "support-desk"}]})
        if parsed.path == "/api/public/v2/observations":
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if query.get("cursor"):
                return _json({"data": [], "meta": {"cursor": None}})
            wanted = query.get("traceId")
            data = [
                self._observation(step) for step in self.steps if wanted in (None, self.trace_id)
            ]
            return _json({"data": data, "meta": {"cursor": None}})
        return _json({"message": f"not found: {parsed.path}"}, status=404)


def _epoch(value: datetime) -> float:
    return value.timestamp()


@dataclass
class LangSmithAPI:
    host: str = "https://langsmith.fake"
    trace_id: str = ""
    steps: list[Step] = field(default_factory=list)
    requests: list[Call] = field(default_factory=list)
    project: str = field(default_factory=lambda: str(uuid.uuid4()))

    def record(self, trace_id: str, steps: list[Step]) -> None:
        self.trace_id = str(uuid.UUID(trace_id))
        self.ids = {step.id: str(uuid.uuid4()) for step in steps}
        self.steps = steps

    def _run(self, step: Step) -> dict[str, Any]:
        prompt, completion = step.tokens
        return {
            "id": self.ids[step.id],
            "name": step.name,
            "run_type": {"generation": "llm", "tool": "tool", "span": "chain"}[step.kind],
            "status": "success",
            "start_time": _iso(step.start),
            "end_time": _iso(step.end),
            "extra": {"metadata": {"ls_model_name": MODEL}} if step.kind == "generation" else {},
            "inputs": {"input": step.input},
            "outputs": {"output": step.output},
            "tags": [],
            "trace_id": self.trace_id,
            "parent_run_id": self.ids[step.parent] if step.parent else None,
            "prompt_tokens": prompt or None,
            "completion_tokens": completion or None,
            "total_tokens": (prompt + completion) or None,
        }

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        path = urlparse(url).path
        self.requests.append(Call(method, url, headers or {}, body))
        if path == "/api/v1/sessions":
            return _json([{"id": self.project, "name": "support-desk"}])
        if path == "/api/v1/runs/query":
            return _json({"runs": [self._run(step) for step in self.steps], "cursors": {}})
        return _json({"detail": f"not found: {path}"}, status=404)


@dataclass
class BraintrustAPI:
    host: str = "https://braintrust.fake"
    trace_id: str = ""
    steps: list[Step] = field(default_factory=list)
    requests: list[Call] = field(default_factory=list)

    def record(self, trace_id: str, steps: list[Step]) -> None:
        self.trace_id, self.steps = trace_id, steps

    def _row(self, step: Step) -> dict[str, Any]:
        prompt, completion = step.tokens
        metrics: dict[str, Any] = {"start": _epoch(step.start), "end": _epoch(step.end)}
        if prompt:
            metrics.update(
                prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
            )
        return {
            "id": f"row-{step.id}",
            "span_id": step.id,
            "root_span_id": self.steps[0].id,
            "span_parents": [step.parent] if step.parent else [],
            "is_root": step.parent is None,
            "span_attributes": {
                "name": step.name,
                "type": {"generation": "llm", "tool": "tool", "span": "task"}[step.kind],
            },
            "metrics": metrics,
            "metadata": {"model": MODEL} if step.kind == "generation" else {},
            "input": step.input,
            "output": step.output,
            "created": _iso(step.start),
            "_xact_id": "1",
        }

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        path = urlparse(url).path
        self.requests.append(Call(method, url, headers or {}, body))
        if path == "/v1/project":
            return _json({"objects": [{"id": "bt-project", "name": "support-desk"}]})
        if path == "/btql":
            return _json({"data": [self._row(step) for step in self.steps]})
        return _json({"message": f"not found: {path}"}, status=404)


@dataclass
class GalileoAPI:
    host: str = "https://galileo.fake"
    trace_id: str = ""
    steps: list[Step] = field(default_factory=list)
    requests: list[Call] = field(default_factory=list)
    project: str = field(default_factory=lambda: str(uuid.uuid4()))
    stream: str = field(default_factory=lambda: str(uuid.uuid4()))

    def record(self, trace_id: str, steps: list[Step]) -> None:
        self.trace_id = str(uuid.UUID(trace_id))
        self.steps = steps

    def _node(self, step: Step) -> dict[str, Any]:
        prompt, completion = step.tokens
        metrics: dict[str, Any] = {
            "duration_ns": int((step.end - step.start).total_seconds() * 1e9)
        }
        if prompt:
            metrics.update(
                num_input_tokens=prompt,
                num_output_tokens=completion,
                num_total_tokens=prompt + completion,
            )
        return {
            "id": self.trace_id if step.parent is None else str(uuid.UUID(step.id.ljust(32, "0"))),
            "type": "trace"
            if step.parent is None
            else {"generation": "llm", "tool": "tool", "span": "workflow"}[step.kind],
            "name": step.name,
            "created_at": _iso(step.start),
            "updated_at": _iso(step.end),
            "input": step.input,
            "output": step.output,
            "metrics": metrics,
            "model": MODEL if step.kind == "generation" else None,
            "spans": [self._node(child) for child in self.steps if child.parent == step.id],
        }

    def handle(self, method: str, url: str, body, headers=None) -> tuple[int, dict, bytes] | None:
        if not url.startswith(self.host):
            return None
        path = urlparse(url).path
        self.requests.append(Call(method, url, headers or {}, body))
        if path == "/v2/projects/paginated":
            return _json(
                {
                    "projects": [
                        {
                            "id": self.project,
                            "name": "support",
                            "log_streams": [{"id": self.stream, "name": "prod"}],
                        }
                    ],
                    "next_starting_token": None,
                }
            )
        if path.endswith("/traces/count"):
            return _json({"total_count": 1})
        if path.endswith("/traces/search"):
            return _json({"records": [{"id": self.trace_id}], "next_starting_token": None})
        if path.endswith(f"/traces/{self.trace_id}"):
            return _json(self._node(self.steps[0]))
        return _json({"detail": f"not found: {path}"}, status=404)
