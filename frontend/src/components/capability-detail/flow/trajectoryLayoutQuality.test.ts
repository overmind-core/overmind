import { describe, expect, it } from "vitest";

import type { CapabilityFlow } from "@/openapi";
import browser from "./__fixtures__/browser-use-topology.json";
import hermes from "./__fixtures__/hermes-agent-topology.json";
import { buildTrajectoryGraph, measureTrajectoryLayout } from "./buildTrajectoryGraph";
import { projectTrajectory } from "./trajectoryVisibility";

// Source topologies from the 2026-10-13 scans, without prompts/configuration.
// Before this change the expanded fixtures had 129/6 distinct wire crossings
// (Hermes/Browser Use), and Hermes had edges crossing unrelated cards.
describe.each([
  { crossingLimit: 60, flow: hermes, lengthLimit: 2_300_000, name: "Hermes" },
  { crossingLimit: 5, flow: browser, lengthLimit: 448_785, name: "Browser Use" },
])("$name layout geometry", ({ name, flow, crossingLimit, lengthLimit }) => {
  it.each([
    "collapsed",
    "expanded",
    "partial",
    "varied heights",
  ])("keeps %s paths clear", async (state) => {
    const graph = buildTrajectoryGraph(flow as unknown as CapabilityFlow, name);
    const groups = graph.nodes.filter((n) => n.data.kind === "actionGroup");
    const collapsed = new Set(
      (state === "expanded" || state === "varied heights"
        ? []
        : state === "partial"
          ? groups.slice(1)
          : groups
      ).map((n) => n.id)
    );
    const projected = projectTrajectory(graph, collapsed);
    const laid = await projected.relayout(
      projected.nodes.map((n, i) => ({
        ...n,
        measured: {
          height:
            (state === "varied heights" ? (i % 3) * 37 : 0) +
            (n.data.kind === "actionGroup"
              ? 72
              : n.data.kind === "step"
                ? 450
                : n.data.kind === "action"
                  ? 340
                  : n.data.kind === "outcome"
                    ? 220
                    : 170),
          width: 320,
        },
      }))
    );
    const quality = measureTrajectoryLayout(laid, projected.edges);
    if (state === "collapsed") {
      const steps = laid.filter((n) => n.data.kind === "step");
      const top = Math.min(...steps.map((n) => n.position.y));
      expect(top).toBeLessThan(300);
      for (const edge of projected.edges) {
        if (edge.data?.relation === "continuation") continue;
        const source = laid.find((n) => n.id === edge.source)!;
        const target = laid.find((n) => n.id === edge.target)!;
        if (
          source.data.kind === "step" &&
          target.data.kind === "step" &&
          target.position.x > source.position.x &&
          target.position.x - source.position.x < 900
        )
          expect(target.position.x - source.position.x - 320).toBeLessThan(280);
      }
    }
    expect(quality.collisions).toBe(0);
    expect(Number.isFinite(quality.width * quality.height)).toBe(true);
    if (state === "expanded") {
      expect(quality.crossings).toBeLessThanOrEqual(crossingLimit);
      expect(quality.length).toBeLessThanOrEqual(lengthLimit);
    }
    expect(laid.map((n) => n.id)).toEqual(projected.nodes.map((n) => n.id));
    for (const edge of projected.edges)
      expect(laid.find((n) => n.id === edge.source)?.data.routes?.[edge.id]).toBeDefined();
  });
});
