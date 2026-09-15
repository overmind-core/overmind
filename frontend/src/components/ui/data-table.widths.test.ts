import { describe, expect, it } from "vitest";

import { fillColumnWidths } from "./data-table";

const col = (id: string, size: number, canResize = true) => ({ canResize, id, size });
const total = (widths: Record<string, number>) =>
  Object.values(widths).reduce((sum, w) => sum + w, 0);

describe("fillColumnWidths", () => {
  it("leaves sizes untouched when the columns already overflow", () => {
    expect(fillColumnWidths([col("a", 200), col("b", 200)], 300)).toEqual({ a: 200, b: 200 });
  });

  it("is a no-op before the container has been measured", () => {
    expect(fillColumnWidths([col("a", 100), col("b", 100)], 0)).toEqual({ a: 100, b: 100 });
  });

  it("spends the leftover width instead of pooling it in a gutter", () => {
    const widths = fillColumnWidths([col("a", 100), col("b", 150), col("c", 50)], 600);
    expect(total(widths)).toBeGreaterThanOrEqual(597);
    expect(total(widths)).toBeLessThanOrEqual(600);
  });

  it("keeps the designed proportions — a wide column stays wide", () => {
    const widths = fillColumnWidths([col("narrow", 100), col("wide", 300)], 800);
    expect(widths.wide / widths.narrow).toBeCloseTo(3, 1);
  });

  it("holds fixed columns (checkbox, actions) at their designed width", () => {
    const widths = fillColumnWidths([col("select", 40, false), col("name", 100)], 440);
    expect(widths.select).toBe(40);
    expect(widths.name).toBe(400);
  });

  it("returns the designed sizes when nothing can grow", () => {
    const cols = [col("a", 100, false), col("b", 100, false)];
    expect(fillColumnWidths(cols, 900)).toEqual({ a: 100, b: 100 });
  });
});
