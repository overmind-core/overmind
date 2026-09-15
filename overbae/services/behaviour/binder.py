"""Binds a unit onto the scanned Capability → Behaviour → Trajectory map.

Identity first, SHA second: the declared key wins when its behaviour's grain
fits the unit, else grain + most-specific inclusion; SHA only picks the
version afterwards, and a missing client SHA never unbinds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from overbae.models import (
    Behaviour,
    BehaviourVersion,
    Capability,
    Span,
    TaskExecution,
)

logger = logging.getLogger(__name__)

CODE_NAMESPACE = "code.namespace"
CODE_FUNCTION_NAME = "code.function.name"
CODE_FUNCTION_LEGACY = "code.function"
VCS_SHA = "vcs.ref.head.revision"
# Declared mapping; either key wins over the structural join.
DECLARED_BEHAVIOUR_KEY = "overmind.behaviour.key"
DECLARED_TASK_KEY = "overmind.task"
_DECLARED_KEYS = (DECLARED_BEHAVIOUR_KEY, DECLARED_TASK_KEY)
_UNIT_KIND_ATTR = "overmind.unit_kind"

FLAG_UNANALYZED_SHA = "unanalyzed_sha"
FLAG_SHA_DRIFT = "sha_drift"
FLAG_AMBIGUOUS = "ambiguous_anchor_overlap"
FLAG_UNKNOWN_ANCHORS = "unknown_anchors"
FLAG_NO_SHA = "missing_sha"
FLAG_NO_CAPABILITY = "missing_capability"
FLAG_DECLARED_GRAIN_MISMATCH = "declared_grain_mismatch"
FLAG_GRAIN_MISMATCH = "grain_mismatch"
FLAG_DEGRADED_CARVE = "degraded_carve"

_ENTRY_POINT = "entry_point"


def is_entry_point(span: Span) -> bool:
    """Run boundary. ``unit_kind="run"`` counts without entry_point typing: the
    SDK's remote-parent exemption lets a subprocess declare a run mid-trace on
    a function-typed span."""
    if (span.span_type or "").strip().lower() == _ENTRY_POINT:
        return True
    attrs = span.attributes or {}
    explicit = (
        attrs.get("overmind.span.type") or attrs.get("overmind.span_type") or attrs.get("type")
    )
    if isinstance(explicit, str) and explicit.strip().lower() == _ENTRY_POINT:
        return True
    return str(attrs.get(_UNIT_KIND_ATTR) or "").strip().lower() == "run"


# Current SDKs stamp ``conversation.id`` on children via OTel context; the
# harness span (opened before set_conversation) may only have the legacy key.
_CONVERSATION_ATTRS = ("conversation.id", "overmind.conversation.id")


def span_qualname(span: Span) -> str:
    attrs = span.attributes or {}
    fn = str(attrs.get(CODE_FUNCTION_NAME) or attrs.get(CODE_FUNCTION_LEGACY) or "").strip()
    ns = str(attrs.get(CODE_NAMESPACE) or "").strip()
    if ns and fn:
        return f"{ns}.{fn}"
    return fn


def trace_sha(spans: list[Span]) -> str:
    for span in spans:
        # Resource attrs are canonical; span attrs are the fallback for exporters
        # (hand-rolled smoke harnesses, third-party pipelines) that stamp per-span.
        sha = str(
            (span.resource_attrs or {}).get(VCS_SHA) or (span.attributes or {}).get(VCS_SHA) or ""
        ).strip()
        if sha:
            return sha
    return ""


def project_vcs_sha(project_id: str) -> str:  # noqa: ARG001
    """No repo SHA to prefer — bind uses the client SHA only."""
    return ""


def attach_inferred_vcs_sha(
    project_id: str,  # noqa: ARG001
    resource_attrs: dict[str, object],
) -> dict[str, object]:
    """No repo SHA to stamp."""
    return resource_attrs


def observed_anchor_sequence(unit_spans: list[Span]) -> list[str]:
    """Ordered qualnames of anchored spans; consecutive duplicates collapsed."""
    ordered = sorted(unit_spans, key=lambda s: s.start_time_ns or 0)
    out: list[str] = []
    for span in ordered:
        qualname = span_qualname(span)
        if qualname and (not out or out[-1] != qualname):
            out.append(qualname)
    return out


def _candidate_versions(capability: Capability, sha: str) -> tuple[list[BehaviourVersion], bool]:
    """Exact-sha contracts when ``sha`` was analyzed, else each active behaviour's
    newest version (``drift=True``). An empty sha must never empty candidates."""
    active = BehaviourVersion.objects.filter(
        behaviour__capability=capability,
        behaviour__status=Behaviour.Status.ACTIVE,
    ).select_related("behaviour")
    if sha:
        exact = list(active.filter(analyzed_sha=sha))
        if exact:
            return exact, False
    newest: dict[object, BehaviourVersion] = {}
    for version in active.order_by("behaviour_id", "-created_at"):
        newest.setdefault(version.behaviour_id, version)
    return list(newest.values()), True


def anchor_matches(observed: str, contract_anchor: str) -> bool:
    """Dotted-suffix match survives analyzer-vs-runtime module-prefix drift
    (``python-agent.agent.run`` vs ``agent.run``); two modules sharing a tail collide."""
    if observed == contract_anchor:
        return True
    return observed.endswith("." + contract_anchor) or contract_anchor.endswith("." + observed)


def _anchor_set(version: BehaviourVersion) -> set[str]:
    contract = version.contract or {}
    anchors = set(contract.get("anchor_sequence") or [])
    anchors.update(a.get("qualname") for a in contract.get("anchors") or [] if a.get("qualname"))
    return anchors


def _route_evidence(observed: list[str], ancestors: list[str]) -> list[str]:
    return [*ancestors, *observed]


def _matched_route(
    version: BehaviourVersion, observed: list[str], ancestors: list[str]
) -> list[str]:
    evidence = _route_evidence(observed, ancestors)
    return sorted({a for a in _anchor_set(version) if any(anchor_matches(q, a) for q in evidence)})


def _unmatched_count(version: BehaviourVersion, matched: list[str]) -> int:
    return len(_anchor_set(version) - set(matched))


def _grain_eligible(
    version: BehaviourVersion,
    unit_grain: str,
    observed: list[str],
) -> bool:
    """``Behaviour.grain`` is written by ``anchoring.refresh_grains`` — never inferred here."""
    if unit_grain not in ("run", "turn"):
        return True
    grain = version.behaviour.grain
    if grain == unit_grain:
        return True
    # Cross-grain is legitimate in both directions — a handoff unit is stamped
    # turn but *is* the new capability's run, and the scan can grade a
    # capability's primary entrypoint as turn while production carves the whole
    # request as one run unit. The entry anchor is the identity that decides.
    if {unit_grain, grain} == {"turn", "run"}:
        entry = str(
            (version.contract or {}).get("entry_anchor") or version.behaviour.entry_anchor or ""
        )
        unit_entry = observed[0] if observed else ""
        return bool(
            unit_entry and (anchor_matches(unit_entry, entry) or anchor_matches(entry, unit_entry))
        )
    return False


def _coverage_anchors(
    scored: list[tuple[list[str], BehaviourVersion]],
    winner: BehaviourVersion,
) -> list[str]:
    """Interior / sibling tasks present on this route — recorded, not rebound."""
    extra: set[str] = set()
    for matched, version in scored:
        if version.behaviour_id != winner.behaviour_id:
            extra.update(matched)
    return sorted(extra)


def _match_version(
    versions: list[BehaviourVersion],
    observed: list[str],
    ancestors: list[str],
    *,
    unit_grain: str = "",
) -> tuple[BehaviourVersion | None, list[str], list[str]]:
    """Most-specific inclusion: fewest unmatched contract anchors among
    candidates whose matched set is not a proper subset of another's.
    Same unmatched count: most distinctive (non-shared) matched anchors.
    Residual tie or zero overlap parks unbound — no sole-candidate shortcut."""
    present: list[tuple[list[str], BehaviourVersion]] = []
    scored: list[tuple[list[str], BehaviourVersion]] = []
    for version in versions:
        matched = _matched_route(version, observed, ancestors)
        if not matched:
            continue
        present.append((matched, version))
        if _grain_eligible(version, unit_grain, observed):
            scored.append((matched, version))
    if not scored:
        # Anchors matched but every candidate was grain-blocked: that is a
        # grain mismatch, not an ambiguous overlap.
        if present:
            return None, [], [FLAG_GRAIN_MISMATCH]
        return None, [], [FLAG_AMBIGUOUS] if versions else []

    remaining = [
        (matched, version)
        for matched, version in scored
        if not any(set(matched) < set(other) for other, _ in scored)
    ]
    if not remaining:
        remaining = scored

    remaining.sort(key=lambda pair: _unmatched_count(pair[1], pair[0]))
    best_unmatched = _unmatched_count(remaining[0][1], remaining[0][0])
    tied = [pair for pair in remaining if _unmatched_count(pair[1], pair[0]) == best_unmatched]
    if len(tied) == 1:
        matched, version = tied[0]
        return (
            version,
            sorted({*matched, *_coverage_anchors(present, version)}),
            [],
        )

    shared = set.intersection(*(_anchor_set(version) for _, version in tied))
    tied.sort(key=lambda pair: len(set(pair[0]) - shared), reverse=True)
    if len(set(tied[0][0]) - shared) > len(set(tied[1][0]) - shared):
        matched, version = tied[0]
        return (
            version,
            sorted({*matched, *_coverage_anchors(present, version)}),
            [],
        )
    return None, [], [FLAG_AMBIGUOUS]


def _known_anchor_qualnames(versions: list[BehaviourVersion]) -> set[str]:
    known: set[str] = set()
    for version in versions:
        known.update(version.contract.get("anchor_sequence") or [])
        known.update(
            a.get("qualname") for a in version.contract.get("anchors") or [] if a.get("qualname")
        )
    return known


def _observed_terminal(unit_span: Span, *, interrupted: bool = False) -> str:
    # Status-code heuristic only; returns_empty/escalates needs output inspection.
    if unit_span.status_code == 2:
        return "error_exit"
    return "interrupted" if interrupted else "emits_record"


def _execution_status(unit_span: Span, *, interrupted: bool) -> str:
    if unit_span.status_code == 2:
        return TaskExecution.Status.ERROR
    if interrupted:
        return TaskExecution.Status.INTERRUPTED
    return TaskExecution.Status.COMPLETED


def _span_conversation_id(span: Span) -> str:
    attrs = getattr(span, "attributes", None) or {}
    for key in _CONVERSATION_ATTRS:
        raw = attrs.get(key)
        if raw:
            return str(raw)[:512]
    return ""


def unit_conversation_id(unit_span: Span, unit_spans: list[Span] | None = None) -> str:
    """Wire ``conversation.id`` for the scoring unit — root first, then children."""
    cid = _span_conversation_id(unit_span)
    if cid:
        return cid
    for span in unit_spans or []:
        cid = _span_conversation_id(span)
        if cid:
            return cid
    return ""


def declared_key(span: Span) -> str:
    attrs = span.attributes or {}
    for key in _DECLARED_KEYS:
        value = str(attrs.get(key) or "").strip()
        if value:
            return value
    return ""


def resolve_unit_capability(
    project_id: str, unit_span: Span, unit_spans: list[Span] | None = None
) -> Capability | None:
    """Span-level identity wins over the process resource and the stored FK."""
    # Lazy: otlp imports this module.
    from overbae.api.otlp import _overmind_tags_from_span, _resolve_capability
    from overbae.models import Project

    project = Project.objects.filter(pk=project_id).first()
    if project is None:
        return None
    seen: set[str] = set()
    for span in [unit_span, *(unit_spans or [])]:
        if span.span_id in seen:
            continue
        seen.add(span.span_id)
        resolved = _resolve_capability(
            project, span.resource_attrs or {}, _overmind_tags_from_span(span)
        )
        if resolved is not None:
            return resolved
    return None


@dataclass(frozen=True)
class BindingResolution:
    capability: Capability | None
    version: BehaviourVersion | None
    binding_source: str
    matched_anchors: list[str]
    flags: list[str]
    sha: str
    entry_qualname: str
    observed: list[str]
    ancestors: list[str]
    unknown_anchors: list[str]


def entry_matches_contract(entry_qualname: str, version: BehaviourVersion) -> bool:
    """True when the qualname IS the task's own entry symbol — the unit is
    that task's grain, not an interior step of it."""
    entry = str(
        (version.contract or {}).get("entry_anchor") or version.behaviour.entry_anchor or ""
    )
    return bool(entry_qualname and entry and anchor_matches(entry_qualname, entry))


def resolve_binding(
    *,
    unit_span: Span,
    unit_spans: list[Span],
    capability: Capability | None,
    project_id: str,
    ancestor_spans: list[Span] | None = None,
) -> BindingResolution:
    """Pure half of :func:`bind_execution`, consulted by unit carving before any
    row exists — must never create or update rows."""
    capability = resolve_unit_capability(project_id, unit_span, unit_spans) or capability
    client_sha = trace_sha(unit_spans) or trace_sha(ancestor_spans or [])
    sha = client_sha or project_vcs_sha(project_id)
    entry_qualname = span_qualname(unit_span)
    observed = observed_anchor_sequence(unit_spans)
    ancestors = observed_anchor_sequence(ancestor_spans or [])
    declared = declared_key(unit_span)
    unit_grain = str((unit_span.attributes or {}).get(_UNIT_KIND_ATTR) or "").strip().lower()
    if not unit_grain and is_entry_point(unit_span):
        # An entry_point span is a capability invocation, so its unit is a run
        # even when the SDK predates unit_kind.
        unit_grain = "run"

    flags: list[str] = []
    version: BehaviourVersion | None = None
    binding_source = TaskExecution.BindingSource.UNBOUND

    if capability is None:
        flags.append(FLAG_NO_CAPABILITY)
        versions: list[BehaviourVersion] = []
    else:
        if not sha:
            flags.append(FLAG_NO_SHA)
        versions, drift = _candidate_versions(capability, sha)
        if sha and drift:
            flags.append(FLAG_SHA_DRIFT if versions else FLAG_UNANALYZED_SHA)

    matched_anchors: list[str] = []
    if declared and versions:
        version = next((v for v in versions if v.behaviour.key == declared), None)
        if version is not None and not _grain_eligible(version, unit_grain, observed):
            flags.append(FLAG_DECLARED_GRAIN_MISMATCH)
            version = None
        if version is not None:
            binding_source = TaskExecution.BindingSource.DECLARED
            matched_anchors = _matched_route(version, observed, ancestors)
    if version is None and versions:
        version, matched_anchors, match_flags = _match_version(
            versions,
            observed,
            ancestors,
            unit_grain=unit_grain,
        )
        flags.extend(match_flags)
        if version is not None:
            binding_source = TaskExecution.BindingSource.ANCHOR_JOIN

    unknown = [q for q in observed if q not in _known_anchor_qualnames(versions)]
    if unknown and versions:
        flags.append(FLAG_UNKNOWN_ANCHORS)

    return BindingResolution(
        capability=capability,
        version=version,
        binding_source=binding_source,
        matched_anchors=matched_anchors,
        flags=flags,
        sha=sha,
        entry_qualname=entry_qualname,
        observed=observed,
        ancestors=ancestors,
        unknown_anchors=unknown,
    )


def bind_execution(
    *,
    unit_span: Span,
    unit_spans: list[Span],
    capability: Capability | None,
    project_id: str,
    interrupted: bool = False,
    ancestor_spans: list[Span] | None = None,
    resolution: BindingResolution | None = None,
    folded_steps: list[dict[str, str]] | None = None,
    carve_source: str = "",
    degraded_carve: bool = False,
) -> TaskExecution:
    """Idempotent per (project, unit_span). ``interrupted``: rootless trace, the
    row must not read completed. ``capability=None`` still mints the row, unbound
    and flagged. ``ancestor_spans``: enclosing chain outermost first — route
    evidence a carved subtree cannot carry itself."""
    resolved = resolution or resolve_binding(
        unit_span=unit_span,
        unit_spans=unit_spans,
        capability=capability,
        project_id=project_id,
        ancestor_spans=ancestor_spans,
    )
    version = resolved.version

    observed_route = {
        "sha": resolved.sha,
        "entry_qualname": resolved.entry_qualname,
        "anchors": resolved.observed,
        "ancestors": resolved.ancestors,
        "matched_anchors": resolved.matched_anchors,
        "unknown_anchors": resolved.unknown_anchors,
        "terminal": _observed_terminal(unit_span, interrupted=interrupted),
    }
    if carve_source:
        observed_route["carve_source"] = carve_source
    if folded_steps:
        observed_route["step_coverage"] = folded_steps
    if version is not None:
        observed_route["contract_sha"] = version.analyzed_sha
    flags = list(resolved.flags)
    if degraded_carve:
        flags.append(FLAG_DEGRADED_CARVE)
    started_at = (
        datetime.fromtimestamp(unit_span.start_time_ns / 1e9, tz=UTC)
        if unit_span.start_time_ns
        else None
    )
    duration_ms = None
    if unit_span.start_time_ns and unit_span.end_time_ns:
        duration_ms = (unit_span.end_time_ns - unit_span.start_time_ns) // 1_000_000

    execution, _ = TaskExecution.objects.update_or_create(
        project_id=project_id,
        unit_span_id=unit_span.span_id,
        defaults={
            "capability": resolved.capability,
            "behaviour": version.behaviour if version else None,
            "behaviour_version": version,
            "trace_id": unit_span.trace_id,
            "conversation_id": unit_conversation_id(unit_span, unit_spans),
            "binding_source": resolved.binding_source,
            "observed_route": observed_route,
            "route_flags": flags,
            "terminal_kind": observed_route["terminal"],
            "status": _execution_status(unit_span, interrupted=interrupted),
            "started_at": started_at,
            "duration_ms": duration_ms,
        },
    )
    return execution
