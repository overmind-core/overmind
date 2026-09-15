"""Post-scan AST checks: drop fabricated anchors, bad provenance, fill prompt spans."""

from __future__ import annotations

import ast
import logging
import re
import textwrap
from pathlib import Path
from typing import Any

from overmind.chassis import module_dotted_path

logger = logging.getLogger(__name__)

_PROVENANCE_SPAN_RE = re.compile(r"^(?P<path>.+?)#L(?P<start>\d+)(?:-L?(?P<end>\d+))?$")
_DEFINITION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_CODE_LINE_RE = re.compile(
    r"^\s*(?:@|def |class |return |import |from |if |elif |else:|for |while |try:|except|with )",
    re.MULTILINE,
)
_MIN_NESTED_PROMPT_CHARS = 40


def source_qualnames(source: str) -> set[str]:
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


def _acceptable_names(rel_path: str, repo_root: str) -> set[str]:
    file = Path(repo_root, rel_path)
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


def verify_analysis_anchors(analysis: dict[str, Any], repo_root: str) -> None:
    """Drop fabricated qualnames. Iterates ``capabilities`` (the scan schema)."""
    cache: dict[str, set[str]] = {}
    kept_total = 0
    dropped_total = 0

    def exists(anchor: dict[str, Any]) -> bool:
        rel = str(anchor.get("file") or "").split("#", 1)[0].strip()
        if not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
            return False
        if rel not in cache:
            cache[rel] = _acceptable_names(rel, repo_root)
        return str(anchor.get("qualname") or "") in cache[rel]

    for item in analysis.get("capabilities") or []:
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
            "dropped %d unverifiable anchor(s) (%d kept) against %s",
            dropped_total,
            kept_total,
            repo_root,
        )


def _make_span_verifier(repo_root: str):
    line_counts: dict[str, int | None] = {}

    def _line_count(rel_path: str) -> int | None:
        if rel_path not in line_counts:
            if rel_path.startswith(("/", "~")) or ".." in rel_path.split("/"):
                line_counts[rel_path] = None
                return None
            file = Path(repo_root, rel_path)
            try:
                line_counts[rel_path] = file.read_bytes().count(b"\n") + 1 if file.is_file() else None
            except OSError:
                line_counts[rel_path] = None
        return line_counts[rel_path]

    def _is_valid(raw: Any) -> bool:
        span = str(raw or "").strip()
        if not span:
            return False
        match = _PROVENANCE_SPAN_RE.match(span)
        count = _line_count(match.group("path") if match else span)
        if count is None:
            return False
        if match:
            return int(match.group("end") or match.group("start")) <= count
        return True

    return _is_valid


def _card_span_lists(card: Any) -> list[tuple[dict[str, Any], str]]:
    if not isinstance(card, dict):
        return []
    out: list[tuple[dict[str, Any], str]] = []
    prov = card.get("provenance")
    if isinstance(prov, dict):
        out.append((prov, "paths"))
    output_schema = card.get("output_schema")
    if isinstance(output_schema, dict):
        out.append((output_schema, "provenance"))
    for entries in (
        card.get("tool_spec"),
        card.get("constraints"),
        card.get("tool_protocol"),
        card.get("trajectory_map"),
    ):
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                out.append((entry, "provenance"))
    return out


def filter_invalid_provenance(analysis: dict[str, Any], repo_root: str) -> None:
    is_valid = _make_span_verifier(repo_root)
    kept = 0
    dropped = 0

    def _clean(spans: Any) -> list[Any]:
        nonlocal kept, dropped
        good: list[Any] = []
        for raw in spans if isinstance(spans, list) else []:
            if is_valid(raw):
                good.append(raw)
                kept += 1
            else:
                dropped += 1
        return good

    for item in analysis.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        for container, key in _card_span_lists(item.get("capability_card")):
            container[key] = _clean(container.get(key))
    for util in analysis.get("llm_utilities") or []:
        prov = util.get("provenance") if isinstance(util, dict) else None
        if isinstance(prov, dict):
            prov["paths"] = _clean(prov.get("paths"))
    if dropped:
        logger.warning(
            "dropped %d unverifiable provenance span(s) (%d kept) against %s",
            dropped,
            kept,
            repo_root,
        )


def _read_span_text(repo_root: str, span: Any) -> str:
    raw = str(span or "").strip()
    match = _PROVENANCE_SPAN_RE.match(raw)
    if not match:
        return ""
    rel = match.group("path").strip()
    if not rel or rel.startswith(("/", "~")) or ".." in rel.split("/"):
        return ""
    file = Path(repo_root, rel)
    try:
        if not file.is_file():
            return ""
        lines = file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    start = int(match.group("start"))
    end = int(match.group("end") or match.group("start"))
    if start < 1 or end < start or end > len(lines):
        return ""
    return "\n".join(lines[start - 1 : end]).strip()


def _literal_prompt_text(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                try:
                    parts.append("{" + ast.unparse(value.value) + "}")
                except Exception:
                    parts.append("{...}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal_prompt_text(node.left), _literal_prompt_text(node.right)
        return None if left is None or right is None else left + right
    return None


def _prompt_text_from_source(source: str) -> str:
    text = textwrap.dedent(source).strip()
    if not text:
        return ""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return "" if _CODE_LINE_RE.search(text) else text
    if any(isinstance(n, _DEFINITION_NODES) for n in tree.body):
        return ""
    for node in tree.body:
        value = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
        literal = _literal_prompt_text(value) if value is not None else None
        if literal and literal.strip():
            return literal.strip()
    nested = [
        found.strip()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Constant, ast.JoinedStr))
        and (found := _literal_prompt_text(node)) is not None
        and len(found.strip()) >= _MIN_NESTED_PROMPT_CHARS
    ]
    return max(nested, key=len) if nested else ""


def resolve_prompt_spans(analysis: dict[str, Any], repo_root: str) -> None:
    """Fill empty system_prompt / mode prompt from a cited source span. Model text wins."""

    def resolve(span: Any) -> str:
        if not str(span or "").strip():
            return ""
        raw = _read_span_text(repo_root, span)
        if not raw:
            return ""
        return _prompt_text_from_source(raw)

    for item in analysis.get("capabilities") or []:
        if not isinstance(item, dict):
            continue
        if not str(item.get("system_prompt") or "").strip():
            text = resolve(item.get("system_prompt_span"))
            if text:
                item["system_prompt"] = text
        for mode in item.get("modes") or []:
            if not isinstance(mode, dict) or str(mode.get("prompt") or "").strip():
                continue
            text = resolve(mode.get("prompt_span"))
            if text:
                mode["prompt"] = text


def enrich_analysis(analysis: dict[str, Any], repo_root: str) -> None:
    """Resolve prompt spans, filter provenance, verify anchors (server sync skips card-schema fallback)."""
    resolve_prompt_spans(analysis, repo_root)
    filter_invalid_provenance(analysis, repo_root)
    verify_analysis_anchors(analysis, repo_root)
