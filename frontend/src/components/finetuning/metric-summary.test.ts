import { describe, expect, it } from "vitest";

import { summarizeMetric } from "./metric-summary";

describe("recorded metric summaries", () => {
  it("uses true step order and ignores invalid values without dropping zero", () => {
    expect(
      summarizeMetric(
        [
          { step: 30, value: 0.4 },
          { step: 10, value: 0.8 },
          { step: 40, value: Number.NaN },
          { step: Number.POSITIVE_INFINITY, value: 7 },
          { step: 20, value: 0 },
        ],
        "loss"
      )
    ).toEqual({ count: 3, first: 0.8, latest: 0.4, reference: 0, third: -0.4 });
  });

  it("retains a short gradient spike in the peak and weights the average by observations", () => {
    const points = Array.from({ length: 1001 }, (_, step) => ({
      step,
      value: step === 500 ? 1001 : 0,
    }));
    expect(summarizeMetric(points, "gradient")).toEqual({
      count: 1001,
      first: 0,
      latest: 0,
      reference: 1001,
      third: 1,
    });
  });

  it("keeps missing history unmeasured and does not infer change from one measurement", () => {
    expect(summarizeMetric([], "loss")).toBeNull();
    expect(summarizeMetric([{ step: 0, value: 0 }], "loss")?.third).toBeNull();
  });

  it("reports accuracy change in percentage points even when the first score was zero", () => {
    expect(
      summarizeMetric(
        [
          { step: 0, value: 0 },
          { step: 10, value: 75 },
          { step: 20, value: 50 },
        ],
        "accuracy"
      )
    ).toEqual({ count: 3, first: 0, latest: 50, reference: 75, third: 50 });
  });

  it("distinguishes the initial learning rate, peak and last recorded rate", () => {
    expect(
      summarizeMetric(
        [
          { step: 0, value: 0 },
          { step: 10, value: 0.0002 },
          { step: 20, value: 0.00001 },
        ],
        "learningRate"
      )
    ).toEqual({ count: 3, first: 0, latest: 0.00001, reference: 0.0002, third: 0 });
  });
});
