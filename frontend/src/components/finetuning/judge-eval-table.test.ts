import { describe, expect, it } from "vitest";

import type { FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import { meanMetricScore } from "./judge-eval-score";

const row = (partial: Partial<FinetuningJudgeEvalRow>): FinetuningJudgeEvalRow => ({
  id: "ev",
  kind: "final",
  status: "completed",
  ...partial,
});

describe("meanMetricScore", () => {
  it("prefers aggregate_score when set", () => {
    expect(
      meanMetricScore(
        row({
          aggregate_score: 0.5,
          metric_scores: [
            { name: "a", score: 1 },
            { name: "b", score: 0 },
          ],
        })
      )
    ).toBe(0.5);
  });

  it("averages metric_scores when aggregate is missing", () => {
    expect(
      meanMetricScore(
        row({
          metric_scores: [
            { name: "a", score: 0.8 },
            { name: "b", score: 0.4 },
          ],
        })
      )
    ).toBeCloseTo(0.6);
  });

  it("returns null with no scores", () => {
    expect(meanMetricScore(row({}))).toBeNull();
  });
});
