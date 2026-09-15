"""Helpers for local setup → toml conversion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from overmind.chassis import stamp_capability_cards
from overmind.config import Capability, Config, EvalMetric, dump, ensure_project_name, load
from overmind.enrich import enrich_analysis


def _slug_of(raw: dict[str, Any], fallback: str = "") -> str:
    return str(raw.get("slug") or raw.get("slug_hint") or fallback or "").strip()


def _metrics_from_matrix(matrix: list) -> list[EvalMetric]:
    metrics: list[EvalMetric] = []
    for raw in matrix:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        entry_type = str(raw.get("type") or "llm_judge_custom").strip()
        metrics.append(
            EvalMetric(
                name=name,
                type=entry_type,
                prompt=str(raw.get("prompt") or raw.get("rubric") or ""),
                measures=str(raw.get("measures") or ""),
                rationale=str(raw.get("rationale") or ""),
                rubric=str(raw.get("rubric") or ""),
                requires_reference=bool(raw.get("requires_reference")),
                managed_name=str(raw.get("managed_name") or ""),
            )
        )
    return metrics


def analysis_json_to_config(data: dict[str, Any], *, base: Config | None = None) -> Config:
    """Map ``overmind_capabilities.json`` (scan schema) onto an ``overmind.toml`` Config."""
    config = base or Config()
    config.repo_summary = str(data.get("repo_summary") or config.repo_summary or "")
    existing = dict(config.capabilities)
    caps: dict[str, Capability] = {}
    for raw in data.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        slug = _slug_of(raw)
        if not slug:
            continue
        prior = existing.get(slug)
        card = raw.get("capability_card") if isinstance(raw.get("capability_card"), dict) else {}
        matrix = raw.get("eval_matrix") if isinstance(raw.get("eval_matrix"), list) else []
        caps[slug] = Capability(
            slug=slug,
            name=str(raw.get("name") or (prior.name if prior else slug)),
            id=(prior.id if prior and prior.id else str(raw.get("id") or "")),
            description=str(raw.get("description") or card.get("task") or ""),
            entrypoint_fn=str(raw.get("entrypoint_fn") or ""),
            model=str(raw.get("model") or ""),
            source_path=str(raw.get("source_path") or ""),
            system_prompt=str(raw.get("system_prompt") or ""),
            tools_summary=str(raw.get("tools_summary") or ""),
            archived=bool(raw.get("archived") or (prior.archived if prior else False)),
            eval_metrics=_metrics_from_matrix(matrix),
            capability_card=dict(card),
            eval_matrix=list(matrix),
        )
    # Preserve archived leftovers that this scan did not remount.
    for slug, prior in existing.items():
        if slug not in caps and prior.archived:
            caps[slug] = prior
    config.capabilities = caps
    return config


def convert_json_to_toml(
    json_path: str | Path,
    toml_path: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> Config:
    """Read analysis JSON, enrich against the repo AST, stamp verified, write toml."""
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    root = repo_root if repo_root is not None else json_path.resolve().parent
    enrich_analysis(data, str(root))
    stamp_capability_cards(data, root)
    toml_path = Path(toml_path)
    base = None
    if toml_path.exists():
        base = load(toml_path)
    config = analysis_json_to_config(data, base=base)
    ensure_project_name(config, toml_path)
    dump(config, toml_path)
    return config
