from __future__ import annotations

import re
import shutil
from pathlib import Path

from overbae.models import Dataset
from overbae.services.datasets import paths

_SLUG = re.compile(r"[^a-z0-9]+")

WORKSPACE_NOTE = """\
## Workspace

`cells/<position>_<title>.py` holds each cell's script with its version and
state in the header. `frames/<version>.parquet` is each version's frame; read
one with pandas when a tool cannot answer, never write to it. Every change to
the chain goes through the tools.
"""


def _slug(title: str) -> str:
    return _SLUG.sub("_", title.lower()).strip("_") or "step"


def prepare(dataset: Dataset, system_prompt: str) -> Path:
    root = paths.workspace_dir(dataset.id)
    root.mkdir(parents=True, exist_ok=True)
    (root / "AGENTS.md").write_text(system_prompt + "\n" + WORKSPACE_NOTE, encoding="utf-8")
    cells_dir = root / "cells"
    frames_dir = root / "frames"
    shutil.rmtree(cells_dir, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    cells_dir.mkdir()
    frames_dir.mkdir()
    versions = dataset.versions()
    lines = ["# Cells", ""]
    for cell in dataset.chain:
        version = versions.get(cell.id, "proposed")
        name = f"{cell.position:03d}_{_slug(cell.title)}.py"
        if cell.position > 0:
            header = (
                f"# {version} · {cell.title} · {cell.state}"
                + (" · frozen" if cell.frozen else "")
                + (f"\n# {cell.note}" if cell.note else "")
                + (f"\n# error: {cell.error}" if cell.error else "")
            )
            (cells_dir / name).write_text(header + "\n" + cell.script + "\n", encoding="utf-8")
        frame = paths.cell_path(dataset.id, cell.id)
        if cell.ran and frame.exists():
            (frames_dir / f"{version}.parquet").symlink_to(frame)
        lines.append(
            f"- {version} `{cell.title}` {cell.state} · {cell.rows:,} rows × {len(cell.columns or [])} cols"
            + (f" · {cell.note}" if cell.note else "")
        )
    (cells_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root
