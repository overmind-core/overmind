"""Apply an AgentManifest (decorator AST scan) to a project.

Replaces the toml capability-card push. Never deletes: missing capability
slugs become leftover (``observed`` rows are exempt). Incoming ``id`` wins;
otherwise slug. Deleted rows are invisible to identity.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from overbae.models import Capability, Project
from overbae.services.behaviour.anchoring import reanchor
from overbae.services.capabilities import identity
from overbae.services.capability_card import decision_logic_from_card, tools_summary_from_card
from overbae.services.codebase.artifacts import normalize_capability_card
from overbae.services.codebase.derive_card import author_card, derive_card
from overbae.tasks.eval import enqueue_capability_eval_preload_on_commit

logger = logging.getLogger(__name__)

_META_SYSTEM_PROMPT = "system_prompt"
_META_CAPABILITY_CARD = "capability_card"
_META_MANIFEST = "manifest_symbols"
_SETTINGS_AGENT_GRAPH = "agent_graph"
_SETTINGS_TOML_VERSION = "toml_version"  # kept key name for Console compat
_SETTINGS_REPO_SUMMARY = "repo_summary"
_SETTINGS_TRACE_PROVIDER = "trace_provider"


def _as_uuid(value) -> uuid.UUID | None:
    if not value:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def _visible(project: Project):
    return project.capabilities.exclude(status=Capability.Status.DELETED)


def _resolve(project: Project, slug: str, cap_id: Any = None) -> Capability | None:
    uid = _as_uuid(cap_id)
    if uid is not None:
        cap = _visible(project).filter(pk=uid).first()
        if cap is not None:
            return cap
    if slug:
        found = identity.lookup(project.id, slug, include_leftover=True)
        if found is not None and found.status != Capability.Status.DELETED:
            return found
        return _visible(project).filter(slug=slug).first()
    return None


def _unclaimed_id(value) -> uuid.UUID:
    cap_id = _as_uuid(value)
    if cap_id is None or Capability.objects.filter(pk=cap_id).exists():
        return uuid.uuid4()
    return cap_id


def _wire_status(status: str) -> str:
    if status == Capability.Status.CURRENT:
        return "active"
    return status


def behaviour_contracts(capability_symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One contract per ``task`` under the capability; ``main`` when none."""
    by_qualname = {
        s["qualname"]: s
        for s in capability_symbols
        if s.get("qualname") and s.get("role") != "task"
    }
    entry = next(
        (s for s in capability_symbols if s.get("role") == "capability"),
        capability_symbols[0] if capability_symbols else None,
    )
    entry_qualname = entry["qualname"] if entry else ""

    tasks = [s for s in capability_symbols if s.get("role") == "task" and s.get("task_key")]
    contracts: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _anchors_for(start_qualname: str) -> list[dict[str, Any]]:
        start = by_qualname.get(start_qualname)
        if start is None:
            return []
        ordered = [start_qualname]
        for called in start.get("calls") or []:
            if called in by_qualname and called not in ordered:
                ordered.append(called)
        out = []
        for q in ordered:
            sym = by_qualname[q]
            kind = (
                "entry_point"
                if sym.get("role") == "capability"
                else ("tool" if sym.get("role") == "tool" else "function")
            )
            out.append(
                {
                    "qualname": q,
                    "kind": kind,
                    "file": f"{sym.get('file', '')}#L{sym.get('line_start', 0)}-L{sym.get('line_end', 0)}",
                }
            )
        return out

    def _tool_set(start_qualname: str) -> list[dict[str, Any]]:
        start = by_qualname.get(start_qualname)
        if start is None:
            return []
        names = [start_qualname, *(start.get("calls") or [])]
        out = []
        seen_tools: set[str] = set()
        for q in names:
            sym = by_qualname.get(q)
            if not sym or sym.get("role") != "tool":
                continue
            bare = q.rsplit(".", 1)[-1]
            if bare in seen_tools:
                continue
            seen_tools.add(bare)
            out.append(
                {
                    "name": bare,
                    "declared_name": bare,
                    "purpose": str(sym.get("description") or ""),
                    "side_effect": "",
                }
            )
        return out

    for task in tasks:
        key = slugify(str(task["task_key"])) or str(task["task_key"])
        if key in seen:
            continue
        seen.add(key)
        anchors = _anchors_for(task["qualname"])
        contracts.append(
            {
                "key": key,
                "name": str(task.get("name") or key),
                "claim": "code_path",
                "routing": "",
                "entry_anchor": task["qualname"],
                "anchor_sequence": [a["qualname"] for a in anchors],
                "anchors": anchors,
                "sequence": [],
                "steps": [],
                "tools": [t["name"] for t in _tool_set(task["qualname"])],
                "tool_set": _tool_set(task["qualname"]),
                "terminal": {"kind": "emits_record", "description": ""},
                "divergences": [],
                "provenance": [f"{task.get('file', '')}#L{task.get('line_start', 0)}"],
            }
        )

    if not contracts and entry_qualname:
        anchors = _anchors_for(entry_qualname)
        contracts.append(
            {
                "key": "main",
                "name": "Main path",
                "claim": "code_path",
                "routing": "",
                "entry_anchor": entry_qualname,
                "anchor_sequence": [a["qualname"] for a in anchors],
                "anchors": anchors,
                "sequence": [],
                "steps": [],
                "tools": [t["name"] for t in _tool_set(entry_qualname)],
                "tool_set": _tool_set(entry_qualname),
                "terminal": {"kind": "emits_record", "description": ""},
                "divergences": [],
                "provenance": [],
            }
        )
    return contracts


def invokes_edges(
    symbols: list[dict[str, Any]], slug_to_id: dict[str, str]
) -> list[dict[str, Any]]:
    """Capability A reaches capability B's entry → an ``invokes`` edge."""
    entries = {
        s["qualname"]: s.get("slug") or s.get("capability")
        for s in symbols
        if s.get("role") == "capability" and s.get("qualname")
    }
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source_q, source_slug in entries.items():
        if not source_slug or source_slug not in slug_to_id:
            continue
        source_sym = next((s for s in symbols if s.get("qualname") == source_q), None)
        if source_sym is None:
            continue
        for called in source_sym.get("calls") or []:
            target_slug = entries.get(called)
            if not target_slug or target_slug == source_slug or target_slug not in slug_to_id:
                continue
            pair = (slug_to_id[source_slug], slug_to_id[target_slug])
            if pair in seen:
                continue
            seen.add(pair)
            edges.append(
                {
                    "source": pair[0],
                    "target": pair[1],
                    "kind": "invokes",
                }
            )
    return edges


def _group_by_capability(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_qualname = {s["qualname"]: s for s in symbols if s.get("qualname")}
    # Capability symbols declare their own slug; others inherit via capability=.
    for sym in symbols:
        if sym.get("role") == "capability":
            slug = str(sym.get("slug") or "").strip()
            if slug:
                groups[slug].append(sym)
    for sym in symbols:
        if sym.get("role") == "capability":
            continue
        slug = str(sym.get("capability") or "").strip()
        if slug:
            groups[slug].append(sym)

    # Call-graph inheritance: undeclared-capability symbols reachable from an
    # entry join that capability's group (tools/llm under the same module).
    claimed = {id(s) for members in groups.values() for s in members}
    for slug, members in list(groups.items()):
        entry = next((s for s in members if s.get("role") == "capability"), None)
        if entry is None:
            continue
        frontier = list(entry.get("calls") or [])
        seen = set(frontier)
        while frontier:
            q = frontier.pop()
            sym = by_qualname.get(q)
            if sym is None or id(sym) in claimed:
                continue
            if str(sym.get("capability") or "").strip() not in ("", slug):
                continue
            groups[slug].append(sym)
            claimed.add(id(sym))
            for nxt in sym.get("calls") or []:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
    return dict(groups)


def _project_settings_update(project: Project, manifest: dict, edges: list[dict]) -> None:
    settings = dict(project.settings or {})
    if manifest.get("version") or manifest.get("sdk_version"):
        settings[_SETTINGS_TOML_VERSION] = str(
            manifest.get("version") or manifest.get("sdk_version") or ""
        )
    provenance = manifest.get("repository_snapshot")
    if provenance:
        provenance = dict(provenance)
        scanned_at = provenance.get("scanned_at")
        if scanned_at is not None and not isinstance(scanned_at, str):
            provenance["scanned_at"] = scanned_at.isoformat()
    settings["repository_snapshot"] = provenance or None
    settings["last_synced_at"] = timezone.now().isoformat()
    settings[_SETTINGS_AGENT_GRAPH] = {"edges": edges}
    if "repo_summary" in manifest:
        settings[_SETTINGS_REPO_SUMMARY] = manifest.get("repo_summary") or ""
    if manifest.get("trace_provider"):
        settings[_SETTINGS_TRACE_PROVIDER] = manifest["trace_provider"]
    project.settings = settings
    project.save(update_fields=["settings", "updated_at"])


@transaction.atomic
def apply_manifest(project: Project, manifest: dict) -> list[Capability]:
    """Mint/update capabilities from decorator symbols, derive cards, mint behaviours."""
    symbols = [s for s in (manifest.get("symbols") or []) if isinstance(s, dict)]
    groups = _group_by_capability(symbols)
    version = str(manifest.get("version") or manifest.get("sdk_version") or "")
    analyzed_sha = ""
    snap = manifest.get("repository_snapshot") or {}
    if isinstance(snap, dict):
        analyzed_sha = str(snap.get("commit") or snap.get("fingerprint") or "")[:64]

    seen: set[uuid.UUID] = set()
    applied: list[Capability] = []
    slug_to_id: dict[str, str] = {}

    for slug, group in groups.items():
        entry = next((s for s in group if s.get("role") == "capability"), group[0])
        existing = _resolve(project, slug)
        created = existing is None
        remounted = existing is not None and existing.status == Capability.Status.LEFTOVER
        cap = existing or Capability(
            project=project,
            id=_unclaimed_id(None),
            slug=slug,
            name=str(entry.get("name") or slug),
        )
        cap.slug = slug
        cap.name = str(entry.get("name") or cap.name or slug)
        cap.description = str(entry.get("description") or cap.description or "")[:4096]
        cap.entrypoint_fn = str(entry.get("qualname") or cap.entrypoint_fn or "")
        cap.source_path = str(entry.get("file") or cap.source_path or "")
        cap.status = Capability.Status.CURRENT

        derived = derive_card(group)
        authored = author_card(derived, evidence={"symbols": group})
        card = normalize_capability_card(authored)
        card["generator"] = (
            authored.get("_generator") or authored.get("generator") or "derive_card@v1"
        )
        cap.input_schema = card.get("input_schema") or {}
        cap.output_fields = card.get("output_fields") or {}
        cap.tools_summary = tools_summary_from_card(card, cap.tools_summary or "")[:4096]
        cap.decision_logic = decision_logic_from_card(card, cap.decision_logic or "")[:4096]

        meta = dict(cap.improvement_metadata or {})
        if card.get("system_prompt") or entry.get("prompt_template"):
            meta[_META_SYSTEM_PROMPT] = str(
                card.get("system_prompt") or entry.get("prompt_template") or ""
            )
        meta[_META_CAPABILITY_CARD] = card
        meta[_META_MANIFEST] = group
        cap.improvement_metadata = meta
        cap.save()

        contracts = behaviour_contracts(group)
        reanchor(cap, contracts, analyzed_sha or version or "manifest")
        enqueue_capability_eval_preload_on_commit(cap)
        if created or remounted:
            identity.enqueue_rebind(cap.project_id)

        seen.add(cap.id)
        applied.append(cap)
        slug_to_id[slug] = str(cap.id)

    edges = invokes_edges(symbols, slug_to_id)
    _project_settings_update(project, manifest, edges)

    leftovers = (
        _visible(project)
        .filter(status=Capability.Status.CURRENT, observed=False)
        .exclude(pk__in=seen)
    )
    for cap in leftovers:
        cap.set_status(Capability.Status.LEFTOVER)

    return applied


def _cap_row(cap: Capability) -> dict[str, Any]:
    meta = cap.improvement_metadata or {}
    return {
        "id": str(cap.id),
        "slug": cap.slug,
        "name": cap.name,
        "description": cap.description,
        "entrypoint_fn": cap.entrypoint_fn,
        "model": cap.model,
        "source_path": cap.source_path,
        "system_prompt": meta.get(_META_SYSTEM_PROMPT) or "",
        "tools_summary": cap.tools_summary,
        "capability_card": meta.get(_META_CAPABILITY_CARD) or {},
        "archived": cap.status == Capability.Status.LEFTOVER,
        "status": _wire_status(cap.status),
    }


def snapshot_of(project: Project, *, capabilities: list[Capability] | None = None) -> dict:
    settings = project.settings or {}
    caps = list(_visible(project).order_by("slug")) if capabilities is None else capabilities
    graph = settings.get(_SETTINGS_AGENT_GRAPH) or {}
    return {
        "project_id": str(project.id),
        "repo_summary": settings.get(_SETTINGS_REPO_SUMMARY) or "",
        "trace_provider": settings.get(_SETTINGS_TRACE_PROVIDER) or "overmind",
        "version": settings.get(_SETTINGS_TOML_VERSION) or "",
        "sdk_version": settings.get(_SETTINGS_TOML_VERSION) or "",
        "repository_snapshot": settings.get("repository_snapshot"),
        "last_synced_at": settings.get("last_synced_at"),
        "capabilities": [_cap_row(cap) for cap in caps],
        "edges": list(graph.get("edges") or []),
        "symbols": [],
    }


# Back-compat alias used by older imports.
apply_snapshot = apply_manifest
