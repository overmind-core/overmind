import { describe, expect, it } from "vitest";

import type { TrajectoryGraph, TrajectoryNode } from "./buildTrajectoryGraph";
import { projectTrajectory, searchTrajectory } from "./trajectoryVisibility";

const nodes = ["entry", "left", "right", "shared", "hidden"].map((id) => ({
  data: {
    anchors: [],
    contract: null,
    kind: "step",
    label: id === "hidden" ? "Read dropdown options" : id,
    mayUse: [],
  },
  id,
  position: { x: 0, y: 0 },
})) as unknown as TrajectoryNode[];
const edges = [
  ["entry", "left"],
  ["entry", "right"],
  ["left", "shared"],
  ["right", "shared"],
  ["left", "hidden"],
].map(([source, target]) => ({ id: `${source}->${target}`, source, target }));
const graph: TrajectoryGraph = { edges, nodes, relayout: async (nodes) => nodes };

describe("trajectory navigation", () => {
  it("keeps shared descendants reachable through an open path while hiding a collapsed branch", () => {
    const visible = projectTrajectory(graph, new Set(["left"]));
    expect(visible.nodes.map((node) => node.id)).toContain("shared");
    expect(visible.nodes.map((node) => node.id)).not.toContain("hidden");
    expect(visible.edges.some((edge) => edge.source === "left")).toBe(false);
  });
  it("searches hidden nodes, reveals their incoming path and restores collapsed branches when cleared", () => {
    const matches = searchTrajectory(graph, "dropdown");
    expect(matches.map((match) => match.id)).toEqual(["hidden"]);
    const collapsed = new Set(["entry", "left"]);
    const visible = projectTrajectory(graph, collapsed, matches[0].id);
    expect(visible.nodes.map((node) => node.id)).toEqual(["entry", "left", "hidden"]);
    expect(projectTrajectory(graph, collapsed).nodes.map((node) => node.id)).toEqual(["entry"]);
  });
  it("highlights every incoming route to a shared match and tolerates cycles", () => {
    const cyclic = {
      ...graph,
      edges: [...edges, { id: "shared->left", source: "shared", target: "left" }],
    };
    const result = projectTrajectory(cyclic, new Set(["entry", "left", "right"]), "shared");
    expect(result.edges.map((edge) => edge.id)).toContain("left->shared");
    expect(result.edges.map((edge) => edge.id)).toContain("right->shared");
    expect(searchTrajectory(graph, "missing")).toEqual([]);
  });
});

it("keeps a collapsed branch connected to its known continuation without exposing its children", () => {
  const loop = {
    ...graph,
    edges: [
      ...edges,
      { data: { relation: "continuation" }, id: "return", source: "hidden", target: "entry" },
    ],
  };
  const result = projectTrajectory(loop, new Set(["left"]));
  expect(result.nodes.some((node) => node.id === "hidden")).toBe(false);
  expect(
    result.edges.some(
      (edge) =>
        edge.source === "left" && edge.target === "entry" && edge.data?.relation === "continuation"
    )
  ).toBe(true);
  expect(
    result.edges.find((edge) => edge.source === "left" && edge.target === "entry")?.sourceHandle
  ).toBeUndefined();
});

it("finds model cards by their visible provider header and model identity", () => {
  const modelGraph = {
    ...graph,
    nodes: [
      {
        ...nodes[0],
        data: {
          ...nodes[0].data,
          actor: "model",
          kind: "step",
          label: "Choose the next action",
          model: "gpt-4.1",
        },
      },
    ],
  } as TrajectoryGraph;
  for (const query of ["OpenAI", "gpt-4.1", "Model call", "Choose the next action"]) {
    expect(searchTrajectory(modelGraph, query).map((match) => match.id)).toEqual(["entry"]);
  }
});
