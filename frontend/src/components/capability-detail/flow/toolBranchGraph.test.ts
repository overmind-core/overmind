import { describe, expect, it } from "vitest";

import type { CapabilityFlow, CapabilityFlowTool } from "@/openapi";
import { buildTrajectoryGraph, collectAncestors } from "./buildTrajectoryGraph";
import { CONDITION_HEIGHT, CONDITION_WIDTH, outcomeRoute } from "./trajectoryRouting";

const tool: CapabilityFlowTool = {
  args: "index",
  branches: [
    {
      action: "Read options",
      basis: "code",
      condition: "Element is a select",
      next: "Decide again",
      outcome: "Choices",
      provenance: ["tools.py#L20-L30"],
      transitions: [{ condition: "Results return", target: "Agent.decide" }],
    },
    {
      action: "Report changed context",
      basis: "code",
      condition: "Click opens a new tab",
      next: "Stop queued actions and observe again",
      outcome: "New tab",
      provenance: ["tools.py#L31-L40"],
    },
  ],
  name: "click",
  purpose: "Activate an element",
};
const flow = (option = tool): CapabilityFlow =>
  ({
    toolSpec: [option],
    trajectoryMap: [
      {
        claim: "decision_surface",
        divergences: [],
        id: "run",
        name: "Run",
        provenance: [],
        routing: "Request received",
        steps: [
          {
            anchors: ["Agent.decide"],
            kind: "model_invocation",
            mayUse: [],
            step: "Choose action",
          },
        ],
        terminal: { description: "Answer", kind: "emits_record" },
        tools: [],
      },
    ],
  }) as unknown as CapabilityFlow;

describe("tool branches within the trajectory", () => {
  it("keeps the entry, decision, tools and expanded outcomes in one connected graph", () => {
    const graph = buildTrajectoryGraph(flow(), "Agent", [], "");
    const outcomes = graph.nodes.filter((node) => node.data.kind === "outcome");
    expect(
      outcomes.map((node) => (node.data.kind === "outcome" ? node.data.branch : null))
    ).toEqual(tool.branches);
    expect(
      graph.edges
        .filter((edge) => outcomes.some((node) => node.id === edge.target))
        .map((edge) => edge.label)
    ).toEqual(tool.branches?.map((branch) => branch.condition));
    for (const outcome of outcomes) {
      const upstream = collectAncestors(graph, outcome.id).nodeIds;
      expect(upstream.has("entry")).toBe(true);
      expect(upstream.has("step:run:0")).toBe(true);
      expect(upstream.has("tool:step:run:0:click")).toBe(true);
    }
    expect(graph.nodes.some((node) => node.data.kind === "terminal")).toBe(true);
  });
  it("does not infer a run ending from a tool name without branch evidence", () => {
    const graph = buildTrajectoryGraph(
      flow({ args: "", name: "done", purpose: "Finish" }),
      "Agent",
      [],
      ""
    );
    expect(graph.nodes.find((node) => node.data.kind === "outcome")?.data).toMatchObject({
      branch: null,
    });
  });
  it("binds an action evaluation once, rather than also calling it unmapped", () => {
    const graph = buildTrajectoryGraph(flow(), "Agent", [
      { enabled: true, id: "check", spanName: "click", type: "llm" } as never,
    ]);
    expect(graph.nodes.find((node) => node.data.kind === "action")?.data).toMatchObject({
      evaluations: [{ id: "check" }],
    });
    expect(graph.nodes.some((node) => node.data.kind === "evaluations")).toBe(false);
  });
});

describe("continuation edges", () => {
  it("connects a recorded tool return without treating it as an incoming search route", () => {
    const graph = buildTrajectoryGraph(flow(), "Agent");
    const result = graph.edges.find((edge) => edge.data?.relation === "continuation");
    expect(result).toMatchObject({
      source: "outcome:tool:step:run:0:click:0",
      target: "step:run:0",
    });
    expect(
      collectAncestors(graph, "step:run:0").nodeIds.has("outcome:tool:step:run:0:click:0")
    ).toBe(false);
  });
  it("does not invent a destination from continuation prose", () => {
    const graph = buildTrajectoryGraph(
      flow({ ...tool, branches: tool.branches?.map(({ transitions, ...branch }) => branch) }),
      "Agent"
    );
    expect(graph.edges.filter((edge) => edge.data?.relation === "continuation")).toHaveLength(0);
  });
});

it("distinguishes model selection from code conditions and prompt guidance inside a tool", () => {
  const graph = buildTrajectoryGraph(
    flow({ ...tool, branches: [tool.branches![0], { ...tool.branches![1], basis: "prompt" }] }),
    "Agent"
  );
  expect(graph.nodes.find((node) => node.data.kind === "action")?.data).toMatchObject({
    modelSelected: true,
  });
  const branches = graph.edges.filter((edge) => edge.target.startsWith("outcome:"));
  expect(branches.map((edge) => edge.data?.basis)).toEqual(["code", "prompt"]);
  expect(branches[0].style?.strokeDasharray).toBeUndefined();
  expect(branches[1].style?.strokeDasharray).toBeTruthy();
});

it("lays expanded categories, tools and outcomes out as separate non-overlapping nodes", async () => {
  const graph = buildTrajectoryGraph(flow(), "Agent");
  const laid = await graph.relayout(
    graph.nodes.map((node) => ({ ...node, measured: { height: 200, width: 320 } }))
  );
  const nodes = laid.filter((node) =>
    ["actionGroup", "action", "outcome"].includes(node.data.kind)
  );
  for (const node of nodes) {
    expect(node.parentId).toBeUndefined();
    for (const other of nodes) {
      if (node === other) continue;
      const width = node.measured!.width!;
      const height = node.measured!.height!;
      const otherWidth = other.measured!.width!;
      const otherHeight = other.measured!.height!;
      const overlaps =
        node.position.x < other.position.x + otherWidth &&
        node.position.x + width > other.position.x &&
        node.position.y < other.position.y + otherHeight &&
        node.position.y + height > other.position.y;
      expect(overlaps).toBe(false);
    }
  }
});

// The live journeys do not measure SVG routes against variable-height cards.
// Regressions here are crossed conditions, covered outcomes, and crowded neighbours.
it("aligns sibling outcomes with direct incoming routes and room for neighbouring groups", async () => {
  const graph = buildTrajectoryGraph(
    {
      ...flow(),
      toolSpec: [
        { ...tool, branches: [...tool.branches!, ...tool.branches!], cluster: "Interaction" },
        { ...tool, cluster: "Interaction", name: "type" },
        { ...tool, cluster: "Navigation", name: "navigate" },
      ],
    },
    "Agent"
  );
  const laid = await graph.relayout(
    graph.nodes.map((node, index) => ({
      ...node,
      measured: { height: 180 + (index % 3) * 90, width: 320 },
    }))
  );
  const boxes = laid.map((node) => ({
    bottom: node.position.y + node.measured!.height!,
    id: node.id,
    left: node.position.x,
    right: node.position.x + node.measured!.width!,
    top: node.position.y,
  }));
  for (const action of laid.filter((node) => node.data.kind === "action")) {
    const outcomes = laid.filter(
      (node) => node.data.kind === "outcome" && node.data.actionId === action.id
    );
    for (const [index, outcome] of outcomes.entries()) {
      if (index) {
        expect(outcome.position.x).toBeGreaterThan(outcomes[index - 1].position.x);
        expect(outcome.position.y).toBe(outcomes[0].position.y);
      } else {
        expect(outcome.position.x).toBe(action.position.x);
      }
      const route = outcomeRoute({
        source: { x: action.position.x + 160, y: action.position.y + action.measured!.height! },
        target: { x: outcome.position.x + 160, y: outcome.position.y },
      });
      const length = route
        .slice(1)
        .reduce(
          (total, point, i) =>
            total + Math.abs(point.x - route[i].x) + Math.abs(point.y - route[i].y),
          0
        );
      const first = route[0],
        last = route[route.length - 1];
      expect(length).toBe(Math.abs(last.x - first.x) + Math.abs(last.y - first.y));
      if (index === 0) expect(route.every((point) => point.x === first.x)).toBe(true);
      const labels = outcomes
        .filter((node) => node !== outcome)
        .map((node) => ({
          bottom: node.position.y - 28,
          id: node.id,
          left: node.position.x + 160 - CONDITION_WIDTH / 2,
          right: node.position.x + 160 + CONDITION_WIDTH / 2,
          top: node.position.y - CONDITION_HEIGHT - 28,
        }));
      for (let segment = 1; segment < route.length; segment++) {
        const a = route[segment - 1],
          b = route[segment];
        for (const box of [...boxes, ...labels]) {
          if (box.id === action.id || box.id === outcome.id) continue;
          const crosses =
            a.x === b.x
              ? a.x > box.left &&
                a.x < box.right &&
                Math.max(a.y, b.y) > box.top &&
                Math.min(a.y, b.y) < box.bottom
              : a.y > box.top &&
                a.y < box.bottom &&
                Math.max(a.x, b.x) > box.left &&
                Math.min(a.x, b.x) < box.right;
          expect(crosses, `Route to ${outcome.id} crosses ${box.id}`).toBe(false);
        }
      }
    }
  }
  const groups = laid.filter((node) => node.data.kind === "actionGroup");
  const nextGroup = groups[1];
  const descendants = laid.filter(
    (node) => node.data.kind === "action" && node.data.groupId === groups[0].id
  );
  const firstLane = laid.filter(
    (node) =>
      descendants.includes(node) ||
      (node.data.kind === "outcome" &&
        descendants.some(
          (action) => node.data.kind === "outcome" && node.data.actionId === action.id
        ))
  );
  // A neighbouring family may be beside or below this family; neither may
  // occupy the other's cards. Do not prescribe one global horizontal strip.
  for (const node of firstLane) {
    const separated =
      nextGroup.position.x >= node.position.x + node.measured!.width! ||
      nextGroup.position.x + nextGroup.measured!.width! <= node.position.x ||
      nextGroup.position.y >= node.position.y + node.measured!.height! ||
      nextGroup.position.y + nextGroup.measured!.height! <= node.position.y;
    expect(separated).toBe(true);
  }
});
