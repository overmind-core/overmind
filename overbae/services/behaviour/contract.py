"""Author-time validation of a user-picked task/step against its Behaviour
contract — distinct from ``surface_binding.py``, which validates authoring
vocabulary against the capability *card*, a different artifact.
"""

from __future__ import annotations

from typing import Any

from overbae.services.behaviour.scoring import segment_ran
from overbae.services.eval.card_compiler import judged_backbone_steps


def available_steps(contract: dict[str, Any]) -> list[dict[str, Any]]:
    """The contract's judgeable backbone steps, in the same shape and order
    the machine-authored suite already uses (``card_compiler.compile_behaviour_suites``)."""
    return judged_backbone_steps(contract)


def anchor_segment_valid(segment: list[str], contract: dict[str, Any]) -> bool:
    """Whether ``segment`` is an ordered, suffix-tolerant subsequence of the
    contract's declared ``anchor_sequence`` — the same check scoring applies
    to *observed* anchors, applied here to the *declared* ones."""
    return segment_ran(segment, contract.get("anchor_sequence") or [])
