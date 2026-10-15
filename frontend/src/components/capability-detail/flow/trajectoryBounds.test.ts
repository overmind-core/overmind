import { expect, it } from "vitest";

import type { TrajectoryNode } from "./buildTrajectoryGraph";
import { trajectoryBounds, trajectoryOverviewBounds } from "./trajectoryBounds";

it("includes return lanes and condition labels outside the node rectangles in the overview", () => {
  const nodes = [
    {
      data: {
        kind: "step",
        routes: {
          retry: {
            label: { x: 550, y: -150 },
            points: [
              { x: 520, y: 250 },
              { x: 600, y: -100 },
              { x: 360, y: 100 },
            ],
          },
        },
      },
      id: "model",
      measured: { height: 300, width: 320 },
      position: { x: 200, y: 100 },
      width: 320,
    },
  ] as unknown as TrajectoryNode[];
  const bounds = trajectoryOverviewBounds(nodes);
  expect(bounds.x).toBe(200);
  expect(bounds.y).toBe(-150);
  expect(bounds.x + bounds.width).toBeGreaterThanOrEqual(646);
  expect(bounds.y + bounds.height).toBe(400);
});

it("keeps search framing on the selected cards rather than their distant outgoing routes", () => {
  const nodes = [
    {
      data: {
        kind: "step",
        routes: {
          next: {
            points: [
              { x: 420, y: 250 },
              { x: 4000, y: 2000 },
            ],
          },
        },
      },
      id: "selected",
      measured: { height: 300, width: 320 },
      position: { x: 100, y: 100 },
      width: 320,
    },
  ] as unknown as TrajectoryNode[];
  expect(trajectoryBounds(nodes)).toEqual({ height: 300, width: 320, x: 100, y: 100 });
});
