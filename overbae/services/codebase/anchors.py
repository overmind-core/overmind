"""Fabricated anchor qualnames are dropped before registry minting so the
binder's exact-equality join never matches a hallucination.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def module_dotted_path(rel_path: str) -> str:
    parts = [p for p in rel_path.split("/") if p]
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def source_qualnames(source: str) -> set[str]:
    """All def/class ``__qualname__``s in ``source`` (including ``<locals>`` nesting)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    out: set[str] = set()

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}{child.name}"
                out.add(qualname)
                walk(child, f"{qualname}.<locals>.")
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}{child.name}"
                out.add(qualname)
                walk(child, f"{qualname}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return out


def _acceptable_names(rel_path: str, clone_dir: str) -> set[str]:
    """Every full anchor name the file can vouch for, at any module-prefix depth
    (import roots like ``src/`` shift the runtime module path)."""
    file = Path(clone_dir, rel_path)
    if not file.is_file() or file.suffix != ".py":
        return set()
    try:
        qualnames = source_qualnames(file.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return set()
    module_parts = module_dotted_path(rel_path).split(".")
    out: set[str] = set()
    for qualname in qualnames:
        for i in range(len(module_parts) + 1):
            out.add(".".join([*module_parts[i:], qualname]))
    return out


def verify_analysis_anchors(analysis: dict[str, Any], clone_dir: str) -> None:
    """In place; never fails the run."""
    cache: dict[str, set[str]] = {}
    kept_total = 0
    dropped_total = 0

    def exists(anchor: dict[str, Any]) -> bool:
        rel = str(anchor.get("file") or "").split("#", 1)[0].strip()
        # LLM-authored paths are a trust boundary — never resolve outside the clone.
        if not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
            return False
        if rel not in cache:
            cache[rel] = _acceptable_names(rel, clone_dir)
        return str(anchor.get("qualname") or "") in cache[rel]

    for item in analysis.get("agents") or []:
        if not isinstance(item, dict):
            continue
        card = item.get("capability_card")
        if not isinstance(card, dict):
            continue
        anchors = [a for a in card.get("anchors") or [] if isinstance(a, dict)]
        kept = [a for a in anchors if exists(a)]
        kept_total += len(kept)
        dropped_total += len(anchors) - len(kept)
        card["anchors"] = kept
        known = {a["qualname"] for a in kept}
        for path in card.get("trajectory_map") or []:
            if isinstance(path, dict):
                path["anchors"] = [q for q in path.get("anchors") or [] if q in known]

    if dropped_total:
        logger.warning(
            "[onboarding] dropped %d unverifiable anchor(s) (%d kept) against %s",
            dropped_total,
            kept_total,
            clone_dir,
        )
