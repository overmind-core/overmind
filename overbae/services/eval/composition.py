"""No gate nodes: a failed member counts in its own phase and never voids the
rest. The ``gate`` kind survives in the schema so stored graph rows still validate.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from overbae.services.eval.specs import EvaluatorSpec

GROUNDING_NODE = "grounding"
FOLD_NODE = "session_fold"

# Phase names are read by the UI and EDD from the stored payload.
_PHASE_BY_GRAIN = {"unit": "steps", "trajectory": "trajectory", "terminal": "output"}
_PHASE_WEIGHTS = {"steps": 0.25, "trajectory": 0.25, "output": 0.50}
_PHASE_BY_SCOPE = {"step": "steps", "turn": "steps", "trajectory": "trajectory"}

_NODE_KIND_BY_CLAIM = {
    "conformance": "score",
    "verification": "score",
    "safety": "cap",
    "grounding": "grounding",
    "quality": "score",
    "progress": "score",
}

NodeKind = Literal["gate", "score", "cap", "grounding", "fold"]


class GraphNode(BaseModel):
    id: str
    kind: NodeKind
    evaluator_name: str = ""
    claim_type: str = ""
    grain: str = "terminal"
    weight: float = 1.0


class Graph(BaseModel):
    version: int = 1
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[tuple[str, str]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _well_formed(self) -> Graph:
        ids = [n.id for n in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("composition graph has duplicate node ids")
        known = set(ids)
        for src, dst in self.edges:
            if src not in known or dst not in known:
                raise ValueError(f"composition edge ({src!r}, {dst!r}) references unknown node")
        if len(self.stages()) == 0 and self.nodes:
            raise ValueError("composition graph has a cycle")
        return self

    def stages(self) -> list[list[GraphNode]]:
        by_id = {n.id: n for n in self.nodes}
        indegree = dict.fromkeys(by_id, 0)
        out: dict[str, list[str]] = {nid: [] for nid in by_id}
        for src, dst in self.edges:
            indegree[dst] += 1
            out[src].append(dst)
        frontier = sorted(nid for nid, d in indegree.items() if d == 0)
        levels: list[list[GraphNode]] = []
        seen = 0
        while frontier:
            levels.append([by_id[nid] for nid in frontier])
            seen += len(frontier)
            nxt: list[str] = []
            for nid in frontier:
                for dst in out[nid]:
                    indegree[dst] -= 1
                    if indegree[dst] == 0:
                        nxt.append(dst)
            frontier = sorted(nxt)
        return levels if seen == len(by_id) else []

    def node_for(self, evaluator_name: str) -> GraphNode | None:
        return next((n for n in self.nodes if n.evaluator_name == evaluator_name), None)


def node_kind_for_claim(claim_type: str) -> NodeKind:
    return _NODE_KIND_BY_CLAIM.get(claim_type, "score")  # type: ignore[return-value]


def compile_graph(specs: Mapping[str, EvaluatorSpec], *, version: int = 1) -> Graph:
    """Every node feeds the fold directly; no node guards another."""
    nodes: list[GraphNode] = []
    for name in sorted(specs):
        spec = specs[name]
        claim = spec.claim
        nodes.append(
            GraphNode(
                id=name,
                kind=node_kind_for_claim(claim.type),
                evaluator_name=name,
                claim_type=claim.type,
                grain=claim.grain,
            )
        )
    if not any(n.kind == "grounding" for n in nodes):
        nodes.append(
            GraphNode(
                id=GROUNDING_NODE,
                kind="grounding",
                evaluator_name=GROUNDING_NODE,
                claim_type="grounding",
                grain="terminal",
            )
        )
    nodes.append(GraphNode(id=FOLD_NODE, kind="fold", grain="session"))

    edges: list[tuple[str, str]] = [(node.id, FOLD_NODE) for node in nodes if node.id != FOLD_NODE]
    return Graph(version=version, nodes=nodes, edges=edges)


def _phase(entry: dict[str, Any]) -> str:
    grain = str(entry.get("grain") or "")
    if grain in _PHASE_BY_GRAIN:
        return _PHASE_BY_GRAIN[grain]
    scope = str(entry.get("scope") or "")
    if scope in ("step", "turn"):
        return "steps"
    # Checkpoint coverage grades step progress whatever scope it was authored with.
    if any(isinstance(s, dict) and "_checkpoint" in s for s in entry.get("sub_scores") or []):
        return "steps"
    return _PHASE_BY_SCOPE.get(scope, "output")


def _normalized_value(entry: dict[str, Any]) -> float | None:
    """A passing boolean defers to the scalar so 0.2-with-passed-true is not
    laundered into 1.0."""
    if entry.get("outcome") != "scored":
        return None
    if entry.get("passed") is False:
        return 0.0
    score = entry.get("score")
    if isinstance(score, (int, float)):
        return max(0.0, min(1.0, float(score)))
    if entry.get("passed") is True:
        return 1.0
    return None


def _evaluator_entries(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(name): entry
        for name, entry in (block or {}).items()
        if not str(name).startswith("_")
        and name != "invocations"
        and isinstance(entry, dict)
        and "outcome" in entry
    }


def compose(block: dict[str, Any] | None, graph: Graph | None = None) -> dict[str, Any] | None:
    """No verdict has veto authority except safety caps: the composite never
    reads healthier than the worst cap verdict."""
    entries = _evaluator_entries(block or {})
    if not entries:
        return None
    cap_names = {n.evaluator_name for n in graph.nodes if n.kind in ("cap",)} if graph else set()

    phases: dict[str, dict[str, Any]] = {
        name: {"score": None, "weight": weight, "n": 0, "na": 0}
        for name, weight in _PHASE_WEIGHTS.items()
    }
    sums: dict[str, float] = dict.fromkeys(phases, 0.0)
    not_applicable = 0
    for entry in entries.values():
        phase = _phase(entry)
        if entry.get("outcome") == "not_applicable":
            phases[phase]["na"] += 1
            not_applicable += 1
            continue
        value = _normalized_value(entry)
        if value is None:
            continue
        phases[phase]["n"] += 1
        sums[phase] += value

    active = [name for name, p in phases.items() if p["n"] > 0]
    for name in active:
        phases[name]["score"] = round(sums[name] / phases[name]["n"], 4)

    score: float | None = None
    if active:
        # An empty phase's weight redistributes proportionally to the others.
        total_weight = sum(_PHASE_WEIGHTS[name] for name in active)
        score = round(
            sum(phases[name]["score"] * _PHASE_WEIGHTS[name] for name in active) / total_weight, 4
        )
    failure_cap: float | None = None
    cap_evaluator = ""
    caps = [
        (value, name)
        for name, entry in entries.items()
        if (name in cap_names or str(entry.get("surface_area") or "") == "failure_mode")
        and (value := _normalized_value(entry)) is not None
    ]
    if caps and score is not None:
        worst, worst_name = min(caps)
        if worst < score:
            failure_cap = worst
            cap_evaluator = worst_name
            score = failure_cap

    result = {
        "score": score,
        "phases": phases,
        "evaluations": len(entries),
        "not_applicable": not_applicable,
        # The list surfaces read the block after the per-entry results moved to
        # Verdict rows, so the boolean-failure marker rides the composite.
        "any_failed": any(
            entry.get("outcome") == "scored" and entry.get("passed") is False
            for entry in entries.values()
        ),
    }
    if failure_cap is not None:
        result["failure_mode_cap"] = failure_cap
        result["cap_evaluator"] = cap_evaluator
    # Two task-success verdicts >0.5 apart never average silently. Grounding
    # and safety caps grade different constructs and stay out.
    outcome_lane = {
        name: value
        for name, entry in entries.items()
        if name != GROUNDING_NODE
        and name not in cap_names
        and str(entry.get("surface_area") or "") != "failure_mode"
        and _phase(entry) == "output"
        and (value := _normalized_value(entry)) is not None
    }
    if len(outcome_lane) >= 2:
        spread = max(outcome_lane.values()) - min(outcome_lane.values())
        if spread > 0.5:
            result["conflict"] = {
                "lane": "output",
                "spread": round(spread, 4),
                "members": {name: round(value, 4) for name, value in outcome_lane.items()},
            }
    return result
