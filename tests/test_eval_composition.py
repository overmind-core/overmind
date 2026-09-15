"""No gate nodes: nothing in the graph has veto authority over the fold."""

import pytest

from overbae.services.eval.composition import (
    FOLD_NODE,
    GROUNDING_NODE,
    Graph,
    GraphNode,
    compile_graph,
)
from overbae.services.eval.specs import EvaluatorSpec, SpecProvenance


def _spec(name: str, *, kind: str = "llm_judge", scope: str = "final_output") -> EvaluatorSpec:
    return EvaluatorSpec(
        name=name,
        kind=kind,
        scope=scope,
        rubric_md="Grade the answer." if kind == "llm_judge" else "",
        config={"check": "regex", "pattern": "x"} if kind == "deterministic" else {},
        provenance=SpecProvenance(source="test", generator="test@v1", surface_area="trajectory"),
    )


def test_compile_graph_mints_no_gate_nodes():
    specs = {
        "format_check": _spec("format_check", kind="deterministic"),
        "helpfulness": _spec("helpfulness"),
    }
    graph = compile_graph(specs)

    kinds = {n.evaluator_name: n.kind for n in graph.nodes}
    assert kinds["format_check"] == "score"
    assert kinds["helpfulness"] == "score"
    assert kinds[GROUNDING_NODE] == "grounding"
    assert any(n.kind == "fold" for n in graph.nodes)
    assert not any(n.kind == "gate" for n in graph.nodes)

    assert ("format_check", FOLD_NODE) in graph.edges
    assert ("helpfulness", FOLD_NODE) in graph.edges
    assert all(dst == FOLD_NODE for _, dst in graph.edges)

    stages = graph.stages()
    assert [n.id for n in stages[-1]] == [FOLD_NODE]


def test_cyclic_graph_rejected():
    nodes = [GraphNode(id="a", kind="score"), GraphNode(id="b", kind="score")]
    with pytest.raises(ValueError, match="cycle"):
        Graph(nodes=nodes, edges=[("a", "b"), ("b", "a")])


def test_edge_to_unknown_node_rejected():
    with pytest.raises(ValueError, match="unknown node"):
        Graph(nodes=[GraphNode(id="a", kind="score")], edges=[("a", "ghost")])
