import { describe, expect, it } from "vitest";

import { bucketPoints, type MetricPoint, strideFor } from "./loss-chart";

const series = (n: number): MetricPoint[] =>
  Array.from({ length: n }, (_, i) => ({ step: i + 1, value: i + 1 }));

describe("strideFor", () => {
  it("plots short series at full density", () => {
    expect(strideFor(150)).toBe(1);
    expect(strideFor(500)).toBe(1);
  });

  it("picks clean 1/2/5×10^k strides for long series", () => {
    expect(strideFor(1300)).toBe(5); // ceil(1300/500)=3 → 5
    expect(strideFor(501)).toBe(2); // ceil(501/500)=2 → 2
    expect(strideFor(59112)).toBe(200); // ceil(59112/500)=119 → 200
  });
});

describe("bucketPoints", () => {
  it("returns the series untouched at stride 1", () => {
    const pts = series(50);
    expect(bucketPoints(pts, 1)).toBe(pts);
  });

  it("averages step windows and plots at the bucket's last true step", () => {
    const out = bucketPoints(series(1000), 10);
    expect(out.length).toBe(100);
    // First bucket: steps 1–10, mean 5.5, plotted at true step 10.
    expect(out[0]).toEqual({ step: 10, value: 5.5 });
    expect(out[out.length - 1].step).toBe(1000);
  });

  it("keeps the render count bounded for huge runs", () => {
    const n = 59112;
    const out = bucketPoints(series(n), strideFor(n));
    expect(out.length).toBeLessThanOrEqual(500);
    expect(out[out.length - 1].step).toBe(n);
  });
});
