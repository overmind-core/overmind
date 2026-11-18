"""The warnings a dataset version's consumers show."""

from __future__ import annotations

from overbae.services.datasets.contract import public_intent


def warnings(dataset, cell, *, capability=None) -> list[str]:
    findings = list(
        (cell.intent_report.get(public_intent(dataset.intent)) or {}).get("warnings", [])
    )
    contract = cell.capability_report or {}
    if contract and not contract.get("ok"):
        findings.append(contract.get("reason") or "Rows do not match the capability.")
    if capability is not None and dataset.capability_id != capability.id:
        findings.append(f"This dataset has not been reviewed for {capability.name}.")
    return findings
