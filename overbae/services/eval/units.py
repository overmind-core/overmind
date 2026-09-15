"""Unit carving: which spans of a trace are the scoring units.

Folding needs bindings and bindings need candidate units, so ``carve`` stays pure
over spans and ``fold_interior_steps`` runs once trace scoring has resolved them.
"""

from __future__ import annotations

from dataclasses import dataclass

from overbae.models import Span
from overbae.services.behaviour import binder

TURN = "turn"
ENTRY_POINT = "entry_point"
KEY_SEGMENT = "key_segment"
ROOT = "root"
# Not a lattice tier: the run boundary re-offered as a run-grain surface.
RUN_SURFACE = "run"

_UNIT_KIND_ATTR = "overmind.unit_kind"
_DELIVERY_ATTR = "overmind.delivery"


@dataclass(frozen=True)
class ResolvedUnit:
    unit_span: Span
    member_spans: list[Span]
    ancestor_spans: list[Span]  # outermost first
    carve_source: str  # TURN | ENTRY_POINT | KEY_SEGMENT | ROOT
    interrupted: bool
    degraded: bool  # carved below the declared tiers


@dataclass(frozen=True)
class CarvedTrace:
    root: Span
    rootless: bool
    units: list[ResolvedUnit]
    multi_entry: bool  # True even for a single declared turn
    turn_slices: bool  # units share one run's message history


def _start(span: Span) -> int:
    return span.start_time_ns or 0


def is_turn_unit(span: Span) -> bool:
    return str((span.attributes or {}).get(_UNIT_KIND_ATTR) or "").strip().lower() == TURN


def find_entry_points(spans: list[Span]) -> list[Span]:
    """Capability invocation spans, ordered by start time."""
    return sorted((s for s in spans if binder.is_entry_point(s)), key=_start)


def subtree_spans(spans: list[Span], unit: Span) -> list[Span]:
    """``unit`` plus all its descendants."""
    by_parent: dict[str | None, list[Span]] = {}
    for s in spans:
        by_parent.setdefault(s.parent_span_id, []).append(s)
    out: list[Span] = []
    stack = [unit]
    while stack:
        cur = stack.pop()
        out.append(cur)
        stack.extend(by_parent.get(cur.span_id, ()))
    return out


def root_span(spans: list[Span]) -> Span:
    """The earliest root, or the earliest span when no root arrived."""
    roots = [s for s in spans if s.is_root]
    return min(roots or spans, key=_start)


def ancestor_chain(spans: list[Span], unit: Span) -> list[Span]:
    """The unit's enclosing spans within the trace, outermost first."""
    by_id = {s.span_id: s for s in spans}
    chain: list[Span] = []
    seen = {unit.span_id}
    cursor = by_id.get(unit.parent_span_id or "")
    while cursor is not None and cursor.span_id not in seen:
        seen.add(cursor.span_id)
        chain.append(cursor)
        cursor = by_id.get(cursor.parent_span_id or "")
    chain.reverse()
    return chain


def _declared_units(spans: list[Span]) -> list[tuple[Span, str]]:
    """An entry point enclosing turn units is their run boundary, not a sibling."""
    turns = sorted((s for s in spans if is_turn_unit(s)), key=_start)
    entry_points = find_entry_points(spans)
    if not turns:
        return [(ep, ENTRY_POINT) for ep in entry_points]
    units = [(t, TURN) for t in turns]
    turn_ids = {t.span_id for t in turns}
    for ep in entry_points:
        if ep.span_id in turn_ids:
            continue
        if not any(s.span_id in turn_ids for s in subtree_spans(spans, ep)):
            units.append((ep, ENTRY_POINT))
    return sorted(units, key=lambda pair: _start(pair[0]))


def _is_delivery_span(span: Span) -> bool:
    return str((span.attributes or {}).get(_DELIVERY_ATTR)).lower() == "true"


def _key_segment_units(spans: list[Span], root: Span) -> dict[str, list[Span]]:
    """Shim for flat traces; delete once every SDK emits ``task(key, unit="turn")``.

    Empty unless 2+ distinct keys prove the run is multi-phase. An unkeyed
    ``deliver()`` span joins the latest-started group so declared delivery keeps
    naming the terminal unit.
    """
    groups: dict[str, list[Span]] = {}
    unkeyed_delivery: list[Span] = []
    for span in spans:
        if span.span_id == root.span_id:
            continue
        key = binder.declared_key(span)
        if key:
            groups.setdefault(key, []).append(span)
        elif _is_delivery_span(span):
            unkeyed_delivery.append(span)
    if len(groups) < 2:
        return {}
    for group in groups.values():
        group.sort(key=_start)
    for span in unkeyed_delivery:
        enclosing = [g for g in groups.values() if _start(g[0]) <= _start(span)]
        if enclosing:
            max(enclosing, key=lambda g: _start(g[0])).append(span)
    return {group[0].span_id: group for group in groups.values()}


def _attach_keyed_strays(
    spans: list[Span],
    invocations: list[tuple[Span, str]],
    member_map: dict[str, list[Span]],
) -> None:
    """Callback instrumentors (LangChain/LangGraph) reparent LLM and tool spans
    outside the turn subtree; the declared key is the SDK's membership claim, so
    such spans join the turn declaring their key or the unit's judges starve."""
    by_key: dict[str, list[Span]] = {}
    for span, source in invocations:
        if source != TURN:
            continue
        key = binder.declared_key(span)
        if key:
            by_key.setdefault(key, []).append(span)
    if not by_key:
        return
    claimed = {m.span_id for members in member_map.values() for m in members}
    for span in spans:
        if span.span_id in claimed or is_declared_boundary(span):
            continue
        candidates = by_key.get(binder.declared_key(span))
        if not candidates:
            continue
        preceding = [u for u in candidates if _start(u) <= _start(span)]
        owner = max(preceding, key=_start) if preceding else min(candidates, key=_start)
        member_map[owner.span_id].append(span)


def is_declared_boundary(span: Span) -> bool:
    kind = str((span.attributes or {}).get(_UNIT_KIND_ATTR) or "").strip().lower()
    return kind in ("run", TURN) or binder.is_entry_point(span)


def carve(spans: list[Span]) -> CarvedTrace:
    """Resolve a non-empty trace into its scoring units: declared turns, then
    entry points, then the key-segment shim, then the root."""
    root = root_span(spans)
    rootless = root.parent_span_id is not None

    invocations = [(s, src) for s, src in _declared_units(spans) if s.span_id != root.span_id]
    segment_groups: dict[str, list[Span]] = {}
    if not invocations:
        segment_groups = _key_segment_units(spans, root)
        heads = sorted(segment_groups.values(), key=lambda g: _start(g[0]))
        invocations = [(group[0], KEY_SEGMENT) for group in heads]

    # A single declared turn is still a unit; the root collapse is reserved
    # for the structural tiers, where one invocation is the whole run.
    if len(invocations) < 2 and not any(source == TURN for _, source in invocations):
        unit = ResolvedUnit(
            unit_span=root,
            member_spans=list(spans),
            ancestor_spans=[],
            carve_source=ROOT,
            interrupted=rootless,
            degraded=not is_declared_boundary(root),
        )
        return CarvedTrace(
            root=root, rootless=rootless, units=[unit], multi_entry=False, turn_slices=False
        )

    last_id = invocations[-1][0].span_id
    member_map = {
        span.span_id: segment_groups[span.span_id]
        if source == KEY_SEGMENT
        else subtree_spans(spans, span)
        for span, source in invocations
    }
    _attach_keyed_strays(spans, invocations, member_map)
    units = [
        ResolvedUnit(
            unit_span=span,
            member_spans=member_map[span.span_id],
            ancestor_spans=ancestor_chain(spans, span),
            carve_source=source,
            interrupted=rootless and span.span_id == last_id,
            degraded=source == KEY_SEGMENT,
        )
        for span, source in invocations
    ]
    return CarvedTrace(
        root=root,
        rootless=rootless,
        units=units,
        multi_entry=True,
        turn_slices=any(u.carve_source in (TURN, KEY_SEGMENT) for u in units),
    )


def run_surfaces(spans: list[Span], carved: CarvedTrace) -> list[ResolvedUnit]:
    """Run boundaries enclosing turn slices, offered as run-grain surfaces.
    Candidates only: trace scoring keeps one only when a run-grain behaviour
    binds it."""
    if carved.rootless or not carved.turn_slices:
        return []
    unit_ids = {u.unit_span.span_id for u in carved.units}
    turn_ids = {u.unit_span.span_id for u in carved.units if u.carve_source in (TURN, KEY_SEGMENT)}
    boundaries = [carved.root]
    for entry_point in find_entry_points(spans):
        if entry_point.span_id == carved.root.span_id or entry_point.span_id in unit_ids:
            continue
        if any(s.span_id in turn_ids for s in subtree_spans(spans, entry_point)):
            boundaries.append(entry_point)
    return [
        ResolvedUnit(
            unit_span=boundary,
            member_spans=subtree_spans(spans, boundary),
            ancestor_spans=ancestor_chain(spans, boundary),
            carve_source=RUN_SURFACE,
            interrupted=False,
            degraded=False,
        )
        for boundary in boundaries
    ]


def _span_depth(by_id: dict[str, Span], span: Span) -> int:
    depth, seen = 0, {span.span_id}
    cursor = by_id.get(span.parent_span_id or "")
    while cursor is not None and cursor.span_id not in seen:
        depth += 1
        seen.add(cursor.span_id)
        cursor = by_id.get(cursor.parent_span_id or "")
    return depth


def fold_interior_steps(
    spans: list[Span],
    units: list[Span],
    resolutions: dict[str, binder.BindingResolution | None],
) -> dict[str, str]:
    """Folded turn span_id → surviving enclosing unit span_id. A turn bound to
    the same behaviour as its enclosing unit, not at that behaviour's entry
    anchor, is an interior step, not a sibling execution."""
    by_id = {s.span_id: s for s in spans}
    unit_ids = {u.span_id for u in units}

    def _enclosing_unit_id(unit: Span) -> str | None:
        seen = {unit.span_id}
        cursor = by_id.get(unit.parent_span_id or "")
        while cursor is not None and cursor.span_id not in seen:
            if cursor.span_id in unit_ids:
                return cursor.span_id
            seen.add(cursor.span_id)
            cursor = by_id.get(cursor.parent_span_id or "")
        return None

    folds: dict[str, str] = {}
    for unit in sorted(units, key=lambda u: _span_depth(by_id, u)):
        if not is_turn_unit(unit):
            continue
        enclosing_id = _enclosing_unit_id(unit)
        if enclosing_id is None:
            continue
        resolved = resolutions.get(unit.span_id)
        enclosing = resolutions.get(enclosing_id)
        if (
            resolved is None
            or enclosing is None
            or resolved.version is None
            or enclosing.version is None
        ):
            continue
        if resolved.version.behaviour_id != enclosing.version.behaviour_id:
            continue
        if binder.entry_matches_contract(resolved.entry_qualname, resolved.version):
            continue
        folds[unit.span_id] = folds.get(enclosing_id, enclosing_id)
    return folds
