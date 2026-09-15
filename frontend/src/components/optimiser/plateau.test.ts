import { describe, expect, it } from "vitest";

import type { OptimizerExperiment } from "@/openapi";
import { isCrossExperimentPlateau } from "./plateau";

function makeExp(best: number | null, offset: number): OptimizerExperiment {
  return {
    createdAt: new Date(1_000_000 + offset * 1000).toISOString(),
    id: String(offset),
    scores: best != null ? { best } : null,
    status: "completed",
  } as unknown as OptimizerExperiment;
}

describe("isCrossExperimentPlateau", () => {
  it("returns false with fewer than 2 completed scored experiments", () => {
    expect(isCrossExperimentPlateau([])).toBe(false);
    expect(isCrossExperimentPlateau([makeExp(50, 0)])).toBe(false);
  });

  it("returns false when a later experiment beats the first", () => {
    expect(isCrossExperimentPlateau([makeExp(40, 0), makeExp(50, 1)])).toBe(false);
  });

  it("returns true when the second score equals the first", () => {
    expect(isCrossExperimentPlateau([makeExp(50, 0), makeExp(50, 1)])).toBe(true);
  });

  it("returns true when the second score is worse than the first", () => {
    expect(isCrossExperimentPlateau([makeExp(50, 0), makeExp(40, 1)])).toBe(true);
  });

  it("returns false when a middle experiment sets a new high", () => {
    expect(isCrossExperimentPlateau([makeExp(40, 0), makeExp(55, 1), makeExp(48, 2)])).toBe(false);
  });

  it("ignores experiments without a numeric best score", () => {
    expect(isCrossExperimentPlateau([makeExp(50, 0), makeExp(null, 1)])).toBe(false);
  });

  it("ignores non-completed experiments", () => {
    const running = { ...makeExp(30, 1), status: "running" } as unknown as OptimizerExperiment;
    expect(isCrossExperimentPlateau([makeExp(50, 0), running])).toBe(false);
  });

  it("sorts chronologically when the API returns newest-first ISO timestamps", () => {
    expect(isCrossExperimentPlateau([makeExp(50, 1), makeExp(40, 0)])).toBe(false);
  });
});
