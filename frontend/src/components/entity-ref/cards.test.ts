import { describe, expect, it } from "vitest";

import { pickJudgeHeadline } from "@/components/entity-ref/cards";

describe("pickJudgeHeadline", () => {
  it("prefers final over latest checkpoint", () => {
    const picked = pickJudgeHeadline([
      { aggregate_score: 0.5, kind: "baseline" },
      { aggregate_score: 0.7, kind: "checkpoint" },
      { aggregate_score: 0.85, baseline_delta: 0.1, kind: "final" },
    ]);
    expect(picked?.kind).toBe("final");
    expect(picked?.aggregate_score).toBe(0.85);
  });

  it("falls back to latest non-baseline scored", () => {
    const picked = pickJudgeHeadline([
      { aggregate_score: 0.4, kind: "baseline" },
      { aggregate_score: 0.6, kind: "checkpoint" },
      { aggregate_score: 0.72, kind: "checkpoint" },
    ]);
    expect(picked?.aggregate_score).toBe(0.72);
  });
});
