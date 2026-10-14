"""
Import-free Python checks that grade one span at a time.

Users write ``def evaluate(span): ...``. The script sees only the span, its
parent, children, siblings and their fields, plus ``json`` (loads, dumps) and
``re`` (search, fullmatch, findall, sub, the flags) as preloaded globals: an
``import`` is rejected. Fields are read-only: mappings are not ``dict`` and lists
arrive as tuples. Execution is an AST audit plus a ``python3 -I`` child under
CPU/memory/wall limits.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
from contextlib import suppress
from typing import Any

from overbae.innermind import evidence
from overbae.models import Span

CPU_SECONDS = 5
WALL_SECONDS = 15
MEMORY_BYTES = 512 * 1024**2
TRACE_SPAN_CAP = 2_000
# Two warm interpreters. Each one is a CPython process (~10MB); a trace burst used to
# start one per check, up to 16, and that was the worker's memory and disk spike.
MAX_CONCURRENT_SANDBOXES = 2
# A child that has grown well past a fresh interpreter is replaced, so one script
# cannot leave its allocations in the pool.
SANDBOX_RSS_KB = 64 * 1024
# Text a neighbouring span carries into the sandbox; its full payload would copy a large
# trace into every call, past the child's memory limit.
NEIGHBOUR_TEXT = 4_000
SLOT_POLL_SECONDS = 0.05
NEIGHBOUR_VIEW = "_sandbox_neighbour_view"

# Every task runs its own event loop, so only a thread-level lock bounds the worker.
sandbox_slots = threading.BoundedSemaphore(MAX_CONCURRENT_SANDBOXES)

SPAN_FIELDS = (
    "span_id",
    "parent_id",
    "trace_id",
    "span_type",
    "name",
    "tool",
    "status",
    "input",
    "output",
    "expected",
    "duration_s",
    "usage",
    "attributes",
)

FORBIDDEN_ATTRS = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__import__",
        "__loader__",
        "__spec__",
        "__class__",
        "__mro__",
        "__bases__",
        "__dict__",
        "__code__",
        "__closure__",
        "__func__",
        "__self__",
        "__module__",
        "__qualname__",
        "__annotations__",
        "__reduce__",
        "__reduce_ex__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__slots__",
        "format",
        "format_map",
        "system",
        "popen",
        "spawn",
        "fork",
    }
)

FORBIDDEN_CALLS = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "exit",
        "quit",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        "globals",
        "locals",
        "vars",
        "dir",
        "type",
        "memoryview",
        "bytearray",
        "bytes",
        "object",
        "super",
        "classmethod",
        "staticmethod",
        "property",
        "help",
        "print",
        "id",
        "hash",
        "callable",
        "issubclass",
    }
)

BUILTIN_NAMES = (
    "abs",
    "all",
    "any",
    "AttributeError",
    "bool",
    "dict",
    "enumerate",
    "Exception",
    "filter",
    "float",
    "IndexError",
    "int",
    "isinstance",
    "KeyError",
    "len",
    "list",
    "map",
    "max",
    "min",
    "range",
    "reversed",
    "round",
    "RuntimeError",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "TypeError",
    "ValueError",
    "zip",
)

CHILD = rf"""
import copy, json, re, resource, sys, traceback
from types import MappingProxyType, SimpleNamespace

CPU_SECONDS = {CPU_SECONDS}
ADDRESS_SPACE = {MEMORY_BYTES}
SPAN_FIELDS = tuple({list(SPAN_FIELDS)!r})
BUILTIN_NAMES = tuple({list(BUILTIN_NAMES)!r})

try:
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE, ADDRESS_SPACE))
except (ValueError, OSError):
    pass
try:
    resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
except (ValueError, OSError):
    pass

def cpu_limit():
    usage = resource.getrusage(resource.RUSAGE_SELF)
    used = int(usage.ru_utime + usage.ru_stime) + 1
    _soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
    soft = min(used + CPU_SECONDS, hard)
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (soft, hard))
    except (ValueError, OSError):
        pass

def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({{k: freeze(v) for k, v in value.items()}})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value

class FrozenSpan:
    __slots__ = SPAN_FIELDS + ("_parent", "_children", "_siblings")

    def __init__(self, payload):
        for field in SPAN_FIELDS:
            object.__setattr__(self, field, freeze(copy.deepcopy(payload.get(field))))
        object.__setattr__(self, "_parent", None)
        object.__setattr__(self, "_children", ())
        object.__setattr__(self, "_siblings", ())

    def __setattr__(self, name, value):
        raise AttributeError("span is read-only")

    def __delattr__(self, name):
        raise AttributeError("span is read-only")

    @property
    def parent(self):
        return self._parent

    @property
    def children(self):
        return self._children

    @property
    def siblings(self):
        return self._siblings

def build_graph(payloads):
    nodes = {{p["span_id"]: FrozenSpan(p) for p in payloads}}
    children = {{sid: [] for sid in nodes}}
    for payload in payloads:
        parent_id = payload.get("parent_id")
        if parent_id and parent_id in nodes:
            children[parent_id].append(nodes[payload["span_id"]])
            object.__setattr__(nodes[payload["span_id"]], "_parent", nodes[parent_id])
    for sid, node in nodes.items():
        object.__setattr__(node, "_children", tuple(children[sid]))
    # Siblings need every parent's children list already sealed.
    for sid, node in nodes.items():
        parent = node.parent
        if parent is None:
            object.__setattr__(node, "_siblings", ())
        else:
            object.__setattr__(
                node,
                "_siblings",
                tuple(sib for sib in parent.children if sib.span_id != sid),
            )
    return nodes

def whitelist():
    out = {{name: __builtins__[name] if isinstance(__builtins__, dict) else getattr(__builtins__, name)
           for name in BUILTIN_NAMES}}
    # Functions, never the modules: a module attribute chain reaches os and sys.
    out["json"] = SimpleNamespace(loads=json.loads, dumps=json.dumps)
    out["re"] = SimpleNamespace(
        search=re.search, fullmatch=re.fullmatch, findall=re.findall, sub=re.sub,
        IGNORECASE=re.IGNORECASE, DOTALL=re.DOTALL, error=re.error,
    )
    return out

def load_evaluate(source):
    ns = {{"__builtins__": whitelist()}}
    exec(compile(source, "<script>", "exec"), ns, ns)
    fn = ns.get("evaluate")
    if not callable(fn):
        raise RuntimeError("script must define def evaluate(span)")
    return fn

def map_return(value):
    if value is None:
        return {{"outcome": "abstained", "score": None, "passed": None,
                "explanation": "evaluate returned None"}}
    if isinstance(value, bool):
        return {{"outcome": "scored", "score": 1.0 if value else 0.0,
                "passed": value, "explanation": ""}}
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if score < 0.0 or score > 1.0:
            return {{"outcome": "error", "score": None, "passed": None,
                    "explanation": f"score {{score}} is outside [0, 1]"}}
        return {{"outcome": "scored", "score": score, "passed": score >= 0.5,
                "explanation": ""}}
    if isinstance(value, (list, tuple)) and len(value) == 2:
        score_part, reason = value
        reason_text = "" if reason is None else str(reason)
        if isinstance(score_part, bool):
            return {{"outcome": "scored", "score": 1.0 if score_part else 0.0,
                    "passed": score_part, "explanation": reason_text}}
        if isinstance(score_part, (int, float)) and not isinstance(score_part, bool):
            score = float(score_part)
            if score < 0.0 or score > 1.0:
                return {{"outcome": "error", "score": None, "passed": None,
                        "explanation": f"score {{score}} is outside [0, 1]"}}
            return {{"outcome": "scored", "score": score, "passed": score >= 0.5,
                    "explanation": reason_text}}
    return {{"outcome": "error", "score": None, "passed": None,
            "explanation": f"evaluate returned unsupported value of type {{type(value).__name__}}"}}

def run(payload):
    node = build_graph(payload["spans"]).get(payload["span_id"])
    if node is None:
        return {{"outcome": "error", "score": None, "explanation": "span not found"}}
    try:
        return map_return(load_evaluate(payload["source"])(node))
    except Exception:
        tb = traceback.format_exc().splitlines()
        keep = [line for line in tb if ("<script>" in line or not line.startswith("  File"))]
        return {{"outcome": "error", "score": None,
                "explanation": (keep[-1] if keep else "script error")[:400]}}

def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        cpu_limit()
        try:
            result = run(json.loads(line))
        except Exception:
            result = {{"outcome": "error", "score": None, "explanation": "script runner failed"}}
        sys.stdout.write(json.dumps(result) + chr(10))
        sys.stdout.flush()

main()
"""


def audit(source: str) -> str | None:
    """Return a short violation summary, or None when the source is allowed."""
    violations: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"syntax error on line {exc.lineno}: {exc.msg}"

    evaluate_defs: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "evaluate":
            evaluate_defs.append(node)

    if not evaluate_defs:
        violations.append("script must define def evaluate(span)")
    elif len(evaluate_defs) > 1:
        violations.append("script must define evaluate exactly once")
    else:
        fn = evaluate_defs[0]
        if isinstance(fn, ast.AsyncFunctionDef):
            violations.append("evaluate must be a plain def, not async")
        args = fn.args
        positional = len(args.posonlyargs) + len(args.args)
        if (
            args.vararg is not None
            or args.kwarg is not None
            or args.kwonlyargs
            or positional != 1
            or args.defaults
        ):
            violations.append("evaluate must take exactly one positional argument: span")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            violations.append("import is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRS:
                violations.append(f"attribute {node.attr!r} is not allowed")
        elif isinstance(node, ast.Name) and node.id.startswith("_"):
            violations.append(f"name {node.id!r} is not allowed")
        elif isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue) and value.format_spec is not None:
                    violations.append("f-string format specs are not allowed")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name in FORBIDDEN_CALLS:
                violations.append(f"{name}() is not allowed")

    if not violations:
        return None
    return "; ".join(sorted(set(violations))[:5])


def clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > NEIGHBOUR_TEXT:
        keep = NEIGHBOUR_TEXT // 2
        return f"{value[:keep]} …[clipped]… {value[-keep:]}"
    if isinstance(value, dict):
        return {key: clip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clip(item) for item in value]
    return value


def serialize(span: Span, *, neighbour: bool = False) -> dict[str, Any]:
    """A neighbour's text is clipped; ``clip`` builds new containers, so it needs no copy."""
    payload = {
        "span_id": span.span_id,
        "parent_id": span.parent_span_id or None,
        "trace_id": span.trace_id,
        "span_type": span.span_type or "",
        "name": span.name or "",
        "tool": evidence.tool_name(span) if evidence.is_tool(span) else "",
        "status": evidence.status(span),
        "input": evidence.read_input(span),
        "output": evidence.read_output(span),
        "expected": evidence.read_expected(span) or None,
        "duration_s": evidence.seconds(span),
    }
    if neighbour:
        return {
            **clip(payload),
            "usage": clip(span.usage or {}),
            "attributes": clip(span.attributes or {}),
        }
    return {
        **payload,
        "usage": copy.deepcopy(span.usage or {}),
        "attributes": copy.deepcopy(span.attributes or {}),
    }


def neighbour_view(span: Span) -> dict[str, Any]:
    """Kept on the loaded span: every span and row of a trace sends the same neighbours,
    and rebuilding them per call is quadratic in the trace."""
    view = span.__dict__.get(NEIGHBOUR_VIEW)
    if view is None:
        view = span.__dict__[NEIGHBOUR_VIEW] = serialize(span, neighbour=True)
    return view


def failure(explanation: str) -> dict[str, Any]:
    return {"outcome": "error", "score": None, "explanation": explanation}


class _Sandbox:
    """One warm ``python -I`` interpreter. Checks reuse it; a death or a large RSS replaces it."""

    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="innermind_check_")
        runner_path = os.path.join(self.tmp.name, "runner.py")
        with open(runner_path, "w", encoding="utf-8") as handle:
            handle.write(CHILD)
        self.proc = subprocess.Popen(
            [sys.executable, "-I", runner_path],
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
        self.keep = True

    def rss_kb(self) -> int | None:
        try:
            with open(f"/proc/{self.proc.pid}/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])
        except OSError:
            return None
        return None

    def request(self, encoded: bytes) -> dict[str, Any]:
        stdin, stdout = self.proc.stdin, self.proc.stdout
        if stdin is None or stdout is None:
            self.keep = False
            return failure("script runner could not start")

        def write() -> None:
            try:
                stdin.write(encoded + b"\n")
                stdin.flush()
            except OSError:
                self.keep = False

        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        ready, _, _ = select.select([stdout], [], [], WALL_SECONDS)
        if not ready:
            self.keep = False
            if self.proc.poll() is None:
                self.proc.kill()
            writer.join(timeout=1)
            return failure(f"script ran longer than {WALL_SECONDS}s and was stopped")
        line = stdout.readline()
        writer.join(timeout=1)
        if not line or self.proc.poll() not in (None, 0):
            self.keep = False
            return failure("script used more memory or CPU than allowed")
        try:
            result = json.loads(line)
        except json.JSONDecodeError as exc:
            self.keep = False
            return failure(f"script runner returned invalid JSON: {exc}")
        rss = self.rss_kb()
        if rss is not None and rss > SANDBOX_RSS_KB:
            self.keep = False
        return result if isinstance(result, dict) else failure("script runner returned no result")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            with suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=2)
        if self.tmp is not None:
            self.tmp.cleanup()
            self.tmp = None


_idle: list[_Sandbox] = []
_pool_lock = threading.Lock()


def _checkout() -> _Sandbox:
    with _pool_lock:
        while _idle:
            sandbox = _idle.pop()
            if sandbox.proc.poll() is None:
                return sandbox
            sandbox.close()
    return _Sandbox()


def _checkin(sandbox: _Sandbox) -> None:
    if not sandbox.keep or sandbox.proc.poll() is not None:
        sandbox.close()
        return
    with _pool_lock:
        _idle.append(sandbox)


def _pool_request(encoded: bytes) -> dict[str, Any]:
    try:
        sandbox = _checkout()
    except OSError as exc:
        return failure(f"script runner could not start: {exc}")
    try:
        return sandbox.request(encoded)
    finally:
        _checkin(sandbox)


async def run(source: str, span: Span, trace: list[Span]) -> dict[str, Any]:
    """
    runs one script against one span in a jailed child and returns
    {"outcome": scored | abstained | error, "score", "explanation"}; never raises,
    because a deterministic failure fails the same way on a retry
    """
    violation = audit(source)
    if violation:
        return failure(violation)

    # Polled, not awaited in a thread: a cancelled waiter must never take a slot it cannot free.
    while not sandbox_slots.acquire(blocking=False):
        await asyncio.sleep(SLOT_POLL_SECONDS)
    try:
        payload = {
            "spans": [
                serialize(item) if item.span_id == span.span_id else neighbour_view(item)
                for item in trace
            ],
            "span_id": span.span_id,
            "source": source,
        }
        encoded = json.dumps(payload, default=str).encode()
        return await asyncio.to_thread(_pool_request, encoded)
    finally:
        sandbox_slots.release()
