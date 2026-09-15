"""Holds only what the AST proves (which functions exist, who calls whom);
semantic identification belongs to the codebase scan.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    "site-packages",
    "dist",
    "build",
}


@dataclass
class Chassis:
    functions: dict[str, str] = field(default_factory=dict)
    # caller qualname -> called names (bare trailing segment, over-approximate)
    calls: dict[str, set[str]] = field(default_factory=dict)


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


def extract_chassis(clone_dir: str) -> Chassis:
    from overbae.services.codebase.anchors import module_dotted_path

    chassis = Chassis()
    root = Path(clone_dir)
    for file in root.rglob("*.py"):
        rel_parts = file.relative_to(root).parts
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        rel_path = "/".join(rel_parts)
        try:
            tree = ast.parse(file.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        _walk_functions(tree, rel_path, module_dotted_path(rel_path), chassis)
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
