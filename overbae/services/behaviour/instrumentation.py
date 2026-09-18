"""Build read-only instrumentation tickets from the behaviour registry."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from django.db.models import Prefetch

from overbae.models import Behaviour, BehaviourVersion, Capability
from overbae.services.codebase.anchors import module_dotted_path

_SPECIALIZED_DECORATORS = {
    "tool": "@overmind.tool()",
    "workflow": "@overmind.workflow()",
    "retrieval": "@overmind.retrieval()",
    "entry_point": "@overmind.entry_point()",
}


def _target_for_anchor(qualname: str, anchor: dict[str, Any]) -> dict[str, Any]:
    file_ref = str(anchor.get("file") or "")
    file = file_ref.split("#", 1)[0]
    module = module_dotted_path(file) if file else ""
    local = qualname
    if module and qualname.startswith(module + "."):
        local = qualname[len(module) + 1 :]
    else:
        parts = module.split(".") if module else []
        for index in range(len(parts)):
            suffix = ".".join(parts[index:])
            if qualname.startswith(suffix + "."):
                module = suffix
                local = qualname[len(suffix) + 1 :]
                break
    symbol = local.split(".")[0] if local else ""
    return {
        "file": file,
        "qualname": qualname,
        "module": module,
        "import_line": f"from {module} import {symbol}" if module and symbol else "",
    }


def _target(entry_anchor: str, contract: dict[str, Any], behaviour: Behaviour) -> dict[str, Any]:
    anchor = next(
        (
            item
            for item in contract.get("anchors") or []
            if isinstance(item, dict) and item.get("qualname") == entry_anchor
        ),
        {},
    )
    return _target_for_anchor(entry_anchor or behaviour.entry_anchor, anchor)


def _canonical_anchors(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    by_qualname: dict[str, dict[str, Any]] = {}
    for anchor in contract.get("anchors") or []:
        if not isinstance(anchor, dict):
            continue
        qualname = anchor.get("qualname")
        if isinstance(qualname, str) and qualname:
            by_qualname.setdefault(qualname, anchor)

    ordered: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    for qualname in contract.get("anchor_sequence") or []:
        if not isinstance(qualname, str) or not qualname or qualname in seen:
            continue
        anchor = by_qualname.get(qualname)
        if anchor is None:
            continue
        seen.add(qualname)
        ordered.append((qualname, anchor))
    return ordered


def _required_spans(
    contract: dict[str, Any], behaviour: Behaviour, shared: bool
) -> list[dict[str, Any]]:
    entry_anchor = str(contract.get("entry_anchor") or behaviour.entry_anchor or "")
    spans: list[dict[str, Any]] = []
    for qualname, anchor in _canonical_anchors(contract):
        primary = qualname == entry_anchor
        if primary and behaviour.grain == Behaviour.Grain.RUN and not shared:
            continue
        kind = str(anchor.get("kind") or "")
        decorator = (
            "@overmind.observe()"
            if primary and kind == "entry_point"
            else _SPECIALIZED_DECORATORS.get(kind, "@overmind.observe()")
        )
        spans.append(
            {
                "target": _target_for_anchor(qualname, anchor),
                "required_decorator": decorator,
            }
        )
    return spans


def _latest_rows(
    project: Any, capabilities: list[Capability]
) -> list[tuple[Behaviour, BehaviourVersion | None]]:
    versions = BehaviourVersion.objects.order_by("-created_at")
    behaviours = (
        Behaviour.objects.filter(
            project=project,
            capability__in=capabilities,
            status=Behaviour.Status.ACTIVE,
        )
        .select_related("capability")
        .prefetch_related(
            Prefetch("versions", queryset=versions, to_attr="instrumentation_versions")
        )
        .order_by("capability__name", "key")
    )
    return [
        (behaviour, (getattr(behaviour, "instrumentation_versions", []) or [None])[0])
        for behaviour in behaviours
    ]


def _contract(version: BehaviourVersion | None) -> dict[str, Any]:
    value = version.contract if version is not None else {}
    return value if isinstance(value, dict) else {}


def _fingerprint(contract: dict[str, Any]) -> str:
    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _required_scope(key: str, grain: str, shared: bool, capability_id: str) -> str:
    if shared:
        return 'with overmind.task(<selected key>, unit="turn"): ...'
    if grain == Behaviour.Grain.RUN:
        return f'@overmind.run(capability_id="{capability_id}")'
    return f'@overmind.task({json.dumps(key)}, unit="turn")'


def _required_identity(capability: Capability) -> dict[str, str]:
    capability_id = str(capability.id)
    return {
        "capability_id": capability_id,
        "capability_name": capability.name,
        "how": (
            f'overmind.init(providers=[], capability_id="{capability_id}") at the entry module, '
            f'or wrap the entry in overmind.capability(id="{capability_id}")'
        ),
    }


def _capability_summary(capability: Capability) -> dict[str, str]:
    return {
        "id": str(capability.id),
        "name": capability.name,
        "slug": capability.slug,
    }


def _ticket(
    capability: Capability,
    behaviour: Behaviour,
    version: BehaviourVersion | None,
    entry_members: dict[str, list[str]],
) -> dict[str, Any]:
    contract = _contract(version)
    entry_anchor = str(contract.get("entry_anchor") or behaviour.entry_anchor or "")
    allowed_keys = sorted(entry_members.get(entry_anchor, []))
    shared = len(allowed_keys) > 1
    return {
        "key": behaviour.key,
        "behaviour_id": str(behaviour.id),
        "version_id": str(version.id) if version is not None else None,
        "version_analyzed_sha": version.analyzed_sha if version is not None else "",
        "contract_fingerprint": _fingerprint(contract) if version is not None else "",
        "capability": capability.name,
        "capability_id": str(capability.id),
        "placement_mode": "dynamic_key" if shared else "fixed",
        "allowed_keys": allowed_keys if shared else [],
        "grain": behaviour.grain,
        "target": _target(entry_anchor, contract, behaviour),
        "required_scope": _required_scope(
            behaviour.key, behaviour.grain, shared, str(capability.id)
        ),
        "required_spans": _required_spans(contract, behaviour, shared),
        "required_identity": _required_identity(capability),
    }


def instrumentation_tickets(
    project: Any, capability: Capability | None = None, behaviour_ref: str = ""
) -> dict[str, Any]:
    """Return placement tickets for active, already-registered contracts."""
    if capability is not None and capability.project_id != project.id:
        return {"error": "capability does not belong to this project"}

    capabilities = (
        [capability]
        if capability is not None
        else list(Capability.objects.filter(project=project).current().order_by("name", "id"))
    )
    if not capabilities:
        return {"error": "no behaviour registry; scan the repository on Overmind first"}

    rows = _latest_rows(project, capabilities)
    by_capability: dict[Any, list[tuple[Behaviour, BehaviourVersion | None]]] = {
        cap.id: [] for cap in capabilities
    }
    for row in rows:
        by_capability[row[0].capability_id].append(row)

    selected_rows = rows
    ref = behaviour_ref.strip()
    if ref:
        selected_rows = [
            row for row in rows if row[0].key.casefold() == ref.casefold() or str(row[0].id) == ref
        ]
        if not selected_rows:
            cap_name = capability.name if capability is not None else "project"
            return {"error": f"behaviour {ref!r} not found for {cap_name}"}

    tickets: list[dict[str, Any]] = []
    for cap in capabilities:
        cap_rows = by_capability[cap.id]
        entry_members: dict[str, list[str]] = {}
        for behaviour, version in cap_rows:
            contract = _contract(version)
            entry_anchor = str(contract.get("entry_anchor") or behaviour.entry_anchor or "")
            if entry_anchor:
                entry_members.setdefault(entry_anchor, []).append(behaviour.key)
        for keys in entry_members.values():
            keys.sort()
        for behaviour, version in selected_rows:
            if behaviour.capability_id == cap.id:
                tickets.append(_ticket(cap, behaviour, version, entry_members))

    tickets.sort(key=lambda ticket: (ticket["target"]["file"], ticket["key"]))
    if capability is not None:
        return {"capability": _capability_summary(capability), "placements": tickets}
    if not tickets:
        return {"error": "no behaviour registry; scan the repository on Overmind first"}
    return {"placements": tickets}
