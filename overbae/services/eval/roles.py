"""Evaluator → eval-set role applicability.

The single source of truth for the add-members guard, the catalog serializer's
``applicable_roles``, the generate-evals role split and the Default set preload.
The two roles are not mutually exclusive.
"""

from __future__ import annotations

from overbae.models import EvalSetMember, Evaluator
from overbae.services.eval.evidence import HARNESS_ARTIFACT, REFERENCE

GENERATIVE = EvalSetMember.Role.GENERATIVE
TRACE_SCORING = EvalSetMember.Role.TRACE_SCORING

SURFACE_MODEL = Evaluator.Surface.MODEL
SURFACE_HARNESS = Evaluator.Surface.HARNESS
SURFACE_ANY = Evaluator.Surface.ANY

# Corpus-level scopes grade a statistic over many samples, not a single trace.
_CORPUS_SCOPES = frozenset({Evaluator.Scope.SAMPLE, Evaluator.Scope.DATASET})


def trace_scoring_applicable(
    scope: str | None,
    evidence_requirement: str | None,
    requires_reference: bool,
) -> bool:
    """A live trace has no curated golden reference, no harness-assembled
    ``structured`` object, and is one trace rather than a corpus — a grader
    needing any of those three is generation-only."""
    if requires_reference:
        return False
    if evidence_requirement in (REFERENCE, HARNESS_ARTIFACT):
        return False
    return scope not in _CORPUS_SCOPES


def roles_for_scope(
    scope: str | None,
    *,
    evidence_requirement: str | None = "",
    requires_reference: bool = False,
    surface: str | None = SURFACE_ANY,
) -> tuple[str, ...]:
    """The structural role(s) a grader of this shape may serve.

    A ``harness``-surface grader needs the assembled capability record, which the
    generate path (model + replay) cannot produce. A ``model``-surface grader is
    dropped from trace scoring, which grades the harness deliverable rather than
    the raw model extraction. ``any`` fires neither gate.
    """
    resolved_surface = surface or SURFACE_ANY
    roles: list[str] = []
    if resolved_surface != SURFACE_HARNESS:
        roles.append(GENERATIVE)
    if resolved_surface != SURFACE_MODEL and trace_scoring_applicable(
        scope, evidence_requirement, requires_reference
    ):
        roles.append(TRACE_SCORING)
    # Never persist a member with no role: a harness-surface grader that also
    # can't trace-score still runs on the generate path, which has the golden.
    return tuple(roles) or (GENERATIVE,)


def is_role_applicable(
    scope: str | None,
    role: str,
    *,
    evidence_requirement: str | None = "",
    requires_reference: bool = False,
    surface: str | None = SURFACE_ANY,
) -> bool:
    return role in roles_for_scope(
        scope,
        evidence_requirement=evidence_requirement,
        requires_reference=requires_reference,
        surface=surface,
    )


def _resolve_roles(
    explicit: list[str] | None,
    scope: str | None,
    evidence_requirement: str | None,
    requires_reference: bool,
    surface: str | None = SURFACE_ANY,
) -> tuple[str, ...]:
    """Structure (scope + evidence + surface) is authoritative: an explicit
    ``applicable_roles`` may only NARROW to a subset of the structurally-allowed
    roles, never grant one the shape can't support. A narrowing that empties the
    set keeps the first allowed role so an evaluator is never orphaned."""
    allowed = roles_for_scope(
        scope,
        evidence_requirement=evidence_requirement,
        requires_reference=requires_reference,
        surface=surface,
    )
    if explicit:
        narrowed = tuple(role for role in allowed if role in explicit)
        return narrowed or allowed[:1]
    return allowed


def blocked_on_of(obj) -> list[str]:
    provenance = getattr(obj, "provenance", None)
    if provenance is not None:
        return list(getattr(provenance, "blocked_on", None) or [])
    return list(
        ((getattr(obj, "config", None) or {}).get("provenance") or {}).get("blocked_on") or []
    )


def blocks_generate(blocked_on: list[str] | None) -> bool:
    return any("generate-mode" in str(item) for item in (blocked_on or []))


def _without_blocked_generate(
    roles: tuple[str, ...], blocked_on: list[str] | None
) -> tuple[str, ...]:
    if not blocks_generate(blocked_on):
        return roles
    return tuple(role for role in roles if role != GENERATIVE)


def roles_for_evaluator(evaluator) -> tuple[str, ...]:
    return _without_blocked_generate(
        _resolve_roles(
            evaluator.applicable_roles,
            evaluator.scope,
            evaluator.evidence_requirement or "",
            bool(evaluator.requires_reference),
            getattr(evaluator, "surface", SURFACE_ANY) or SURFACE_ANY,
        ),
        blocked_on_of(evaluator),
    )


def roles_for_spec(spec) -> tuple[str, ...]:
    return _without_blocked_generate(
        _resolve_roles(
            spec.applicable_roles,
            spec.scope,
            spec.effective_evidence(),
            spec.requires_reference,
            getattr(spec, "surface", SURFACE_ANY) or SURFACE_ANY,
        ),
        blocked_on_of(spec),
    )
