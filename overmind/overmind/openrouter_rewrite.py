"""Deterministic OpenRouter rewrite — Anthropic/OpenAI constructors → env-driven OpenRouter.

Used by the ``/overmind backtest`` skill so provider translation is code, not a
prompt. Leftover call sites (ChatAnthropic, Google genai, …) are reported for
the agent to patch.
"""

from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from pathlib import Path

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_SKIP_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".overmind",
    ".tox",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    "site-packages",
})
_OPENAI_CTORS = frozenset({"OpenAI", "AsyncOpenAI"})
_ANTHROPIC_CTORS = frozenset({"Anthropic", "AsyncAnthropic"})
_CHAT_OPENAI = frozenset({"ChatOpenAI"})
_LEFTOVER_CTORS = frozenset({
    "ChatAnthropic",
    "ChatGoogleGenerativeAI",
    "GenerativeModel",
    "AnthropicVertex",
})
_MODEL_CALL_ATTRS = frozenset({
    "create",
    "generate",
    "ainvoke",
    "invoke",
    "generate_content",
    "completions",
    "messages",
    "chat",
    "responses",
})
_ENV_FILES = (".env", ".env.local", ".env.development", ".env.example")


@dataclass
class RewriteReport:
    changed: list[str] = field(default_factory=list)
    leftover: list[str] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "changed": list(self.changed),
            "leftover": list(self.leftover),
            "env_files": list(self.env_files),
        }


def _line_starts(src: str) -> list[int]:
    starts = [0]
    for i, ch in enumerate(src):
        if ch == "\n":
            starts.append(i + 1)
    return starts


def _abs(starts: list[int], lineno: int, col: int) -> int:
    return starts[lineno - 1] + col


def _span(starts: list[int], node: ast.AST) -> tuple[int, int] | None:
    lineno = getattr(node, "lineno", None)
    end_lineno = getattr(node, "end_lineno", None)
    col = getattr(node, "col_offset", None)
    end_col = getattr(node, "end_col_offset", None)
    if None in (lineno, end_lineno, col, end_col):
        return None
    return _abs(starts, lineno, col), _abs(starts, end_lineno, end_col)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _attr_chain(node: ast.AST) -> list[str]:
    names: list[str] = []
    cur: ast.AST | None = node
    while isinstance(cur, ast.Attribute):
        names.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        names.append(cur.id)
    names.reverse()
    return names


def _kw(node: ast.Call, name: str) -> ast.keyword | None:
    return next((k for k in node.keywords if k.arg == name), None)


def _str_const(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _already_openrouter(node: ast.AST | None) -> bool:
    text = _str_const(node) or ""
    return "openrouter.ai" in text


def _openai_ctor_source(node: ast.Call, *, name: str) -> str:
    kept = [f"{k.arg}={ast.unparse(k.value)}" for k in node.keywords if k.arg and k.arg not in {"api_key", "base_url"}]
    parts = [
        'api_key=os.environ["OPENROUTER_API_KEY"]',
        f'base_url="{OPENROUTER_BASE_URL}"',
        *kept,
    ]
    positional = [ast.unparse(a) for a in node.args]
    return f"{name}({', '.join([*positional, *parts])})"


def _has_import(tree: ast.AST, module: str, name: str | None = None) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == module or a.name.startswith(module + ".") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and node.module == module:
            if name is None:
                return True
            if any(a.name == name for a in node.names):
                return True
    return False


def _has_name_import_os(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == "os" or a.name.startswith("os.") for a in node.names):
            return True
    return False


def _is_docstring(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Expr)
        and isinstance(getattr(node, "value", None), ast.Constant)
        and isinstance(node.value.value, str)
    )


def _import_insert_pos(src: str) -> int:
    """After shebang, module docstring, and ``from __future__`` — never before them."""
    starts = _line_starts(src)
    pos = 0
    first = src.lstrip("\ufeff")
    offset = len(src) - len(first)
    if first.startswith("#!") or first.startswith("# -*-") or first.startswith("# coding"):
        nl = first.find("\n")
        pos = offset + (nl + 1 if nl != -1 else len(first))
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return pos
    body = list(tree.body)
    idx = 0
    if body and _is_docstring(body[0]):
        end = body[0].end_lineno or 1
        pos = starts[end] if end < len(starts) else len(src)
        idx = 1
    last_future = None
    for node in body[idx:]:
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            last_future = node
        else:
            break
    if last_future is not None:
        end = last_future.end_lineno or last_future.lineno
        pos = starts[end] if end < len(starts) else len(src)
    return pos


def _insert_imports(src: str, *, need_os: bool, need_openai: bool, async_client: bool) -> str:
    if not need_os and not need_openai:
        return src
    lines: list[str] = []
    if need_os:
        lines.append("import os")
    if need_openai:
        name = "AsyncOpenAI" if async_client else "OpenAI"
        lines.append(f"from openai import {name}")
    block = "\n".join(lines) + "\n"
    pos = _import_insert_pos(src)
    return src[:pos] + block + src[pos:]


def _rewrite_python(path: Path, src: str) -> tuple[str, list[str], bool]:
    leftover: list[str] = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        leftover.append(f"{path}: unparseable")
        return src, leftover, False

    starts = _line_starts(src)
    replacements: list[tuple[int, int, str]] = []
    need_os = False
    need_openai = False
    used_async = False
    rewrote_anthropic = False

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        span = _span(starts, node)
        if span is None:
            continue
        start, end = span
        rel = f"{path}:{node.lineno}"

        if name in _LEFTOVER_CTORS:
            leftover.append(rel)
            continue

        if name in _ANTHROPIC_CTORS:
            ctor_name = "AsyncOpenAI" if name.startswith("Async") else "OpenAI"
            used_async = used_async or ctor_name.startswith("Async")
            replacements.append((start, end, _openai_ctor_source(node, name=ctor_name)))
            need_os = True
            need_openai = True
            rewrote_anthropic = True
            continue

        if name in _OPENAI_CTORS:
            base = _kw(node, "base_url")
            if base is not None and _already_openrouter(base.value):
                continue
            replacements.append((start, end, _openai_ctor_source(node, name=name)))
            need_os = True
            continue

        if name in _CHAT_OPENAI:
            base = _kw(node, "openai_api_base") or _kw(node, "base_url")
            if base is not None and _already_openrouter(base.value):
                continue
            keywords = [
                k
                for k in node.keywords
                if k.arg not in {"openai_api_base", "base_url", "openai_api_key", "api_key", "model"}
            ]
            env_key = ast.Subscript(
                value=ast.Attribute(value=ast.Name(id="os", ctx=ast.Load()), attr="environ", ctx=ast.Load()),
                slice=ast.Constant(value="OPENROUTER_API_KEY"),
                ctx=ast.Load(),
            )
            new_kws = [
                ast.keyword(arg="openai_api_base", value=ast.Constant(value=OPENROUTER_BASE_URL)),
                ast.keyword(arg="openai_api_key", value=env_key),
            ]
            model_kw = _kw(node, "model")
            original_model = _str_const(model_kw.value) if model_kw else None
            if original_model:
                new_kws.append(
                    ast.keyword(
                        arg="model",
                        value=ast.parse(
                            f"os.environ.get('OPENROUTER_MODEL', {original_model!r})",
                            mode="eval",
                        ).body,
                    )
                )
            elif model_kw is not None:
                new_kws.append(model_kw)
            new_call = ast.Call(func=node.func, args=node.args, keywords=[*new_kws, *keywords])
            replacements.append((start, end, ast.unparse(new_call)))
            need_os = True
            continue

        model_kw = _kw(node, "model")
        original_model = _str_const(model_kw.value) if model_kw else None
        if original_model is None or original_model.startswith("$"):
            continue
        chain = _attr_chain(node.func)
        if not _MODEL_CALL_ATTRS.intersection(chain) and name not in _MODEL_CALL_ATTRS:
            continue
        if "OPENROUTER_MODEL" in ast.dump(model_kw.value):  # type: ignore[union-attr]
            continue
        value_span = _span(starts, model_kw.value)
        if value_span is None:
            continue
        replacements.append((value_span[0], value_span[1], f"os.environ.get('OPENROUTER_MODEL', {original_model!r})"))
        need_os = True

    if not replacements and not rewrote_anthropic:
        return src, leftover, False

    replacements.sort(key=lambda item: item[0], reverse=True)
    new_src = src
    for start, end, text in replacements:
        new_src = new_src[:start] + text + new_src[end:]

    if rewrote_anthropic:
        new_src = new_src.replace(".messages.create(", ".chat.completions.create(")

    try:
        tree_after = ast.parse(new_src) if new_src != src else tree
    except SyntaxError:
        leftover.append(f"{path}: rewrite produced invalid Python")
        return src, leftover, False
    inject_os = need_os and not _has_name_import_os(tree_after)
    inject_openai = need_openai and not (
        _has_import(tree_after, "openai", "OpenAI") or _has_import(tree_after, "openai", "AsyncOpenAI")
    )
    new_src = _insert_imports(new_src, need_os=inject_os, need_openai=inject_openai, async_client=used_async)
    return new_src, leftover, new_src != src


def _rewrite_env_file(path: Path) -> bool:
    original = path.read_text()
    lines = original.splitlines(keepends=True)
    keys = {
        "OPENAI_BASE_URL": f"OPENAI_BASE_URL={OPENROUTER_BASE_URL}\n",
        "OPENAI_API_BASE": f"OPENAI_API_BASE={OPENROUTER_BASE_URL}\n",
    }
    seen: set[str] = set()
    out: list[str] = []
    changed = False
    for line in lines:
        stripped = line.strip()
        assigned = stripped.split("=", 1)[0] if "=" in stripped and not stripped.startswith("#") else ""
        if assigned in keys:
            seen.add(assigned)
            replacement = keys[assigned]
            if line != replacement and not line.startswith(replacement.rstrip("\n")):
                out.append(replacement)
                changed = True
            else:
                out.append(line)
        else:
            out.append(line)
    for key, line in keys.items():
        if key not in seen:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(line)
            changed = True
    if not changed:
        return False
    path.write_text("".join(out))
    return True


def rewrite_repo(root: str | os.PathLike[str] = ".") -> RewriteReport:
    """Rewrite LLM clients under *root* onto OpenRouter. Returns what changed."""
    base = Path(root).resolve()
    report = RewriteReport()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            path = Path(dirpath) / name
            rel = str(path.relative_to(base))
            if name.endswith(".py"):
                try:
                    src = path.read_text()
                except (OSError, UnicodeDecodeError):
                    continue
                if len(src) > 400_000:
                    continue
                new_src, leftover, changed = _rewrite_python(path.relative_to(base), src)
                report.leftover.extend(leftover)
                if changed:
                    path.write_text(new_src)
                    report.changed.append(rel)
            elif name in _ENV_FILES:
                if _rewrite_env_file(path):
                    report.env_files.append(rel)
    return report
