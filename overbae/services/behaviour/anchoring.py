"""Carries stable Behaviour IDs across rescans: same key, else unique entry
anchor, else unique file-lineage + anchor-overlap match. Ambiguity mints new
rather than merges.
"""

from __future__ import annotations

from typing import Any

from overbae.models import Behaviour, BehaviourVersion
from overbae.services.behaviour.binder import anchor_matches

_LINEAGE_MIN_JACCARD = 0.5


def _anchor_files(contract: dict[str, Any]) -> set[str]:
    return {
        str(a.get("file") or "").split("#", 1)[0]
        for a in contract.get("anchors") or []
        if a.get("file")
    }


def _latest_contract(behaviour: Behaviour) -> dict[str, Any]:
    version = behaviour.versions.order_by("-created_at").first()
    return version.contract if version else {}


def _lineage_match(contract: dict[str, Any], candidates: list[Behaviour]) -> Behaviour | None:
    new_anchors = set(contract.get("anchor_sequence") or [])
    new_files = _anchor_files(contract)
    scored: list[tuple[float, Behaviour]] = []
    for behaviour in candidates:
        old = _latest_contract(behaviour)
        old_anchors = set(old.get("anchor_sequence") or [])
        if not (new_files & _anchor_files(old)):
            continue
        union = new_anchors | old_anchors
        jaccard = len(new_anchors & old_anchors) / len(union) if union else 0.0
        if jaccard >= _LINEAGE_MIN_JACCARD:
            scored.append((jaccard, behaviour))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None  # ambiguity mints new rather than merges
    return scored[0][1]


def _capability_run_entries(capability: Any) -> set[str]:
    entries: set[str] = set()
    fn = (capability.entrypoint_fn or "").strip()
    if fn:
        entries.add(fn)
    card = (capability.improvement_metadata or {}).get("capability_card") or {}
    for anchor in card.get("anchors") or []:
        if (
            isinstance(anchor, dict)
            and anchor.get("kind") == "entry_point"
            and anchor.get("qualname")
        ):
            entries.add(str(anchor["qualname"]))
    return entries


def _hits_run_entry(qualname: str, run_entries: set[str]) -> bool:
    return bool(qualname) and any(
        anchor_matches(qualname, entry) or anchor_matches(entry, qualname) for entry in run_entries
    )


def contract_grain(
    contract: dict[str, Any],
    sibling_contracts: list[dict[str, Any]],
    run_entries: set[str],
    entry_anchor: str = "",
) -> str:
    """``run`` for the decision surface or the only entry-matching path;
    prefix/interior siblings of a decision surface are ``turn``."""
    if str(contract.get("claim") or "") == "decision_surface":
        return Behaviour.Grain.RUN
    entry = str(contract.get("entry_anchor") or entry_anchor or "")
    if not _hits_run_entry(entry, run_entries):
        return Behaviour.Grain.TURN
    sibling_decision = any(
        str(c.get("claim") or "") == "decision_surface" for c in sibling_contracts
    )
    return Behaviour.Grain.TURN if sibling_decision else Behaviour.Grain.RUN


def refresh_grains(capability: Any) -> None:
    """The scan is the only writer of ``Behaviour.grain``."""
    behaviours = list(
        Behaviour.objects.filter(capability=capability, status=Behaviour.Status.ACTIVE)
    )
    contracts = {b.id: _latest_contract(b) for b in behaviours}
    run_entries = _capability_run_entries(capability)
    for behaviour in behaviours:
        siblings = [c for other_id, c in contracts.items() if other_id != behaviour.id]
        grain = contract_grain(
            contracts[behaviour.id], siblings, run_entries, entry_anchor=behaviour.entry_anchor
        )
        if behaviour.grain != grain:
            behaviour.grain = grain
            behaviour.save(update_fields=["grain", "updated_at"])


def reanchor(capability: Any, contracts: list[dict[str, Any]], analyzed_sha: str) -> dict[str, Any]:
    """Returns ``{"carried": [Behaviour], "minted": [Behaviour], "retired": [Behaviour]}``."""
    existing = list(Behaviour.objects.filter(capability=capability))
    unclaimed: dict[Any, Behaviour] = {b.id: b for b in existing}
    carried: list[Behaviour] = []
    minted: list[Behaviour] = []

    def claim(behaviour: Behaviour, contract: dict[str, Any]) -> None:
        unclaimed.pop(behaviour.id, None)
        behaviour.key = contract["key"]
        behaviour.display_name = contract["name"]
        behaviour.entry_anchor = contract["entry_anchor"]
        behaviour.status = Behaviour.Status.ACTIVE
        behaviour.last_seen_sha = analyzed_sha
        if not behaviour.first_seen_sha:
            behaviour.first_seen_sha = analyzed_sha
        behaviour.save()
        carried.append(behaviour)

    pending: list[dict[str, Any]] = []
    for contract in contracts:
        match = next((b for b in unclaimed.values() if b.key == contract["key"]), None)
        if match is not None:
            claim(match, contract)
        else:
            pending.append(contract)

    still_pending: list[dict[str, Any]] = []
    for contract in pending:
        entry = contract["entry_anchor"]
        matches = [b for b in unclaimed.values() if entry and b.entry_anchor == entry]
        if len(matches) == 1:
            claim(matches[0], contract)
        else:
            still_pending.append(contract)

    for contract in still_pending:
        match = _lineage_match(contract, list(unclaimed.values()))
        if match is not None:
            claim(match, contract)
        else:
            behaviour = Behaviour.objects.create(
                project_id=capability.project_id,
                capability=capability,
                key=contract["key"],
                display_name=contract["name"],
                entry_anchor=contract["entry_anchor"],
                first_seen_sha=analyzed_sha,
                last_seen_sha=analyzed_sha,
            )
            minted.append(behaviour)

    by_key = {b.key: b for b in carried + minted}
    for contract in contracts:
        behaviour = by_key.get(contract["key"])
        if behaviour is None:
            continue
        version, created = BehaviourVersion.objects.get_or_create(
            behaviour=behaviour, analyzed_sha=analyzed_sha, defaults={"contract": contract}
        )
        if not created and version.contract != contract:
            version.contract = contract
            version.save(update_fields=["contract"])

    retired: list[Behaviour] = []
    for behaviour in unclaimed.values():
        if behaviour.status == Behaviour.Status.ACTIVE:
            behaviour.status = Behaviour.Status.RETIRED
            behaviour.save(update_fields=["status", "updated_at"])
            retired.append(behaviour)

    refresh_grains(capability)
    return {"carried": carried, "minted": minted, "retired": retired}
