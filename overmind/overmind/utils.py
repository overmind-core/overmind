"""Helpers for local setup → toml conversion."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from overmind.chassis import stamp_capability_cards
from overmind.config import Capability, Config, EvalMetric, dump, ensure_project_name, load
from overmind.enrich import enrich_analysis, read_prompt_span
from overmind.repository_snapshot import finish_repository_scan


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


def _utilities_by_parent(data: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Scan ``llm_utilities`` sit at the top level; ``called_by`` names the parent by slug,
    else by a name no other capability shares. Unresolvable utilities attach nowhere."""
    caps = [raw for raw in data.get("capabilities") or [] if isinstance(raw, dict) and _slug_of(raw)]
    parent: dict[str, str] = {}
    for raw in caps:
        for key in (raw.get("slug"), raw.get("slug_hint")):
            if str(key or "").strip():
                parent.setdefault(str(key).strip(), _slug_of(raw))
    names: dict[str, list[str]] = {}
    for raw in caps:
        name = str(raw.get("name") or "").strip()
        if name:
            names.setdefault(name, []).append(_slug_of(raw))
    for name, slugs in names.items():
        if len(slugs) == 1 and name not in parent:
            parent[name] = slugs[0]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for util in data.get("llm_utilities") or []:
        slug = parent.get(str(util.get("called_by") or "").strip()) if isinstance(util, dict) else None
        if slug:
            grouped.setdefault(slug, []).append(util)
    return grouped


def analysis_json_to_config(data: dict[str, Any], *, base: Config | None = None) -> Config:
    """Map ``overmind_capabilities.json`` (scan schema) onto an ``overmind.toml`` Config."""
    config = base or Config()
    config.repo_summary = str(data.get("repo_summary") or config.repo_summary or "")
    existing = dict(config.capabilities)
    utilities = _utilities_by_parent(data)
    caps: dict[str, Capability] = {}
    for raw in data.get("capabilities") or []:
        if not isinstance(raw, dict):
            continue
        slug = _slug_of(raw)
        if not slug:
            continue
        prior = existing.get(slug)
        card = dict(raw.get("capability_card") if isinstance(raw.get("capability_card"), dict) else {})
        if isinstance(raw.get("modes"), list) and not card.get("modes"):
            card["modes"] = raw["modes"]
        if utilities.get(slug) and not card.get("llm_utilities"):
            card["llm_utilities"] = utilities[slug]
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
            decision_logic=str(raw.get("decision_logic") or ""),
            policy_markdown=str(raw.get("policy_markdown") or ""),
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
    validate_scan_detail(data, repo_root=str(root))
    stamp_capability_cards(data, root)
    toml_path = Path(toml_path)
    base = None
    if toml_path.exists():
        base = load(toml_path)
    config = analysis_json_to_config(data, base=base)
    config.repository_snapshot = finish_repository_scan(Path(root))
    ensure_project_name(config, toml_path)
    dump(config, toml_path)
    return config


def validate_scan_detail(data: dict[str, Any], *, repo_root: str) -> None:
    """Reject incomplete decision maps before replacing the saved snapshot.

    This checks supplied evidence and graph references, not semantic completeness
    against every possible execution of the source.
    """
    issues: list[str] = []
    utilities = _utilities_by_parent(data)
    for raw in data.get("capabilities") or []:
        card = raw.get("capability_card") or {}
        paths = card.get("trajectory_map") or []
        if not any(path.get("claim") == "decision_surface" for path in paths):
            continue
        label = _slug_of(raw) or str(raw.get("name") or "capability")
        known = {anchor.get("qualname"): anchor for anchor in card.get("anchors") or []}
        for key, default_key in (("model_resolution", "default_model"), ("prompt_resolution", "default_span")):
            resolution = card.get(key) or {}
            for field in ("selection", "provenance"):
                if not resolution.get(field):
                    issues.append(f"{label}/{key}: supply source-backed {field}")
            if not resolution.get(default_key) and not resolution.get("unresolved"):
                issues.append(f"{label}/{key}: supply {default_key} or explain what cannot be resolved statically")
        prompt = card.get("prompt_resolution") or {}
        if prompt.get("default_span"):
            text = read_prompt_span(repo_root, prompt["default_span"])
            if not text:
                issues.append(f"{label}/prompt_resolution: default_span must identify readable prompt text")
            else:
                try:
                    # Only the declared template is rendered; repository code is never executed.
                    if "bindings" in prompt:
                        text = text.format_map(prompt["bindings"])
                    supplied = str(raw.get("system_prompt") or "").strip()
                    if supplied and supplied != text.strip():
                        issues.append(
                            f"{label}/prompt_resolution: system_prompt differs from the declared default template"
                        )
                    else:
                        raw["system_prompt"] = text.strip()
                except (KeyError, ValueError, TypeError, AttributeError, IndexError) as exc:
                    issues.append(f"{label}/prompt_resolution: invalid template bindings ({type(exc).__name__})")
        for utility in utilities.get(_slug_of(raw), []) + (card.get("llm_utilities") or []):
            anchors = utility.get("anchors") or []
            implementation = str(utility.get("entrypoint_fn") or "")
            matches_source = any(
                known.get(anchor, {}).get("file", "").split("#", 1)[0] == utility.get("source_path")
                and implementation
                and (anchor == implementation or anchor.endswith("." + implementation))
                for anchor in anchors
            )
            if (
                any(anchor not in known for anchor in anchors)
                or (anchors and not matches_source)
                or (not anchors and not utility.get("unresolved"))
            ):
                issues.append(
                    f"{label}/utility {utility.get('name')}: anchor the implementation or explain unavailable source"
                )
        destinations = {"terminal"}
        owners: list[tuple[str, dict[str, Any]]] = []
        for path in paths:
            path_label = f"{label}/{path.get('id') or path.get('name') or 'trajectory'}"
            decision_surface = path.get("claim") == "decision_surface"
            steps = path.get("steps") or path.get("sequence") or []
            steps = [step for step in steps if isinstance(step, dict)]
            if decision_surface and not any(step.get("kind") == "model_invocation" for step in steps):
                issues.append(f"{path_label}: describe the model invocation with structured steps")
            for step in steps:
                owner = f"{path_label}/{step.get('step') or 'step'}"
                anchors = step.get("anchors") or []
                if decision_surface and (not anchors or any(anchor not in known for anchor in anchors)):
                    issues.append(f"{owner}: supply source-verified step anchors")
                destinations.update(anchor for anchor in anchors if anchor in known)
                if decision_surface and step.get("kind") == "model_invocation":
                    for field in ("input", "action", "output"):
                        if not str(step.get(field) or "").strip():
                            issues.append(f"{owner}: describe the model's {field}")
                owners.append((owner, step))
        for tool in card.get("tool_spec") or []:
            owner = f"{label}/tool {tool.get('name') or '(unnamed)'}"
            branches = tool.get("branches") or []
            if not branches:
                issues.append(f"{owner}: inspect the implementation and describe its outcomes in branches")
            for index, branch in enumerate(branches, 1):
                branch_owner = f"{owner}/branch {index}"
                for field in ("condition", "action", "outcome", "provenance"):
                    if not branch.get(field):
                        issues.append(f"{branch_owner}: missing {field}")
                owners.append((branch_owner, branch))
        for owner, item in owners:
            for transition in item.get("transitions") or []:
                target = transition.get("target")
                if target not in destinations:
                    issues.append(f"{owner}: transition target {target!r} is not a destination step anchor or terminal")
    if issues:
        raise ValueError(
            "Incomplete capability scan; existing TOML was not changed. "
            "Read the cited source, repair the analysis JSON and rerun conversion; do not repeat init.\n- "
            + "\n- ".join(issues)
        )
