"""AST-based instrumentation for overmind-sdk observe() tracing.

Transforms agent source code to:
  1. Add ``from overmind_sdk import init as overmind_init, observe`` and
     ``overmind_init()`` at the top of the file.
  2. Add ``@observe()`` decorator above every function definition.
  3. Remove ``from overclaw.core.tracer import ...`` imports.
  4. Replace ``call_llm(model, messages, ...)`` with
     ``litellm.completion(model, messages, ...)``.
  5. Replace ``call_tool(name, args, fn)`` with ``fn(**args)``.

The transform operates on source text (not AST rewriting) so it
preserves formatting, comments, and decorators.
"""

from __future__ import annotations

import ast
import re


def instrument_source(source: str) -> str:
    """Apply all instrumentation transforms to *source* and return the result."""
    source = _add_overmind_imports(source)
    source = _remove_overclaw_tracer_imports(source)
    source = _replace_call_llm(source)
    source = _replace_call_tool(source)
    source = _add_observe_decorators(source)
    return source


# ---------------------------------------------------------------------------
# 1. Add overmind-sdk imports + init at the top
# ---------------------------------------------------------------------------

_OVERMIND_IMPORT = "from overmind_sdk import init as overmind_init, observe"
_OVERMIND_INIT = "overmind_init()"


def _add_overmind_imports(source: str) -> str:
    # If already has the full import with observe, nothing to do.
    if _OVERMIND_IMPORT in source:
        return source

    # If overmind_sdk is imported but without ``observe``, patch the import.
    if "from overmind_sdk import" in source and "observe" not in source:
        return re.sub(
            r"(from overmind_sdk import .+)",
            r"\1, observe",
            source,
            count=1,
        )

    if "from overmind_sdk import" in source or "import overmind_sdk" in source:
        # Already imported (and observe is present or unused) — skip.
        return source

    lines = source.split("\n")

    insert_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (
            stripped.startswith("#")
            or stripped == ""
            or stripped.startswith('"""')
            or stripped.startswith("'''")
        ):
            if stripped.startswith('"""') or stripped.startswith("'''"):
                quote = stripped[:3]
                if stripped.count(quote) == 1:
                    for j in range(i + 1, len(lines)):
                        if quote in lines[j]:
                            insert_idx = j + 1
                            break
                    else:
                        insert_idx = i + 1
                else:
                    insert_idx = i + 1
            else:
                insert_idx = i + 1
            continue
        break

    lines.insert(insert_idx, _OVERMIND_IMPORT)
    lines.insert(insert_idx + 1, _OVERMIND_INIT)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 2. Remove overclaw tracer imports
# ---------------------------------------------------------------------------

_OVERCLAW_IMPORT_RE = re.compile(
    r"^from\s+overclaw\.core\.tracer\s+import\s+[^\n]+\n?", re.MULTILINE
)


def _remove_overclaw_tracer_imports(source: str) -> str:
    return _OVERCLAW_IMPORT_RE.sub("", source)


# ---------------------------------------------------------------------------
# 3. Replace call_llm(...) → litellm.completion(...)
# ---------------------------------------------------------------------------


def _replace_call_llm(source: str) -> str:
    """Replace ``call_llm(`` with ``litellm.completion(`` everywhere."""
    if "call_llm(" not in source:
        return source

    source = source.replace("call_llm(", "litellm.completion(")

    if "import litellm" not in source:
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if line.strip().startswith(("import ", "from ")):
                continue
            if line.strip() == _OVERMIND_INIT:
                continue
            if line.strip() == "" or line.strip().startswith("#"):
                continue
            lines.insert(i, "import litellm")
            break
        source = "\n".join(lines)

    return source


# ---------------------------------------------------------------------------
# 4. Replace call_tool(name, args, fn) → fn(**args)
# ---------------------------------------------------------------------------

_CALL_TOOL_RE = re.compile(
    r"""call_tool\(\s*
        (?P<name>[^,]+),\s*   # first arg: name (ignored)
        (?P<args>[^,]+),\s*   # second arg: args dict
        (?P<fn>[^)]+)          # third arg: callable
    \)""",
    re.VERBOSE,
)


def _replace_call_tool(source: str) -> str:
    """Replace ``call_tool(name, args, fn)`` with ``fn(**args)``."""
    if "call_tool(" not in source:
        return source

    def _sub(m: re.Match) -> str:
        args = m.group("args").strip()
        fn = m.group("fn").strip()
        return f"{fn}(**{args})"

    return _CALL_TOOL_RE.sub(_sub, source)


# ---------------------------------------------------------------------------
# 5. Add @observe() decorator to every function
# ---------------------------------------------------------------------------


def _add_observe_decorators(source: str) -> str:
    """Add ``@observe()`` above every ``def`` / ``async def`` that lacks it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source

    lines = source.split("\n")
    func_lines: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            already_has = any(
                (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Name)
                    and d.func.id == "observe"
                )
                or (isinstance(d, ast.Name) and d.id == "observe")
                or (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "observe"
                )
                for d in node.decorator_list
            )
            if not already_has:
                func_lines.append(node.lineno)

    # Insert in reverse order to preserve line numbers
    for lineno in sorted(func_lines, reverse=True):
        idx = lineno - 1
        existing_line = lines[idx]
        indent = len(existing_line) - len(existing_line.lstrip())
        decorator = " " * indent + "@observe()"
        lines.insert(idx, decorator)

    return "\n".join(lines)


def is_instrumented(source: str) -> bool:
    """Return True if the source already has overmind-sdk instrumentation.

    Requires both the import (with ``observe``) and at least one
    ``@observe()`` decorator to be present.
    """
    has_import = "observe" in source and (
        "from overmind_sdk import" in source or "import overmind_sdk" in source
    )
    has_decorator = "@observe()" in source
    return has_import and has_decorator
