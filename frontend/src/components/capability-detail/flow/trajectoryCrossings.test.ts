import { describe, expect, it } from "vitest";

import { crossingPoints, roundedRoute } from "./trajectoryRouting";

describe("trajectory crossing bridges", () => {
  it("marks only the later edge at an interior crossing", () => {
    const routes = [
      {
        points: [
          { x: 0, y: 50 },
          { x: 100, y: 50 },
        ],
        source: "a",
        target: "b",
      },
      {
        points: [
          { x: 50, y: 0 },
          { x: 50, y: 100 },
        ],
        source: "c",
        target: "d",
      },
    ];
    const hits = crossingPoints(routes);
    expect(hits).toEqual([[], [{ x: 50, y: 50 }]]);
    const path = roundedRoute(routes[1].points, hits[1]);
    expect(path).toContain("C");
    expect(path).toMatch(/^M 50 0/);
    expect(path).toMatch(/L 50 100$/);
  });
  it("keeps real joins, shared trunks and corner touches connected", () => {
    expect(
      crossingPoints([
        {
          points: [
            { x: 0, y: 50 },
            { x: 100, y: 50 },
          ],
          source: "a",
          target: "b",
        },
        {
          points: [
            { x: 50, y: 0 },
            { x: 50, y: 100 },
          ],
          source: "a",
          target: "c",
        },
        {
          points: [
            { x: 100, y: 0 },
            { x: 100, y: 50 },
          ],
          source: "d",
          target: "e",
        },
        {
          points: [
            { x: 0, y: 50 },
            { x: 100, y: 50 },
          ],
          source: "a",
          target: "g",
        },
      ])
    ).toEqual([[], [], [], []]);
  });
});
