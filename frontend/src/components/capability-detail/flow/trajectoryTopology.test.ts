import { describe, expect, it } from "vitest";

import type { CapabilityFlow } from "@/openapi";
import { buildTrajectoryGraph } from "./buildTrajectoryGraph";
import { CONDITION_HEIGHT, CONDITION_WIDTH } from "./trajectoryRouting";
import { projectTrajectory } from "./trajectoryVisibility";

// A real branch, a retry and an alternate runtime must not become one serial chain.
const flow = {
  trajectoryMap: [
    {
      claim: "decision_surface",
      divergences: [],
      id: "turn",
      name: "Turn",
      provenance: [],
      routing: "",
      steps: [
        {
          anchors: ["route"],
          step: "Route",
          transitions: [
            { condition: "Standard provider", target: "model" },
            { condition: "External runtime", target: "external" },
          ],
        },
        {
          anchors: ["model"],
          kind: "model_invocation",
          step: "Model",
          transitions: [
            { condition: "Tool calls returned", target: "tools" },
            { condition: "Text returned", target: "answer" },
            { condition: "Retry this request", target: "model" },
          ],
        },
        {
          anchors: ["tools"],
          step: "Tools",
          transitions: [
            { condition: "Results recorded; another iteration is permitted", target: "model" },
          ],
        },
        {
          anchors: ["answer"],
          step: "Answer",
          transitions: [{ condition: "Response accepted", target: "terminal" }],
        },
        {
          anchors: ["external"],
          step: "External",
          transitions: [{ condition: "Runtime returned", target: "terminal" }],
        },
      ].map((s) => ({ kind: "agent_step", mayUse: [], ...s })),
      terminal: { description: "Result", kind: "emits_record" },
      tools: [],
    },
  ],
} as unknown as CapabilityFlow;

describe("source-directed trajectory layout", () => {
  it("keeps alternatives near their decision and prevents retries from stretching the sequence", async () => {
    const graph = buildTrajectoryGraph(flow, "Agent");
    const withReturns = await graph.relayout(graph.nodes);
    const forwardOnly = await graph.relayout(
      graph.nodes,
      graph.edges.filter((e) => e.data?.relation !== "continuation")
    );
    const x = (name: string) =>
      withReturns.find((n) => n.data.kind === "step" && n.data.label === name)!.position.x;
    expect(x("Tools")).toBe(x("Answer"));
    for (const node of withReturns)
      expect(
        Math.abs(node.position.x - forwardOnly.find((n) => n.id === node.id)!.position.x)
      ).toBeLessThan(64);
    expect(projectTrajectory(graph, new Set()).edges.some((e) => e.source === e.target)).toBe(true);
  });
  it("keeps connections on their measured handles when evaluations expand below a card", async () => {
    const graph = buildTrajectoryGraph(flow, "Agent");
    const nodes = await graph.relayout(
      graph.nodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          ...(n.id === "entry" ? { layoutPorts: { out: { x: 320, y: 100 } } } : {}),
        },
        measured: { height: n.id === "entry" ? 600 : 310, width: 320 },
      }))
    );
    const entry = nodes.find((n) => n.id === "entry")!;
    const edge = graph.edges.find((e) => e.source === "entry")!;
    expect(entry.data.routes![edge.id].points[0]).toEqual({
      x: entry.position.x + 320,
      y: entry.position.y + 100,
    });
  });
  it("attaches planned routes to handles outside the card boundary", async () => {
    const graph = buildTrajectoryGraph(flow, "Agent");
    const nodes = await graph.relayout(
      graph.nodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          layoutPorts: {
            in: { x: -3, y: 155 },
            out: { x: 323, y: 155 },
            retry: { x: 160, y: -3 },
          },
        },
        measured: { height: 310, width: 320 },
      }))
    );
    for (const edge of graph.edges) {
      const source = nodes.find((n) => n.id === edge.source)!;
      const target = nodes.find((n) => n.id === edge.target)!;
      const points = source.data.routes![edge.id].points;
      expect(points[0]).toEqual({ x: source.position.x + 323, y: source.position.y + 155 });
      expect(points.at(-1)).toEqual(
        edge.targetHandle === "retry"
          ? { x: target.position.x + 160, y: target.position.y - 3 }
          : { x: target.position.x - 3, y: target.position.y + 155 }
      );
      for (let i = 1; i < points.length; i++)
        expect(points[i].x === points[i - 1].x || points[i].y === points[i - 1].y).toBe(true);
    }
  });
  it("uses declared transitions instead of adding fictitious sequential paths", () => {
    const graph = buildTrajectoryGraph(flow, "Agent");
    const names = new Map(
      graph.nodes.map((n) => [n.id, n.data.kind === "step" ? n.data.label : n.data.kind])
    );
    const links = graph.edges.map((e) => `${names.get(e.source)} → ${names.get(e.target)}`);
    expect(links).toContain("Model → Answer");
    expect(links).toContain("Route → External");
    expect(links).not.toContain("Tools → Answer");
    expect(links).not.toContain("Answer → External");
    const visible = projectTrajectory(graph, new Set());
    expect(visible.nodes).toHaveLength(graph.nodes.length);
  });

  it("reserves space for condition labels and routes every backbone edge with real ELK", async () => {
    const graph = buildTrajectoryGraph(flow, "Agent");
    const nodes = await graph.relayout(
      graph.nodes.map((n) => ({ ...n, measured: { height: 310, width: 320 } }))
    );
    const boxes = nodes.map((n) => ({
      h: 310,
      id: n.id,
      w: 320,
      x: n.position.x,
      y: n.position.y,
    }));
    const labels: typeof boxes = [];
    for (const edge of graph.edges) {
      if (edge.data?.relation !== "continuation") {
        expect(nodes.find((n) => n.id === edge.source)!.position.x, edge.id).toBeLessThan(
          nodes.find((n) => n.id === edge.target)!.position.x
        );
      }
      const route = nodes.find((n) => n.id === edge.source)?.data.routes?.[edge.id];
      expect(route?.points.length, edge.id).toBeGreaterThanOrEqual(2);
      if (edge.label) {
        expect(route?.label, edge.id).toBeDefined();
        labels.push({ ...route!.label!, h: CONDITION_HEIGHT, id: edge.id, w: CONDITION_WIDTH });
        const center = {
          x: route!.label!.x + CONDITION_WIDTH / 2,
          y: route!.label!.y + CONDITION_HEIGHT / 2,
        };
        expect(
          route!.points.some((p, i, points) => {
            const next = points[i + 1];
            if (!next) return false;
            return (
              (p.y === next.y &&
                Math.abs(center.y - p.y) <= 2 &&
                center.x >= Math.min(p.x, next.x) &&
                center.x <= Math.max(p.x, next.x)) ||
              (p.x === next.x &&
                Math.abs(center.x - p.x) <= 2 &&
                center.y >= Math.min(p.y, next.y) &&
                center.y <= Math.max(p.y, next.y))
            );
          }),
          `Label must sit on its edge: ${edge.id}`
        ).toBe(true);
      }
    }
    const overlaps = (a: (typeof boxes)[number], b: (typeof boxes)[number]) =>
      a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
    for (const label of labels) {
      expect(
        boxes.filter((b) => overlaps(label, b)),
        label.id
      ).toEqual([]);
      expect(
        labels.filter((b) => b !== label && overlaps(label, b)),
        label.id
      ).toEqual([]);
    }
  });
});
