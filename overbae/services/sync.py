"""Apply a local ``overmind.toml`` snapshot to a project.

Never deletes. Missing incoming slugs become leftover (``observed`` rows are
exempt). A leftover slug that reappears without ``archived`` remounts to
current. Incoming ``archived=true`` keeps leftover. Incoming ``id`` wins;
otherwise slug. Deleted rows are invisible to identity.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction

from overbae.models import Capability, Project
from overbae.services.behaviour.registry import mint_behaviour_registry
from overbae.services.capabilities import identity
from overbae.services.capability_card import decision_logic_from_card, tools_summary_from_card
from overbae.services.codebase.artifacts import normalize_capability_card
from overbae.tasks.eval import enqueue_capability_eval_preload_on_commit

_CAP_COLUMNS = (
    "slug",
    "name",
    "description",
    "entrypoint_fn",
    "model",
    "source_path",
    "tools_summary",
)

_SETTINGS_REPO_SUMMARY = "repo_summary"
_SETTINGS_TRACE_PROVIDER = "trace_provider"
_SETTINGS_TOML_VERSION = "toml_version"
_META_SYSTEM_PROMPT = "system_prompt"
_META_EVAL_METRICS = "eval_metrics"
_META_CAPABILITY_CARD = "capability_card"
_META_EVAL_MATRIX = "eval_matrix"


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


def _resolve(project: Project, data: dict) -> Capability | None:
    cap_id = _as_uuid(data.get("id"))
    if cap_id is not None:
        cap = _visible(project).filter(pk=cap_id).first()
        if cap is not None:
            return cap
    slug = data.get("slug")
    if slug:
        return _visible(project).filter(slug=slug).first()
    return None


def _wire_status(status: str) -> str:
    """Toml / SDK wire uses ``active``; the DB uses ``current``."""
    if status == Capability.Status.CURRENT:
        return "active"
    return status


def _fill(cap: Capability, data: dict) -> None:
    for field in _CAP_COLUMNS:
        if field in data and data[field] is not None:
            setattr(cap, field, data[field] or "")

    card_raw = data.get("capability_card")
    card: dict = {}
    if isinstance(card_raw, dict):
        card = normalize_capability_card(card_raw)
        if not data.get("description"):
            cap.description = (card.get("task") or cap.description or "")[:4096]
        cap.input_schema = card.get("input_schema") or cap.input_schema or {}
        cap.output_fields = card.get("output_fields") or cap.output_fields or {}
        cap.tools_summary = tools_summary_from_card(
            card, data.get("tools_summary") or cap.tools_summary or ""
        )[:4096]
        cap.decision_logic = decision_logic_from_card(
            card, data.get("decision_logic") or cap.decision_logic or ""
        )[:4096]

    meta = dict(cap.improvement_metadata or {})
    if "system_prompt" in data and data["system_prompt"] is not None:
        meta[_META_SYSTEM_PROMPT] = data["system_prompt"] or ""
    if "eval_metrics" in data:
        meta[_META_EVAL_METRICS] = list(data["eval_metrics"] or [])
    if isinstance(card_raw, dict):
        meta[_META_CAPABILITY_CARD] = card
    if "eval_matrix" in data and isinstance(data.get("eval_matrix"), list):
        meta[_META_EVAL_MATRIX] = list(data["eval_matrix"] or [])
        # Keep the flat wire metrics in sync when only the matrix is sent.
        if "eval_metrics" not in data:
            meta[_META_EVAL_METRICS] = list(data["eval_matrix"] or [])
    cap.improvement_metadata = meta


def _project_settings_update(project: Project, snapshot: dict) -> None:
    settings = dict(project.settings or {})
    if "repo_summary" in snapshot:
        settings[_SETTINGS_REPO_SUMMARY] = snapshot.get("repo_summary") or ""
    if snapshot.get("trace_provider"):
        settings[_SETTINGS_TRACE_PROVIDER] = snapshot["trace_provider"]
    if snapshot.get("version"):
        settings[_SETTINGS_TOML_VERSION] = snapshot["version"]
    project.settings = settings
    project.save(update_fields=["settings", "updated_at"])


def _after_write(
    cap: Capability, data: dict, *, created: bool, remounted: bool, version: str
) -> None:
    if cap.status != Capability.Status.CURRENT:
        return
    mint_behaviour_registry(cap, version or "")
    enqueue_capability_eval_preload_on_commit(cap)
    if created or remounted:
        identity.enqueue_rebind(cap.project_id)


@transaction.atomic
def apply_snapshot(project: Project, snapshot: dict) -> list[Capability]:
    _project_settings_update(project, snapshot)
    version = str(snapshot.get("version") or "")

    incoming = list(snapshot.get("capabilities") or [])
    seen: set[uuid.UUID] = set()
    applied: list[Capability] = []
    for data in incoming:
        existing = _resolve(project, data)
        created = existing is None
        remounted = (
            existing is not None
            and existing.status == Capability.Status.LEFTOVER
            and not data.get("archived")
        )
        cap = existing or Capability(
            project=project,
            id=_as_uuid(data.get("id")) or uuid.uuid4(),
            slug=data.get("slug") or "",
            name=data.get("name") or data.get("slug") or "",
        )
        _fill(cap, data)
        cap.status = (
            Capability.Status.LEFTOVER if data.get("archived") else Capability.Status.CURRENT
        )
        cap.save()
        _after_write(cap, data, created=created, remounted=remounted, version=version)
        seen.add(cap.id)
        applied.append(cap)

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
        "eval_metrics": list(meta.get(_META_EVAL_METRICS) or []),
        "capability_card": meta.get(_META_CAPABILITY_CARD) or {},
        "eval_matrix": list(meta.get(_META_EVAL_MATRIX) or []),
        "archived": cap.status == Capability.Status.LEFTOVER,
        "status": _wire_status(cap.status),
    }


def snapshot_of(project: Project, *, capabilities: list[Capability] | None = None) -> dict:
    settings = project.settings or {}
    caps = list(_visible(project).order_by("slug")) if capabilities is None else capabilities
    return {
        "project_id": str(project.id),
        "repo_summary": settings.get(_SETTINGS_REPO_SUMMARY) or "",
        "trace_provider": settings.get(_SETTINGS_TRACE_PROVIDER) or "overmind",
        "version": settings.get(_SETTINGS_TOML_VERSION) or "",
        "capabilities": [_cap_row(cap) for cap in caps],
    }
