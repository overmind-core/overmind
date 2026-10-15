import { describe, expect, it } from "vitest";

import type { EvalScore } from "@/openapi";
import { summarizeTrajectoryScores } from "./trajectoryScoreSummary";

const receipt = (id: number, sourceId: string, metrics: unknown): EvalScore => ({
  createdAt: new Date(id * 1000),
  id,
  metrics,
  project: "p",
  run: null,
  score: null,
  sourceId,
  sourceType: "span",
});

describe("trajectory runtime scores", () => {
  it("keeps judge errors and abstentions out of the numerical average", () => {
    const result = summarizeTrajectoryScores([
      receipt(1, "a", { rule: { score: 0 } }),
      receipt(2, "b", { rule: { score: 1 } }),
      receipt(3, "c", { rule: { error: "judge unavailable", score: 0 } }),
      receipt(4, "d", { rule: { score: null, verdict: "abstain" } }),
    ]);
    expect(result.get("rule")).toMatchObject({ abstained: 1, errors: 1, mean: 0.5, scored: 2 });
    expect(result.has("unexecuted tool")).toBe(false);
  });

  it("uses the newest receipt per span and excludes trajectory aggregates", () => {
    const result = summarizeTrajectoryScores([
      receipt(1, "a", { rule: { score: 0 } }),
      receipt(2, "a", { rule: { score: 1 } }),
      { ...receipt(3, "trace", { rule: { score: 0 } }), sourceType: "trajectory" },
    ]);
    expect(result.get("rule")).toMatchObject({ mean: 1, scored: 1 });
  });
});
