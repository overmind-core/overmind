"""Import-free Python scripts that grade one span at a time.

Users write ``def evaluate(span): ...``. The script sees only the span, its
parent, children, siblings, and their attributes. No imports. Execution is an
AST audit plus a ``python3 -I`` child under CPU/memory/wall limits.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

CPU_SECONDS = 5
WALL_SECONDS = 15
_ADDRESS_SPACE_BYTES = 512 * 1024**2
SPAN_CAP = 2_000

_STATUS_LABELS = {0: "UNSET", 1: "OK", 2: "ERROR"}

_FORBIDDEN_ATTRS = frozenset(
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

_BUILTIN_NAMES = (
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
_SPAN_FIELDS = (
    "span_id",
    "trace_id",
    "parent_span_id",
    "span_type",
    "operation",
    "service_name",
    "status_code",
    "status_message",
    "start_time_ns",
    "end_time_ns",
    "duration_ns",
    "attributes",
    "events",
    "resource_attrs",
)


@dataclass(frozen=True)
class ScriptResult:
    outcome: str  # scored | abstained | not_applicable | error
    score: float | None = None
    passed: bool | None = None
    explanation: str = ""


def source_identifier(source: str) -> str:
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"script:{digest}"


def serialize_span(span) -> dict[str, Any]:
    status = span.status_code
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id or None,
        "span_type": span.span_type or "",
        "operation": span.operation or "",
        "service_name": span.service_name or "",
        "status_code": _STATUS_LABELS.get(status, status if isinstance(status, str) else "UNSET"),
        "status_message": span.status_message or "",
        "start_time_ns": int(span.start_time_ns or 0),
        "end_time_ns": int(span.end_time_ns or 0),
        "duration_ns": int(span.duration_ns or 0),
        "attributes": copy.deepcopy(span.attributes or {}),
        "events": copy.deepcopy(span.events or []),
        "resource_attrs": copy.deepcopy(span.resource_attrs or {}),
    }


def matches_filter(span_payload: dict[str, Any], span_filter: dict[str, Any] | None) -> bool:
    if not span_filter:
        return True
    span_type = span_filter.get("span_type")
    if span_type and span_payload.get("span_type") != span_type:
        return False
    operation = span_filter.get("operation")
    return not (operation and span_payload.get("operation") != operation)


_FORBIDDEN_CALLS = frozenset(
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


def audit(source: str) -> list[str]:
    violations: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]

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
            if node.attr.startswith("_") or node.attr in _FORBIDDEN_ATTRS:
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
            if name in _FORBIDDEN_CALLS:
                violations.append(f"{name}() is not allowed")

    return sorted(set(violations))


def map_return(value: Any) -> ScriptResult:
    if value is None:
        return ScriptResult(outcome="abstained", explanation="evaluate returned None")
    if isinstance(value, bool):
        return ScriptResult(
            outcome="scored",
            score=1.0 if value else 0.0,
            passed=value,
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        score = float(value)
        if score < 0.0 or score > 1.0:
            return ScriptResult(
                outcome="error",
                explanation=f"score {score} is outside [0, 1]",
            )
        return ScriptResult(outcome="scored", score=score, passed=score >= 0.5)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        score_part, reason = value
        reason_text = "" if reason is None else str(reason)
        if isinstance(score_part, bool):
            return ScriptResult(
                outcome="scored",
                score=1.0 if score_part else 0.0,
                passed=score_part,
                explanation=reason_text,
            )
        if isinstance(score_part, (int, float)) and not isinstance(score_part, bool):
            score = float(score_part)
            if score < 0.0 or score > 1.0:
                return ScriptResult(
                    outcome="error",
                    explanation=f"score {score} is outside [0, 1]",
                )
            return ScriptResult(
                outcome="scored",
                score=score,
                passed=score >= 0.5,
                explanation=reason_text,
            )
    return ScriptResult(
        outcome="error",
        explanation=f"evaluate returned unsupported value of type {type(value).__name__}",
    )


_CHILD = rf"""
import copy, json, resource, sys, traceback
from types import MappingProxyType

CPU_SECONDS = {CPU_SECONDS}
ADDRESS_SPACE = {_ADDRESS_SPACE_BYTES}
SPAN_FIELDS = tuple({list(_SPAN_FIELDS)!r})
BUILTIN_NAMES = tuple({list(_BUILTIN_NAMES)!r})

for limit, ceiling in (
    (resource.RLIMIT_CPU, CPU_SECONDS),
    (resource.RLIMIT_AS, ADDRESS_SPACE),
):
    try:
        resource.setrlimit(limit, (ceiling, ceiling))
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
        parent_id = payload.get("parent_span_id")
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

payload = json.load(sys.stdin)
nodes = build_graph(payload["spans"])
results = {{}}
for job in payload["jobs"]:
    name = job["name"]
    try:
        fn = load_evaluate(job["source"])
    except Exception as exc:
        err = str(exc).splitlines()[-1][:400]
        for span_id in job["span_ids"]:
            results[f"{{name}}\0{{span_id}}"] = {{
                "outcome": "error", "score": None, "passed": None,
                "explanation": err,
            }}
        continue
    for span_id in job["span_ids"]:
        node = nodes.get(span_id)
        if node is None:
            results[f"{{name}}\0{{span_id}}"] = {{
                "outcome": "error", "score": None, "passed": None,
                "explanation": "span not found",
            }}
            continue
        try:
            value = fn(node)
            results[f"{{name}}\0{{span_id}}"] = map_return(value)
        except Exception:
            tb = traceback.format_exc().splitlines()
            keep = [line for line in tb if ("<script>" in line or not line.startswith("  File"))]
            results[f"{{name}}\0{{span_id}}"] = {{
                "outcome": "error", "score": None, "passed": None,
                "explanation": (keep[-1] if keep else "script error")[:400],
            }}
json.dump(results, sys.stdout)
"""


def run(
    jobs: list[dict[str, Any]],
    spans: list[dict[str, Any]],
) -> dict[tuple[str, str], ScriptResult]:
    """Run every ``(name, source)`` job against its filtered ``span_ids``.

    ``jobs`` entries: ``{"name", "source", "span_ids"}``.
    Returns ``{(name, span_id): ScriptResult}``.
    """
    out: dict[tuple[str, str], ScriptResult] = {}
    if not jobs:
        return out

    for job in jobs:
        violations = audit(job["source"])
        if violations:
            explanation = "; ".join(violations[:5])
            for span_id in job["span_ids"]:
                out[(job["name"], span_id)] = ScriptResult(outcome="error", explanation=explanation)
            job["span_ids"] = []

    runnable = [job for job in jobs if job["span_ids"]]
    if not runnable:
        return out

    payload = {"spans": spans, "jobs": runnable}
    with tempfile.TemporaryDirectory(prefix="span_script_") as tmp:
        runner_path = os.path.join(tmp, "_runner.py")
        with open(runner_path, "w", encoding="utf-8") as fh:
            fh.write(_CHILD)
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HOME": tmp,
        }
        try:
            proc = subprocess.run(  # noqa: S603 — audited script, jailed child
                [sys.executable, "-I", runner_path],
                input=json.dumps(payload, default=str),
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=WALL_SECONDS,
            )
        except subprocess.TimeoutExpired:
            explanation = f"script ran longer than {WALL_SECONDS}s and was stopped"
            for job in runnable:
                for span_id in job["span_ids"]:
                    out[(job["name"], span_id)] = ScriptResult(
                        outcome="error", explanation=explanation
                    )
            return out
        except (OSError, subprocess.SubprocessError) as exc:
            explanation = f"script runner could not start: {exc}"
            for job in runnable:
                for span_id in job["span_ids"]:
                    out[(job["name"], span_id)] = ScriptResult(
                        outcome="error", explanation=explanation
                    )
            return out

        if proc.returncode != 0:
            failure = (proc.stderr or proc.stdout or "").strip().splitlines()
            explanation = (failure[-1] if failure else f"exited {proc.returncode}")[:400]
            if proc.returncode in (-9, 137):
                explanation = explanation or "script used more memory or CPU than allowed"
            for job in runnable:
                for span_id in job["span_ids"]:
                    out.setdefault(
                        (job["name"], span_id),
                        ScriptResult(outcome="error", explanation=explanation),
                    )
            return out

        try:
            raw = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            explanation = f"script runner returned invalid JSON: {exc}"
            for job in runnable:
                for span_id in job["span_ids"]:
                    out[(job["name"], span_id)] = ScriptResult(
                        outcome="error", explanation=explanation
                    )
            return out

        for key, value in raw.items():
            name, _, span_id = key.partition("\0")
            out[(name, span_id)] = ScriptResult(
                outcome=str(value.get("outcome") or "error"),
                score=value.get("score"),
                passed=value.get("passed"),
                explanation=str(value.get("explanation") or "")[:2000],
            )
        for job in runnable:
            for span_id in job["span_ids"]:
                out.setdefault(
                    (job["name"], span_id),
                    ScriptResult(outcome="error", explanation="no result from script runner"),
                )
        return out
