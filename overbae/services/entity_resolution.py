"""Project-scoped name-first entity resolution.

Callers should refer to entities by slug or display name — UUIDs are
an internal fallback for disambiguation only. Every resolver returns
``(entity, error)`` where *error* names matching entities (never bare ids) when
lookup fails or is ambiguous.
"""

from __future__ import annotations

import uuid

from overbae.models import (
    Behaviour,
    Capability,
    ConnectorCredential,
    Conversation,
    Dataset,
    EvalRun,
)
from overbae.services.capabilities import identity


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def resolve_capability(project, ref: str) -> tuple[Capability | None, str | None]:
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide a capability slug or display name (from list_capabilities)"
    if ref.startswith("capabilities:"):
        ref = ref.split(":", 1)[1].strip()
    # identity.lookup knows every id, name, and slug the capability has ever
    # answered to, renames included.
    capability = identity.lookup(project.id, ref)
    if capability is not None:
        return capability, None
    if is_uuid(ref):
        return None, f"no capability with id {ref!r} in this project"
    qs = Capability.objects.filter(project=project).current()
    if Dataset.objects.filter(project=project, name__iexact=ref).exists():
        return None, (
            f"{ref!r} is a dataset name, not a capability — pass the capability slug "
            "from list_capabilities (e.g. langextract-annotator)."
        )
    slugs = list(qs.order_by("slug").values_list("slug", flat=True)[:5])
    hint = f" Known capabilities: {', '.join(slugs)}." if slugs else ""
    return None, f"no capability {ref!r} in this project.{hint}"


def resolve_dataset(
    project, ref: str, *, capability: Capability | None = None
) -> tuple[Dataset | None, str | None]:
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide a dataset name (from list_datasets) or its id"
    qs = Dataset.objects.filter(project=project).select_related("capability")
    if capability is not None:
        qs = qs.filter(capability=capability)
    if is_uuid(ref):
        ds = qs.filter(id=ref).first()
        return (ds, None) if ds else (None, f"no dataset {ref!r} in this project")
    # Names are labels, not keys: several datasets may share one. The newest wins;
    # every tool result carries the id, which is the unambiguous handle.
    ds = qs.filter(name__iexact=ref).order_by("-created_at").first()
    if ds is not None:
        return ds, None
    ds = qs.filter(name__icontains=ref).order_by("-created_at").first()
    if ds is not None:
        return ds, None
    return None, f"no dataset named {ref!r} in this project"


def resolve_eval_run(project, ref: str) -> tuple[EvalRun | None, str | None]:
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide an eval run name (from list_eval_runs)"
    qs = EvalRun.objects.filter(project=project)
    if is_uuid(ref):
        run = qs.filter(id=ref).first()
        return (run, None) if run else (None, f"no eval run {ref!r} in this project")
    matches = list(qs.filter(name__iexact=ref).order_by("-created_at"))
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        options = ", ".join(f"{r.name} ({r.status}, {r.created_at.date()})" for r in matches[:5])
        return None, f"multiple eval runs named {ref!r} — disambiguate: {options}"
    run = qs.filter(name__icontains=ref).order_by("-created_at").first()
    if run is not None:
        return run, None
    return None, f"no eval run named {ref!r} in this project"


def resolve_behaviour(
    project, ref: str, *, capability: Capability | None = None
) -> tuple[Behaviour | None, str | None]:
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide a task key or display name (from list_behaviours)"
    if ref.startswith("behaviours:"):
        ref = ref.split(":", 1)[1].strip()
    qs = Behaviour.objects.filter(project=project).select_related("capability")
    if capability is not None:
        qs = qs.filter(capability=capability)
    if is_uuid(ref):
        behaviour = qs.filter(id=ref).first()
        return (behaviour, None) if behaviour else (None, f"no task {ref!r} in this project")
    for field in ("key", "display_name"):
        matches = list(qs.filter(**{f"{field}__iexact": ref}).order_by("created_at"))
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            options = ", ".join(
                f"{b.display_name or b.key} ({b.capability.slug})" for b in matches[:5]
            )
            return None, f"multiple tasks match {ref!r}: {options}"
    behaviour = qs.filter(key__icontains=ref).order_by("created_at").first()
    if behaviour is not None:
        return behaviour, None
    return None, f"no task {ref!r} in this project — call list_behaviours first"


def resolve_session(project, ref: str) -> tuple[Conversation | None, str | None]:
    """Resolve a session (Conversation) by external_id, name, or id."""
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide a session external_id or name (from list_sessions)"
    qs = Conversation.objects.filter(project=project).select_related("capability")
    if is_uuid(ref):
        session = qs.filter(id=ref).first()
        if session is not None:
            return session, None
    for field in ("external_id", "name"):
        matches = list(qs.filter(**{f"{field}__iexact": ref}).order_by("-created_at"))
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            options = ", ".join(f"{s.external_id or s.name} ({str(s.id)[:8]})" for s in matches[:5])
            return None, f"multiple sessions match {ref!r} — pass the id: {options}"
    return None, f"no session {ref!r} in this project"


def resolve_connector(project, ref: str) -> tuple[ConnectorCredential | None, str | None]:
    ref = str(ref or "").strip()
    if not ref:
        return None, "provide a connector name (from list_connectors)"
    if ref.startswith("connectors:"):
        ref = ref.split(":", 1)[1].strip()
    qs = ConnectorCredential.objects.filter(project=project)
    if is_uuid(ref):
        c = qs.filter(id=ref).first()
        return (c, None) if c else (None, f"no connector {ref!r} in this project")
    matches = list(qs.filter(name__iexact=ref))
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        options = ", ".join(f"{c.connector_type} ({c.id})" for c in matches[:5])
        return None, f"multiple connectors named {ref!r} — pass the id: {options}"
    return None, f"no connector named {ref!r} in this project"
