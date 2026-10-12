import { describe, expect, it } from "vitest";

import { elbowHeightPx } from "./elbow-list";

describe("elbowHeightPx", () => {
  it("keeps the shared start pad on the first row", () => {
    expect(elbowHeightPx(100, 100, 12)).toBe(12);
  });

  it("grows by the real distance between row centers", () => {
    // Expanded detail pushed the next header 48px further down.
    expect(elbowHeightPx(100, 100 + 32 + 48, 12)).toBe(92);
  });
});
