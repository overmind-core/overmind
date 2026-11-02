"""Deterministic checks as GRPO rewards.

A check script is the same ``def evaluate(span)`` source the eval matrix runs on live
spans. Here the span is one sampled completion: its output is the completion text and
any tool calls it wrote become child ``tool_call`` spans, so ``tool_selection`` and
``tool_args_match`` score a single-turn tool call exactly as they score a traced one.
A row can supply a check's top-of-file constants (``bind``) and the span's attributes,
so a per-row reference reaches scripts that read ``REFERENCE`` or ``EXPECTED_CALLS``.

Scripts run in a warm ``python -I`` child built from the platform's own runner source,
never in the training process.
"""

from __future__ import annotations

import json
import os
import re
import select
import subprocess
import sys
import tempfile
import threading
from typing import Any

WALL_SECONDS = 15
TOOL_CALL_TAG = re.compile(r"<tool_call>\s*(.*?)\s*(?:</tool_call>|$)", re.DOTALL)


def literal(value: object) -> str:
    if value is None or isinstance(value, (bool, int, float, str)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(literal(item) for item in value) + "]"
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return "{" + ", ".join(f"{key!r}: {literal(item)}" for key, item in value.items()) + "}"
    raise ValueError(f"unsupported parameter value {value!r}")


def constants(text: str) -> dict[str, str]:
    """The script's parameters: single-line ``NAME = value`` constants above the first def."""
    header = text.split("\ndef ", 1)[0]
    return {
        match.group(1): match.group(2).strip()
        for match in re.finditer(r"^([A-Z][A-Z_]*) = (.*)$", header, re.MULTILINE)
    }


def bind(text: str, params: dict[str, Any]) -> str:
    """*text* with its parameter constants set to *params*; an unknown name is rejected."""
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    known = constants(text)
    for key, value in params.items():
        if key not in known:
            raise ValueError(f"no parameter {key!r}")
        line = re.compile(rf"^{re.escape(key)} = .*$", re.MULTILINE)
        text = line.sub(lambda _m, key=key, value=value: f"{key} = {literal(value)}", text, 1)
    return text


def _json_objects(text: str) -> list[Any]:
    """Every top-level JSON value that opens with ``{`` or ``[`` in *text*, in order."""
    decoder = json.JSONDecoder()
    found, index = [], 0
    while index < len(text):
        starts = [i for i in (text.find("{", index), text.find("[", index)) if i >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        found.append(value)
        index = end
    return found


def _call(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = value.get("name")
    if not isinstance(name, str) or not name:
        function = value.get("function")
        if isinstance(function, dict):
            return _call(function)
        return None
    arguments = value.get("arguments", value.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    return {"name": name, "arguments": arguments if isinstance(arguments, dict) else {}}


def tool_calls(text: str) -> list[dict[str, Any]]:
    """Tool calls in a completion: Hermes-style ``<tool_call>`` blocks, else bare JSON calls."""
    blocks = TOOL_CALL_TAG.findall(text or "")
    values = [value for block in blocks for value in _json_objects(block)] if blocks else []
    if not blocks:
        values = _json_objects(text or "")
    calls = []
    for value in values:
        for item in value if isinstance(value, list) else [value]:
            call = _call(item)
            if call:
                calls.append(call)
    return calls


def visible_text(text: str) -> str:
    """The completion without an empty or closed reasoning block."""
    return re.sub(r"<think>.*?</think>\s*", "", text or "", flags=re.DOTALL).strip()


def span_graph(
    prompt: str,
    completion: str,
    *,
    expected: Any = None,
    attributes: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    output = visible_text(completion)
    root = {
        "span_id": "completion",
        "parent_id": None,
        "trace_id": "rl",
        "span_type": "llm_call",
        "name": "completion",
        "tool": "",
        "status": "ok",
        "input": prompt,
        "output": output,
        "expected": expected,
        "duration_s": None,
        "usage": {},
        "attributes": attributes or {},
    }
    children = [
        {
            "span_id": f"tool-{index}",
            "parent_id": "completion",
            "trace_id": "rl",
            "span_type": "tool_call",
            "name": call["name"],
            "tool": call["name"],
            "status": "ok",
            "input": json.dumps(call["arguments"]),
            "output": "",
            "expected": None,
            "duration_s": None,
            "usage": {},
            "attributes": {},
        }
        for index, call in enumerate(tool_calls(output))
    ]
    return [root, *children]


class CheckRunner:
    """One warm ``python -I`` child running the platform's check runner; a death or a
    timeout replaces it, and that call scores as an error."""

    def __init__(self, runner_source: str) -> None:
        self.runner_source = runner_source
        self.proc: subprocess.Popen | None = None
        self.tmp: tempfile.TemporaryDirectory | None = None

    def _start(self) -> subprocess.Popen:
        self.close()
        self.tmp = tempfile.TemporaryDirectory(prefix="rl_checks_")
        path = os.path.join(self.tmp.name, "runner.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.runner_source)
        self.proc = subprocess.Popen(
            [sys.executable, "-I", path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=self.tmp.name,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONUNBUFFERED": "1",
                "HOME": self.tmp.name,
            },
        )
        return self.proc

    def run(self, source: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
        proc = self.proc if self.proc and self.proc.poll() is None else self._start()
        encoded = json.dumps({"source": source, "spans": spans, "span_id": spans[0]["span_id"]})
        stdin, stdout = proc.stdin, proc.stdout
        assert stdin is not None and stdout is not None

        def write() -> None:
            try:
                stdin.write(encoded.encode("utf-8") + b"\n")
                stdin.flush()
            except OSError:
                pass

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        ready, _, _ = select.select([stdout], [], [], WALL_SECONDS)
        if not ready:
            self.close()
            return {"outcome": "error", "score": None, "explanation": "check timed out"}
        line = stdout.readline()
        writer.join(timeout=1)
        try:
            result = json.loads(line)
        except ValueError:
            self.close()
            return {"outcome": "error", "score": None, "explanation": "check runner failed"}
        return result if isinstance(result, dict) else {"outcome": "error", "score": None}

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
        if self.tmp is not None:
            self.tmp.cleanup()
            self.tmp = None


def row_source(reward: dict[str, Any], row: dict[str, Any]) -> str:
    bound = {param: row.get(column) for param, column in (reward.get("bind") or {}).items()}
    return bind(reward["source"], bound) if bound else reward["source"]


def row_attributes(reward: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(column) for key, column in (reward.get("attributes") or {}).items()}


def score_completion(
    runner: CheckRunner,
    rewards: list[dict[str, Any]],
    row: dict[str, Any],
    prompt: str,
    completion: str,
) -> dict[str, Any]:
    """Every reward check on one completion, and their weighted mean over scored checks.

    An abstaining or failing check is left out of the mean; a completion no check could
    score earns 0, so GRPO still sees it as worse than one that passes."""
    checks: dict[str, dict[str, Any]] = {}
    total = weight_sum = 0.0
    for reward in rewards:
        spans = span_graph(
            prompt,
            completion,
            expected=row.get(reward["expected"]) if reward.get("expected") else None,
            attributes=row_attributes(reward, row),
        )
        try:
            result = runner.run(row_source(reward, row), spans)
        except ValueError as exc:
            result = {"outcome": "error", "score": None, "explanation": str(exc)[:200]}
        checks[reward["key"]] = {
            "outcome": result.get("outcome"),
            "score": result.get("score"),
            "explanation": str(result.get("explanation") or "")[:240],
        }
        if result.get("outcome") == "scored" and result.get("score") is not None:
            weight = float(reward.get("weight", 1.0))
            total += weight * float(result["score"])
            weight_sum += weight
    scored = [c for c in checks.values() if c["outcome"] == "scored"]
    return {
        "reward": total / weight_sum if weight_sum else 0.0,
        "passed": bool(scored) and all((c["score"] or 0.0) >= 0.5 for c in scored),
        "checks": checks,
    }
