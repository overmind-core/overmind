"""AST scan of decorator call sites → AgentManifest for ``overmind sync``.

Never imports user code. Non-literal decorator kwargs are recorded under
``unresolved`` and reported; they are never guessed.

One walk over the repository parses each file once and feeds two passes:
a call graph over every function, then the declarations. Each declared
symbol's ``calls`` are the other declared symbols reachable through it.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from overmind import __version__
from overmind.repository_snapshot import begin_repository_scan, finish_repository_scan
from overmind.slug import split_capability_reference

Role = Literal["capability", "tool", "llm", "retrieval", "function", "task"]
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef

_SKIP_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    "site-packages",
    "dist",
    "build",
    ".overmind",
    "tests",
    "test",
})

_DECORATOR_ROLES: dict[str, Role] = {
    "tool": "tool",
    "tool_call": "tool",
    "retrieval": "retrieval",
    "entry_point": "capability",
    "workflow": "function",
}
_DECLARING_DECORATORS = frozenset({"capability", "task", "observe", *_DECORATOR_ROLES})
_TYPE_ROLES: dict[str, Role] = {
    "entry_point": "capability",
    "tool": "tool",
    "tool_call": "tool",
    "llm": "llm",
    "llm_call": "llm",
    "retrieval": "retrieval",
    "workflow": "function",
    "function": "function",
}
_REACHABILITY_DEPTH = 12


@dataclass(frozen=True)
class DeclaredSymbol:
    qualname: str
    file: str
    line_start: int
    line_end: int
    role: Role
    capability: str | None = None
    slug: str | None = None
    name: str = ""
    description: str = ""
    signature: dict[str, Any] = field(default_factory=dict)
    expectations: list[dict[str, Any]] = field(default_factory=list)
    task_key: str | None = None
    unit: str | None = None
    prompt_template: str | None = None
    calls: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentManifest:
    sdk_version: str
    repository_snapshot: dict[str, Any] | None
    symbols: list[DeclaredSymbol]
    project_id: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "sdk_version": self.sdk_version,
            "project_id": self.project_id,
            "repository_snapshot": self.repository_snapshot,
            "symbols": [asdict(s) for s in self.symbols],
        }


def scan(root: Path) -> AgentManifest:
    """Pure AST scan of *root*; never imports user code."""
    root = root.resolve()
    begin_repository_scan(root)
    modules = list(_parse_modules(root))

    graph = CallGraph()
    for module in modules:
        graph.add(module)
    symbols = [symbol for module in modules for symbol in _declarations(module, graph)]

    return AgentManifest(
        sdk_version=__version__,
        repository_snapshot=finish_repository_scan(root),
        symbols=_link_calls(symbols),
    )


# --- Source --------------------------------------------------------------


@dataclass(frozen=True)
class _Module:
    rel_path: str
    dotted: str
    tree: ast.Module


def _parse_modules(root: Path) -> Iterator[_Module]:
    for file in root.rglob("*.py"):
        parts = file.relative_to(root).parts
        if _SKIP_DIRS.intersection(parts):
            continue
        rel_path = "/".join(parts)
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        yield _Module(rel_path, module_dotted_path(rel_path), tree)


def module_dotted_path(rel_path: str) -> str:
    parts = [p for p in rel_path.split("/") if p]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _qualify(module: str, name: str) -> str:
    return f"{module}.{name}" if module else name


def _bare(qualname: str) -> str:
    return qualname.rsplit(".", 1)[-1]


# --- Call graph ----------------------------------------------------------


class CallGraph:
    """Every function in the repo and the bare names it calls.

    Calls resolve by trailing name, without import tracking. That
    over-approximates reachability: it never drops a real path, only fails
    to drop a fabricated one that reuses a real name.
    """

    def __init__(self) -> None:
        self.calls: dict[str, set[str]] = {}
        self._by_bare_name: dict[str, set[str]] = {}

    def add(self, module: _Module) -> None:
        def visit(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = _qualify(module.dotted, f"{prefix}{child.name}")
                    self.calls[qualname] = {
                        _bare(dotted)
                        for inner in ast.walk(child)
                        if isinstance(inner, ast.Call) and (dotted := _dotted_name(inner.func))
                    }
                    self._by_bare_name.setdefault(child.name, set()).add(qualname)
                    visit(child, f"{prefix}{child.name}.<locals>.")
                elif isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}.")
                else:
                    visit(child, prefix)

        visit(module.tree, "")

    def reachable_names(self, qualname: str) -> set[str]:
        """Bare names reachable from *qualname*, itself included."""
        entry = self._resolve(qualname)
        if entry is None:
            return set()
        seen = {_bare(entry)}
        frontier = {entry}
        for _ in range(_REACHABILITY_DEPTH):
            next_frontier: set[str] = set()
            for caller in frontier:
                for called in self.calls.get(caller, set()) - seen:
                    seen.add(called)
                    next_frontier |= self._by_bare_name.get(called, set())
            if not next_frontier:
                break
            frontier = next_frontier
        return seen

    def _resolve(self, qualname: str) -> str | None:
        """Match at any module-prefix depth; import roots shift dotted paths."""
        if qualname in self.calls:
            return qualname
        candidates = self._by_bare_name.get(_bare(qualname), set())
        for candidate in candidates:
            if candidate.endswith(qualname) or qualname.endswith(candidate):
                return candidate
        return next(iter(candidates), None)


def _dotted_name(node: ast.expr) -> str:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def _link_calls(symbols: list[DeclaredSymbol]) -> list[DeclaredSymbol]:
    """Narrow each symbol's reachable bare names to declared qualnames."""
    declared: dict[str, set[str]] = {}
    for symbol in symbols:
        declared.setdefault(_bare(symbol.qualname), set()).add(symbol.qualname)
    return [
        replace(
            symbol,
            calls=sorted({q for name in symbol.calls for q in declared.get(name, ())} - {symbol.qualname}),
        )
        for symbol in symbols
    ]


# --- Declarations --------------------------------------------------------


def _declarations(module: _Module, graph: CallGraph) -> list[DeclaredSymbol]:
    """Declared symbols in *module*; capabilities scope the functions nested in them."""
    constants = _string_constants(module.tree)
    symbols: list[DeclaredSymbol] = []

    def declare(fn: FunctionNode, owner: str, capabilities: list[str]) -> list[DeclaredSymbol]:
        found = _declare_function(fn, owner, module.rel_path, constants, graph, capabilities)
        symbols.extend(found)
        return found

    def visit(node: ast.AST, capabilities: list[str]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                nested = capabilities
                for symbol in declare(child, module.dotted, capabilities):
                    if symbol.role == "capability" and symbol.slug:
                        nested = [*capabilities, symbol.slug]
                visit(child, nested)
            elif isinstance(child, ast.ClassDef):
                owner = _qualify(module.dotted, child.name)
                for member in child.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        declare(member, owner, capabilities)
                    else:
                        visit(member, capabilities)
            else:
                visit(child, capabilities)

    visit(module.tree, [])
    return symbols


@dataclass
class _Decorator:
    role: Role
    kwargs: dict[str, Any]
    unresolved: list[str]

    @property
    def positional(self) -> Any:
        return self.kwargs.get("__positional__")

    def text(self, key: str) -> str | None:
        value = self.kwargs.get(key)
        return value if isinstance(value, str) else None

    def task_key(self) -> str | None:
        key = self.positional or self.kwargs.get("key")
        return key if isinstance(key, str) else None


def _declare_function(
    fn: FunctionNode,
    owner: str,
    rel_path: str,
    constants: dict[str, str],
    graph: CallGraph,
    capabilities: list[str],
) -> list[DeclaredSymbol]:
    decorators = _overmind_decorators(fn, constants)
    if not decorators:
        return []
    # The first non-task decorator declares the function; a ``@task`` stacked
    # with it contributes only its key and unit.
    primary = next((d for d in decorators if d.role != "task"), decorators[0])
    stacked_task = next((d for d in decorators if d.role == "task"), None)
    enclosing = capabilities[-1] if capabilities else None

    slug = None
    description = str(primary.kwargs.get("description") or "")
    if primary.role == "capability":
        display = ""
        if isinstance(primary.positional, str):
            display, slug = split_capability_reference(primary.positional)
        slug = primary.kwargs.get("slug") or slug
        display = str(primary.kwargs.get("name") or display or slug or fn.name)
        description = description or _docstring_summary(fn)
        capability = slug
    elif primary.role == "task":
        display = str(primary.task_key() or fn.name)
        capability = enclosing
    else:
        display = str(primary.positional or primary.kwargs.get("name") or fn.name)
        capability = primary.text("capability") or enclosing
        description = description or _docstring_summary(fn)

    unit = primary.text("unit")
    if stacked_task is not None:
        unit = unit or stacked_task.kwargs.get("unit")

    qualname = _qualify(owner, fn.name)
    reachable = sorted(graph.reachable_names(qualname))
    declared = DeclaredSymbol(
        qualname=qualname,
        file=rel_path,
        line_start=fn.lineno,
        line_end=fn.end_lineno or fn.lineno,
        role=primary.role,
        capability=str(capability) if capability else None,
        slug=str(slug) if slug else None,
        name=display,
        description=description,
        signature=_signature(fn),
        expectations=_expectations(primary.kwargs.get("expectations")),
        task_key=stacked_task.task_key() if stacked_task else None,
        unit=str(unit) if unit else None,
        prompt_template=primary.text("prompt"),
        calls=reachable,
        unresolved=list(dict.fromkeys(primary.unresolved)),
    )
    task_capability = declared.capability or enclosing
    return [declared, *_inline_tasks(fn, qualname, rel_path, constants, task_capability, reachable)]


def _inline_tasks(
    fn: FunctionNode,
    qualname: str,
    rel_path: str,
    constants: dict[str, str],
    capability: str | None,
    reachable: list[str],
) -> list[DeclaredSymbol]:
    """``with task(...)`` / ``async with task(...)`` blocks in *fn*'s body."""
    tasks: list[DeclaredSymbol] = []
    for node in ast.walk(fn):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            call = item.context_expr
            if not isinstance(call, ast.Call) or _trailing_name(call.func) != "task":
                continue
            kwargs, unresolved = _literal_kwargs(call, constants)
            spec = _Decorator("task", kwargs, unresolved)
            key = spec.task_key()
            if not key or not key.strip():
                continue
            tasks.append(
                DeclaredSymbol(
                    qualname=qualname,
                    file=rel_path,
                    line_start=node.lineno,
                    line_end=node.lineno,
                    role="task",
                    capability=capability,
                    name=key,
                    signature={"params": [], "returns": ""},
                    task_key=key,
                    unit=spec.text("unit") or "turn",
                    calls=reachable,
                    unresolved=list(dict.fromkeys(unresolved)),
                )
            )
    return tasks


def _overmind_decorators(fn: FunctionNode, constants: dict[str, str]) -> list[_Decorator]:
    decorators: list[_Decorator] = []
    for node in fn.decorator_list:
        # A bare ``@capability`` / ``@tool`` is a call with no arguments.
        call = node if isinstance(node, ast.Call) else ast.Call(func=node, args=[], keywords=[])
        name = _trailing_name(call.func)
        if name not in _DECLARING_DECORATORS:
            continue
        kwargs, unresolved = _literal_kwargs(call, constants)
        decorators.append(_Decorator(_role(name, kwargs), kwargs, unresolved))
    return decorators


def _role(decorator: str, kwargs: dict[str, Any]) -> Role:
    if decorator == "capability":
        return "capability"
    if decorator == "task":
        return "task"
    if decorator == "observe":
        span_type = kwargs.get("type")
        return _TYPE_ROLES.get(span_type, "function") if isinstance(span_type, str) else "function"
    return _DECORATOR_ROLES[decorator]


def _trailing_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# --- Literal arguments ---------------------------------------------------


def _literal(node: ast.AST | None) -> Any | None:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return None


def _literal_kwargs(call: ast.Call, constants: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Literal arguments of *call*, plus the names of the ones that are not.

    The first positional argument lands under ``__positional__``. A keyword
    whose value names a module-level string constant resolves through
    *constants*.
    """
    kwargs: dict[str, Any] = {}
    unresolved: list[str] = []
    if call.args:
        value = _literal(call.args[0])
        if value is None:
            unresolved.append("__positional__")
        else:
            kwargs["__positional__"] = value
    for keyword in call.keywords:
        key = keyword.arg or "**"
        value = _expectation_list(keyword.value) if key == "expectations" else _literal(keyword.value)
        if value is not None:
            kwargs[key] = value
        elif isinstance(keyword.value, ast.Name) and keyword.value.id in constants:
            kwargs[key] = constants[keyword.value.id]
        else:
            unresolved.append(key)
    return kwargs, unresolved


def _string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level string assignments, usable as ``prompt=NAME``."""
    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, _literal(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            name, value = node.target.id, _literal(node.value)
        else:
            continue
        if isinstance(value, str):
            constants[name] = value
    return constants


def _expectation_list(node: ast.AST) -> Any | None:
    """A literal, or a list mixing literals and ``Expectation(kind, spec)`` calls."""
    if not isinstance(node, ast.List):
        return _literal(node)
    items: list[Any] = []
    for element in node.elts:
        item = _expectation_call(element) if isinstance(element, ast.Call) else _literal(element)
        if item is None:
            return None
        items.append(item)
    return items


def _expectation_call(node: ast.Call) -> dict[str, Any] | None:
    if _trailing_name(node.func) != "Expectation":
        return None
    arguments = dict(zip(("kind", "spec"), (_literal(arg) for arg in node.args[:2]), strict=False))
    arguments.update({kw.arg: _literal(kw.value) for kw in node.keywords if kw.arg in {"kind", "spec"}})
    kind, spec = arguments.get("kind"), arguments.get("spec")
    if not isinstance(kind, str) or not isinstance(spec, str):
        return None
    return {"kind": kind, "spec": spec}


def _expectations(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    expectations: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            expectations.append(item)
        elif isinstance(item, str):
            expectations.append({"kind": "constraint", "spec": item})
    return expectations


# --- Function shape ------------------------------------------------------


def _signature(fn: FunctionNode) -> dict[str, Any]:
    positional = [*fn.args.posonlyargs, *fn.args.args]
    first_default = len(positional) - len(fn.args.defaults)
    has_default = {arg.arg for arg in positional[first_default:]}
    has_default |= {
        arg.arg for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=True) if default is not None
    }
    params = [
        {"name": arg.arg, "type": _annotation(arg.annotation), "required": arg.arg not in has_default}
        for arg in [*fn.args.args, *fn.args.kwonlyargs]
        if arg.arg not in ("self", "cls")
    ]
    return {"params": params, "returns": _annotation(fn.returns)}


def _annotation(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _docstring_summary(fn: FunctionNode) -> str:
    return (ast.get_docstring(fn) or "").strip().split("\n\n", 1)[0].strip()
