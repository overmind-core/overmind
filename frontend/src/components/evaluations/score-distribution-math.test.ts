import { describe, expect, it } from "vitest";

import {
  boxStats,
  histogramBins,
  kdeCurve,
  quantileSorted,
} from "@/components/evaluations/score-distribution-math";

describe("histogramBins", () => {
  it("bins 0–1 scores; 1.0 lands in the last bin", () => {
    expect(histogramBins([0, 0.05, 0.55, 1, 1], 10)).toEqual([2, 0, 0, 0, 0, 1, 0, 0, 0, 2]);
  });

  it("clamps out-of-range legacy values instead of dropping them", () => {
    expect(histogramBins([-0.2, 1.4], 10)).toEqual([1, 0, 0, 0, 0, 0, 0, 0, 0, 1]);
  });
});

describe("quantileSorted / boxStats", () => {
  it("interpolates quartiles linearly", () => {
    expect(quantileSorted([0, 1], 0.5)).toBe(0.5);
    const stats = boxStats([0.2, 0.4, 0.6, 0.8])!;
    expect(stats.median).toBeCloseTo(0.5);
    expect(stats.q1).toBeCloseTo(0.35);
    expect(stats.q3).toBeCloseTo(0.65);
    expect(stats.min).toBe(0.2);
    expect(stats.max).toBe(0.8);
    expect(stats.n).toBe(4);
  });

  it("returns null for empty input", () => {
    expect(boxStats([])).toBeNull();
  });
});

describe("kdeCurve", () => {
  it("peaks near the data and normalizes to 1", () => {
    const curve = kdeCurve([0.5, 0.5, 0.5]);
    expect(Math.max(...curve)).toBe(1);
    const peakIndex = curve.indexOf(1);
    expect(peakIndex / (curve.length - 1)).toBeCloseTo(0.5, 1);
    expect(curve[0]).toBeLessThan(0.2);
  });

  it("handles a single value without a degenerate spike", () => {
    const curve = kdeCurve([1]);
    expect(curve.length).toBeGreaterThan(0);
    expect(curve[curve.length - 1]).toBe(1);
  });

  it("keeps bimodal 0/1 data bimodal instead of smearing into a slab", () => {
    const values = [...Array(12).fill(0), ...Array(13).fill(1)];
    const curve = kdeCurve(values);
    const mid = Math.floor(curve.length / 2);
    expect(curve[0]).toBeGreaterThan(0.8);
    expect(curve[curve.length - 1]).toBeGreaterThan(0.8);
    expect(curve[mid]).toBeLessThan(0.1);
  });

  it("keeps a repeated value as a tight bump, not a full-width wedge", () => {
    const curve = kdeCurve(Array(25).fill(0));
    expect(curve[0]).toBe(1);
    const at20 = curve[Math.round((curve.length - 1) * 0.2)];
    expect(at20).toBeLessThan(0.01);
  });

  it("keeps the peak AT the 1.0 boundary for mass piled near it (boundary reflection)", () => {
    const curve = kdeCurve([0.9, 0.95, 1, 1, 1]);
    expect(curve[curve.length - 1]).toBe(1);
    expect(curve[0]).toBeLessThan(0.01);
  });
});
