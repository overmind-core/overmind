"""Source-language detection and file discovery shared by the repository scanners."""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

PYTHON = "python"
# TypeScript and JavaScript share one scanner: a repo mixes them freely.
TYPESCRIPT = "typescript"

_SUFFIXES: dict[str, tuple[str, ...]] = {
    PYTHON: (".py",),
    TYPESCRIPT: (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"),
}
_ALIASES = {
    "python": PYTHON,
    "py": PYTHON,
    "typescript": TYPESCRIPT,
    "ts": TYPESCRIPT,
    "tsx": TYPESCRIPT,
    "javascript": TYPESCRIPT,
    "js": TYPESCRIPT,
    "jsx": TYPESCRIPT,
    "node": TYPESCRIPT,
    "nodejs": TYPESCRIPT,
}

SKIP_DIRS = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".tox",
    "site-packages",
    "dist",
    "build",
    ".next",
    ".turbo",
    ".overmind",
})


def suffixes(language: str) -> tuple[str, ...]:
    return _SUFFIXES[language]


def normalise_language(raw: Any) -> str | None:
    return _ALIASES.get(str(raw or "").strip().lower())


def language_of_path(path: str | Path) -> str | None:
    name = str(path).split("#", 1)[0]
    if name.endswith(".d.ts") or name.endswith(".d.mts") or name.endswith(".d.cts"):
        return None
    suffix = os.path.splitext(name)[1].lower()
    return next((language for language, known in _SUFFIXES.items() if suffix in known), None)


def source_files(root: str | Path, language: str) -> Iterator[tuple[Path, str]]:
    """``(file, posix path relative to root)`` for every source file of ``language``, in a stable order.
    Ignored directories are pruned, never walked: ``node_modules`` is the largest tree in a JS checkout."""
    base = Path(root)
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(current, name)
            if language_of_path(name) == language:
                yield path, path.relative_to(base).as_posix()


def _declared_paths(data: Mapping[str, Any]) -> Iterator[str]:
    for raw in data.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        yield str(raw.get("source_path") or "")
        for mode in raw.get("modes") or []:
            if isinstance(mode, dict):
                yield str(mode.get("source_path") or "")
        card = raw.get("capability_card")
        for anchor in (card.get("anchors") if isinstance(card, dict) else None) or []:
            if isinstance(anchor, dict):
                yield str(anchor.get("file") or "")


def detect_language(repo_root: str | Path, data: Mapping[str, Any] | None = None) -> str:
    """The scan's language: the JSON's ``language``, else the language of the source files it cites,
    else the repository's majority language. Python wins ties and empty repos."""
    if data:
        if explicit := normalise_language(data.get("language")):
            return explicit
        cited = Counter(lang for path in _declared_paths(data) if (lang := language_of_path(path)))
        if cited:
            return min(cited, key=lambda lang: (-cited[lang], lang))
    counts = Counter({language: sum(1 for _ in source_files(repo_root, language)) for language in _SUFFIXES})
    return min(counts, key=lambda lang: (-counts[lang], lang))
