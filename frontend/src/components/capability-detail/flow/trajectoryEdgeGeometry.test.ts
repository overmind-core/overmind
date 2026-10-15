import { type EdgeProps, Position } from "@xyflow/react";
import { describe, expect, it } from "vitest";

import { trajectoryEdgeGeometry } from "./trajectoryEdgeGeometry";

// Cached routes can contain consecutive collinear points. Moving either card
// must discard those old lanes and label coordinates, including on retry edges.
describe("trajectory edges after moving cards", () => {
  const route = {
    crossings: [{ x: 500, y: 40 }],
    label: { x: 900, y: -400 },
    points: [
      { x: 100, y: 100 },
      { x: 140, y: 100 },
      { x: 180, y: 100 },
      { x: 180, y: 300 },
      { x: 400, y: 300 },
    ],
  };
  it.each([
    [Position.Right, Position.Left],
    [Position.Bottom, Position.Top],
    [Position.Right, Position.Bottom],
    [Position.Right, Position.Top],
  ])("rebuilds %s to %s routes from both live handles", (sourcePosition, targetPosition) => {
    const props = {
      sourcePosition,
      sourceX: 320,
      sourceY: 200,
      targetPosition,
      targetX: 100,
      targetY: 600,
    } as EdgeProps;
    const geometry = trajectoryEdgeGeometry(props, route);
    expect(geometry.path).toMatch(/^M320 200/);
    expect(geometry.path).toMatch(/L100 600$/);
    expect(geometry.path).not.toContain("C");
    expect(geometry.label.x).toBeGreaterThanOrEqual(0);
    expect(geometry.label.x).toBeLessThan(500);
    expect(geometry.label.y).toBeGreaterThanOrEqual(200);
    expect(geometry.label.y).toBeLessThanOrEqual(650);
    expect(geometry.path).not.toContain("900");
  });

  it("preserves the original layout and label until an endpoint changes", () => {
    const props = { sourceX: 100, sourceY: 100, targetX: 400, targetY: 300 } as EdgeProps;
    const geometry = trajectoryEdgeGeometry(props, route);
    expect(geometry.label).toEqual({ x: 948, y: -386 });
    expect(geometry.path).not.toContain("C");
    const moved = trajectoryEdgeGeometry({ ...props, targetX: 450, targetY: 350 }, route);
    expect(moved.path).toMatch(/L450 350$/);
    expect(moved.label).not.toEqual(geometry.label);
  });

  // A cached shared return corridor must not leave tool edges dependent on
  // other branches or paint crossing masks over their connections.
  it("routes each tool return independently even before a card moves", () => {
    const props = {
      data: { groupId: "tools", relation: "continuation" },
      id: "return:tools->results",
      source: "tools",
      sourcePosition: Position.Bottom,
      sourceX: 100,
      sourceY: 100,
      target: "results",
      targetPosition: Position.Bottom,
      targetX: 400,
      targetY: 300,
    } as EdgeProps;
    const geometry = trajectoryEdgeGeometry(props, route);
    expect(geometry).toEqual(trajectoryEdgeGeometry(props));
    expect(geometry.path).toMatch(/^M100 100/);
    expect(geometry.path).toMatch(/L400 300$/);
  });
});
