"""What a cell may import. Two tiers: packages in the worker image, and
packages ``install`` may pull as wheels into a per-project cache."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STDLIB = frozenset(
    {
        "re",
        "json",
        "math",
        "statistics",
        "collections",
        "itertools",
        "functools",
        "datetime",
        "unicodedata",
        "string",
        "textwrap",
        "difflib",
        "copy",
        "typing",
        "random",
        "hashlib",
        "uuid",
        "decimal",
        "fractions",
        "operator",
        "dataclasses",
        "enum",
        "heapq",
        "bisect",
        "array",
        "struct",
        "base64",
        "html",
        "urllib",
        "email",
        "csv",
        "io",
        "time",
        "zlib",
        "gzip",
        "bz2",
        "lzma",
        "pprint",
        "warnings",
        "contextlib",
        "abc",
        "numbers",
        "types",
        "inspect",
        "ast",
        "tokenize",
        "keyword",
        "codecs",
        "locale",
        "calendar",
        "zoneinfo",
        "secrets",
        "hmac",
        "binascii",
        "colorsys",
        "graphlib",
        "queue",
        "threading",
        "concurrent",
        "multiprocessing",
        "logging",
        "pathlib",
        "os",
        "sys",
    }
)

# Import name → distribution. Present in the workshop worker image.
PRELOADED: dict[str, str] = {
    "pandas": "pandas",
    "numpy": "numpy",
    "pyarrow": "pyarrow",
    "duckdb": "duckdb",
    "scipy": "scipy",
    "sklearn": "scikit-learn",
    "rapidfuzz": "rapidfuzz",
    "regex": "regex",
    "jsonschema": "jsonschema",
    "tiktoken": "tiktoken",
    "langdetect": "langdetect",
    "datasketch": "datasketch",
    "dateutil": "python-dateutil",
    "pytz": "pytz",
    "yaml": "pyyaml",
}

# Import name → distribution. Pure-Python or wheel-only, pulled on request.
INSTALLABLE: dict[str, str] = {
    "nltk": "nltk",
    "textstat": "textstat",
    "unidecode": "unidecode",
    "ftfy": "ftfy",
    "simhash": "simhash",
    "emoji": "emoji",
    "langcodes": "langcodes",
    "presidio_analyzer": "presidio-analyzer",
    "jellyfish": "jellyfish",
    "Levenshtein": "levenshtein",
    "markdownify": "markdownify",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "wordfreq": "wordfreq",
    "sentence_transformers": "sentence-transformers",
    "transformers": "transformers",
    "torch": "torch",
}

_DIST_TO_IMPORT = {dist: name for name, dist in {**PRELOADED, **INSTALLABLE}.items()}


class LibraryError(ValueError):
    pass


def resolve(package: str) -> tuple[str, str]:
    """``(import_name, distribution)`` for a package the agent may name either way."""
    key = package.strip()
    if key in PRELOADED:
        return key, PRELOADED[key]
    if key in INSTALLABLE:
        return key, INSTALLABLE[key]
    if key in _DIST_TO_IMPORT:
        return _DIST_TO_IMPORT[key], key
    allowed = ", ".join(sorted({*PRELOADED.values(), *INSTALLABLE.values()}))
    raise LibraryError(f"{package} is not on the list. Allowed: {allowed}.")


def installed(cache: Path) -> set[str]:
    """Import names present in the project's wheel cache."""
    if not cache.exists():
        return set()
    names: set[str] = set()
    for entry in cache.iterdir():
        stem = entry.name.split("-", 1)[0]
        if stem in INSTALLABLE:
            names.add(stem)
        for import_name, dist in INSTALLABLE.items():
            if dist.replace("-", "_") == stem:
                names.add(import_name)
    return names


def allowed_imports(cache: Path) -> frozenset[str]:
    return frozenset(STDLIB | set(PRELOADED) | installed(cache))


def install(package: str, cache: Path) -> tuple[str, str]:
    """Pull one installable distribution into ``cache``. Returns ``(name, version)``."""
    import_name, dist = resolve(package)
    if import_name in PRELOADED:
        return dist, version_of(dist)
    if dist not in INSTALLABLE.values():
        raise LibraryError(f"{dist} is preloaded or unknown.")
    cache.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(  # noqa: S603 — fixed argv, allow-listed name
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--only-binary=:all:",
            "--no-deps" if dist in ("torch",) else "--upgrade",
            "--target",
            str(cache),
            dist,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise LibraryError(f"{dist} could not be installed: {proc.stderr.strip()[-400:]}")
    return dist, version_of(dist, cache)


def version_of(dist: str, cache: Path | None = None) -> str:
    from importlib import metadata

    paths = [str(cache)] if cache is not None else None
    try:
        found = (
            metadata.distributions(name=dist, path=paths)
            if paths
            else [metadata.distribution(dist)]
        )
        for item in found:
            return item.version
    except metadata.PackageNotFoundError:
        pass
    return ""


def describe(cache: Path) -> str:
    """The Markdown the agent's workspace carries."""
    extra = sorted(installed(cache))
    lines = [
        "# Libraries",
        "",
        "A cell may import the standard library and these packages:",
        "",
        ", ".join(sorted(PRELOADED.values())),
        "",
    ]
    if extra:
        lines += ["Installed on request: " + ", ".join(INSTALLABLE[n] for n in extra), ""]
    lines += [
        "Ask for one of these with the `install` tool: "
        + ", ".join(sorted(set(INSTALLABLE.values()) - {INSTALLABLE[n] for n in extra})),
        "",
        "Nothing else installs. Cells have no network and no file access.",
    ]
    return "\n".join(lines)
