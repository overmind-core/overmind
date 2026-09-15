"""The directory the Cursor agent works in: the prompt as AGENTS.md, the
capability card, the library list, one file per cell, and the frames."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from overbae.models import Dataset
from overbae.services.datasets import paths, store
from overbae.services.datasets.notebook import libraries, prompts

_SAMPLE_ROWS = 20
_SLUG = re.compile(r"[^a-z0-9]+")


def _slug(title: str) -> str:
    return _SLUG.sub("_", title.lower()).strip("_") or "step"


def capability_context(dataset: Dataset) -> tuple[str, dict[str, Any]]:
    """The Markdown block for the prompt and the JSON the workspace carries."""
    capability = dataset.capability
    if capability is None:
        return "## Capability\n\nNone bound yet.\n", {}
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    card = meta.get("capability_card") if isinstance(meta.get("capability_card"), dict) else {}
    system_prompt = str(card.get("system_prompt") or meta.get("system_prompt") or "")
    tools = card.get("tool_spec") or []
    schema = card.get("input_schema") or getattr(capability, "input_schema", None) or {}
    payload = {
        "id": str(capability.id),
        "name": capability.name,
        "description": capability.description or "",
        "system_prompt": system_prompt,
        "tool_spec": tools,
        "input_schema": schema,
        "eval_metrics": meta.get("eval_metrics") or [],
        "trajectory_map": card.get("trajectory_map") or {},
    }
    lines = [f"## Capability: {capability.name}", ""]
    if capability.description:
        lines += [capability.description.strip(), ""]
    lines.append(
        f"System prompt: {'declared, see capability.json' if system_prompt else 'none declared'}."
    )
    names = [str(t.get("name") if isinstance(t, dict) else t) for t in tools]
    lines.append(f"Tools: {', '.join(names) if names else 'none declared'}.")
    keys = list(schema.get("properties", schema)) if isinstance(schema, dict) else []
    lines.append(f"Input keys: {', '.join(str(k) for k in keys) if keys else 'none declared'}.")
    lines.append("")
    return "\n".join(lines), payload


def prepare(dataset: Dataset) -> Path:
    root = paths.workspace_dir(dataset.id)
    root.mkdir(parents=True, exist_ok=True)
    context, card = capability_context(dataset)
    (root / "AGENTS.md").write_text(prompts.system(dataset.intent, context), encoding="utf-8")
    (root / "capability.json").write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    (root / "libraries.md").write_text(
        libraries.describe(paths.library_cache(dataset.project_id)), encoding="utf-8"
    )
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
            link = frames_dir / f"{version}.parquet"
            link.symlink_to(frame)
        lines.append(
            f"- {version} `{cell.title}` {cell.state} · {cell.rows:,} rows × {len(cell.columns or [])} cols"
            + (f" · {cell.note}" if cell.note else "")
        )
    (root / "cells" / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    source = dataset.source
    if source is not None and source.ran:
        sample = store.head(paths.cell_path(dataset.id, source.id), _SAMPLE_ROWS)
        (root / "sample.jsonl").write_text(
            "".join(
                json.dumps(
                    {k: v for k, v in r.items() if k != store.SOURCE_ROW},
                    ensure_ascii=False,
                    default=str,
                )
                + "\n"
                for r in sample
            ),
            encoding="utf-8",
        )
    return root
