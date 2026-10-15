import { describe, expect, it } from "vitest";

import { renderedCrossings } from "./trajectoryRenderedCrossings";

// Cached layout crossings become false notches when native routing or dragging
// changes a path. Curves and shared joins must not be treated as wire crossings.
describe("crossings on rendered trajectory paths", () => {
  const horizontal = { id: "h", path: "M0 50L100 50", source: "a", target: "b" };
  const vertical = { id: "v", path: "M50 0L50 100", source: "c", target: "d" };

  it("bridges only the crossing edge and masks only the short bridge", () => {
    const result = renderedCrossings([horizontal, vertical]);
    expect(result.get("h")?.bridges).toEqual([]);
    expect(result.get("v")?.path).toContain("C");
    expect(result.get("v")?.path).toMatch(/L 50 100$/);
    expect(result.get("v")?.bridges).toEqual(["M 50 44 C 58 47 58 53 50 56"]);
  });

  it("removes the old notch when an edge moves away", () => {
    expect(
      renderedCrossings([horizontal, { ...vertical, path: "M150 0L150 100" }]).get("v")
    ).toMatchObject({ bridges: [], path: "M150 0 L150 100" });
  });

  it("keeps shared returns connected", () => {
    expect(renderedCrossings([horizontal, { ...vertical, target: "b" }]).get("v")?.bridges).toEqual(
      []
    );
  });

  it("does not invent crossings from the bounding box of a curved corner", () => {
    const curved = { ...vertical, path: "M0 0 L30 0 Q100 0 100 70 L100 100" };
    expect(renderedCrossings([horizontal, curved]).get("v")?.bridges).toEqual([]);
  });
});
