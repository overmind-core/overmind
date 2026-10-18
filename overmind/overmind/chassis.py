"""AST function inventory and call graph — the facts the local scan must not contradict."""

from __future__ import annotations

import ast
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import typer

from overmind import typescript_ast
from overmind.languages import PYTHON, TYPESCRIPT, detect_language, language_of_path, normalise_language, source_files
from overmind.repository_snapshot import begin_repository_scan

logger = logging.getLogger(__name__)


@dataclass
class Chassis:
    functions: dict[str, str] = field(default_factory=dict)
    # caller qualname -> called names (bare trailing segment, over-approximate)
    calls: dict[str, set[str]] = field(default_factory=dict)


def module_dotted_path(rel_path: str) -> str:
    parts = [p for p in rel_path.split("/") if p]
    if parts and (language := language_of_path(parts[-1])):
        parts[-1] = os.path.splitext(parts[-1])[0]
        if language == PYTHON and parts[-1] == "__init__":
            parts = parts[:-1]
    return ".".join(parts)


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    cursor = node
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if isinstance(cursor, ast.Name):
        parts.append(cursor.id)
    return ".".join(reversed(parts))


def _walk_functions(tree: ast.Module, rel_path: str, module: str, chassis: Chassis) -> None:
    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                full = f"{module}.{qualname}" if module else qualname
                chassis.functions[full] = rel_path
                called: set[str] = set()
                for inner in ast.walk(child):
                    if isinstance(inner, ast.Call):
                        dotted = _dotted(inner.func)
                        if dotted:
                            called.add(dotted.rsplit(".", 1)[-1])
                chassis.calls[full] = called
                visit(child, f"{qualname}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")


def _scan_language(repo_root: str | Path, language: str | None) -> str:
    return normalise_language(language) or detect_language(repo_root)


def extract_chassis(clone_dir: str, language: str | None = None) -> Chassis:
    """``language`` is ``python`` or ``typescript`` (JavaScript included); unset, the repository's
    majority language."""
    chassis = Chassis()
    language = _scan_language(clone_dir, language)
    for file, rel_path in source_files(clone_dir, language):
        module = module_dotted_path(rel_path)
        try:
            if language == TYPESCRIPT:
                if typescript_ast.is_bundle(rel_path) or file.stat().st_size > typescript_ast.MAX_SOURCE_BYTES:
                    continue
                typescript_ast.add_functions(file.read_bytes(), rel_path, module, chassis)
            else:
                _walk_functions(ast.parse(file.read_text(encoding="utf-8", errors="ignore")), rel_path, module, chassis)
        except (OSError, SyntaxError):
            continue
    return chassis


def chassis_digest(chassis: Chassis, *, max_files: int = 120, max_per_file: int = 12) -> str:
    """Capped so a large repo cannot flood the scan prompt; the scan still has
    the full source for anything elided."""
    by_file: dict[str, list[str]] = {}
    for qualname, rel_path in chassis.functions.items():
        by_file.setdefault(rel_path, []).append(qualname)
    lines = [f"{len(chassis.functions)} functions across {len(by_file)} files"]
    for rel_path in sorted(by_file)[:max_files]:
        names = sorted(by_file[rel_path])
        shown = ", ".join(names[:max_per_file])
        extra = f" (+{len(names) - max_per_file} more)" if len(names) > max_per_file else ""
        lines.append(f"{rel_path}: {shown}{extra}")
    if len(by_file) > max_files:
        lines.append(f"... {len(by_file) - max_files} more files elided")
    return "\n".join(lines)


def _name_index(chassis: Chassis) -> dict[str, set[str]]:
    """Name-based resolution (no import tracking) over-approximates reachability:
    it never wrongly drops a real path, only fails to drop a fabricated one
    that reuses a real name."""
    index: dict[str, set[str]] = {}
    for qualname in chassis.functions:
        index.setdefault(qualname.rsplit(".", 1)[-1], set()).add(qualname)
    return index


def _resolve(qualname: str, chassis: Chassis, index: dict[str, set[str]]) -> str | None:
    """A claimed qualname resolved against the chassis at any module-prefix
    depth (import roots shift dotted paths)."""
    if qualname in chassis.functions:
        return qualname
    candidates = index.get(qualname.rsplit(".", 1)[-1], set())
    for candidate in candidates:
        if candidate.endswith(qualname) or qualname.endswith(candidate):
            return candidate
    return next(iter(candidates), None)


def reachable_names(entry_qualname: str, chassis: Chassis, max_depth: int = 12) -> set[str]:
    index = _name_index(chassis)
    entry = _resolve(entry_qualname, chassis, index)
    if entry is None:
        return set()
    seen_names: set[str] = {entry.rsplit(".", 1)[-1]}
    frontier = {entry}
    for _ in range(max_depth):
        next_frontier: set[str] = set()
        for qualname in frontier:
            for called in chassis.calls.get(qualname, ()):
                if called in seen_names:
                    continue
                seen_names.add(called)
                next_frontier.update(index.get(called, set()))
        if not next_frontier:
            break
        frontier = next_frontier
    return seen_names


def verify_trajectory_claims(card: dict[str, Any], chassis: Chassis) -> None:
    """Unverifiable entries are kept, stamped ``verified: False`` — tiered, not dropped."""
    index = _name_index(chassis)
    for entry in card.get("trajectory_map") or []:
        if not isinstance(entry, dict):
            continue
        anchors = [str(a) for a in entry.get("anchors") or [] if str(a).strip()]
        if not anchors:
            entry["verified"] = False
            continue
        if _resolve(anchors[0], chassis, index) is None:
            entry["verified"] = False
            continue
        reachable = reachable_names(anchors[0], chassis)
        entry["verified"] = all(a.rsplit(".", 1)[-1] in reachable for a in anchors)


def stamp_capability_cards(data: dict[str, Any], repo_root: str | Path, language: str | None = None) -> None:
    """Best-effort: an unextractable repo leaves every entry unstamped."""
    try:
        chassis = extract_chassis(str(repo_root), language or detect_language(repo_root, data))
    except Exception:
        logger.warning("chassis extraction failed; trajectory entries stay unverified")
        return
    for raw in data.get("capabilities") or []:
        if isinstance(raw, dict) and isinstance(raw.get("capability_card"), dict):
            verify_trajectory_claims(raw["capability_card"], chassis)


_ANSWER_DECORATORS = {"observe", "entry_point", "workflow"}
_STEP_TYPES = {"tool", "tool_call", "retrieval", "TOOL", "RETRIEVAL"}


@dataclass
class _Declared:
    qualname: str
    span_name: str
    expectations: list[str]
    capability_id: str = ""


def _literal_str(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _literal_strings(node: ast.expr | None) -> list[str]:
    if not isinstance(node, (ast.List, ast.Tuple)):
        return []
    return [
        item.value.strip()
        for item in node.elts
        if isinstance(item, ast.Constant) and isinstance(item.value, str) and item.value.strip()
    ]


def _declared_on(func: ast.FunctionDef | ast.AsyncFunctionDef, qualname: str) -> _Declared | None:
    """The ``expectations=`` and ``capability_id`` on an Overmind decorator. Tool and
    retrieval expectations are skipped: they describe a step, not the answer."""
    for decorator in func.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        name = _dotted(decorator.func).rsplit(".", 1)[-1]
        if name not in _ANSWER_DECORATORS:
            continue
        kwargs = {kw.arg: kw.value for kw in decorator.keywords if kw.arg}
        span_type = kwargs.get("type")
        if isinstance(span_type, ast.Constant) and span_type.value in _STEP_TYPES:
            return None
        if isinstance(span_type, ast.Attribute) and span_type.attr in _STEP_TYPES:
            return None
        expectations = _literal_strings(kwargs.get("expectations"))
        capability_id = _literal_str(kwargs.get("capability_id")) or ""
        if not expectations and not capability_id:
            return None
        span_name = kwargs.get("span_name", decorator.args[0] if decorator.args else None)
        return _Declared(
            qualname=qualname,
            # A span named per call by a callable has no static name to scope to.
            span_name=qualname if span_name is None else _literal_str(span_name) or "",
            expectations=expectations,
            capability_id=capability_id,
        )
    return None


def declared_expectations(repo_root: str | Path) -> dict[str, list[dict[str, str]]]:
    """Capability id -> the ``expectations=`` its decorators declare, each with the span it
    is declared on. A decorator without ``capability_id`` belongs to every entry point
    whose call graph reaches it."""
    chassis = extract_chassis(str(repo_root), PYTHON)
    declared: list[_Declared] = []
    for file, rel_path in source_files(repo_root, PYTHON):
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue

        module_prefix = module_dotted_path(rel_path)

        def visit(node: ast.AST, prefix: str, module: str = module_prefix) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualname = f"{prefix}{child.name}"
                    if found := _declared_on(child, qualname):
                        found.qualname = f"{module}.{qualname}"
                        declared.append(found)
                    visit(child, f"{qualname}.<locals>.")
                elif isinstance(child, ast.ClassDef):
                    visit(child, f"{prefix}{child.name}.")
                else:
                    visit(child, prefix)

        visit(tree, "")

    reach = {entry.capability_id: reachable_names(entry.qualname, chassis) for entry in declared if entry.capability_id}
    by_capability: dict[str, list[dict[str, str]]] = {}
    for entry in declared:
        owners = (
            [entry.capability_id]
            if entry.capability_id
            else [cap for cap, names in reach.items() if entry.qualname.rsplit(".", 1)[-1] in names]
        )
        for owner in owners:
            by_capability.setdefault(owner, []).extend(
                {"expectation": text, "span_name": entry.span_name} for text in entry.expectations
            )
    return by_capability


def chassis(
    root: Annotated[Path, typer.Option(help="Repository root to parse")] = Path("."),
    language: Annotated[
        str | None,
        typer.Option(help="python, typescript (JavaScript included); default: the repository's majority language"),
    ] = None,
) -> None:
    """Print the deterministic AST chassis digest the local scan treats as ground truth."""
    if language is not None and normalise_language(language) is None:
        raise typer.BadParameter(f"unknown language {language!r}; use python or typescript", param_hint="--language")
    begin_repository_scan(root)
    typer.echo(chassis_digest(extract_chassis(str(root.resolve()), language)))
