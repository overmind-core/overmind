import { describe, expect, it } from "vitest";

import {
  evalHeadlineScore,
  evalProgressCounts,
  evalProgressLabel,
  pickJudgeHeadline,
} from "@/components/entity-ref/cards";
import type { ComparisonSummary } from "@/components/evaluations/run-comparison";

describe("evalProgressCounts", () => {
  it("uses prepared/total while generating", () => {
    expect(
      evalProgressCounts({
        phase: "generating",
        prepared: 3,
        scored: 153,
        scoreTotal: 162,
        total: 27,
      })
    ).toEqual({ done: 3, total: 27 });
  });

  it("uses scored/scoreTotal while scoring", () => {
    expect(
      evalProgressCounts({
        phase: "scoring",
        prepared: 27,
        scored: 153,
        scoreTotal: 162,
        total: 27,
      })
    ).toEqual({ done: 153, total: 162 });
  });

  it("returns null when denominator is zero", () => {
    expect(evalProgressCounts({ phase: "generating", prepared: 0, total: 0 })).toBeNull();
    expect(
      evalProgressCounts({ phase: "scoring", scored: 0, scoreTotal: 0, total: 27 })
    ).toBeNull();
  });
});

describe("evalProgressLabel", () => {
  it("labels generating and scoring with done/total", () => {
    expect(
      evalProgressLabel({ phase: "generating", prepared: 3, scored: 0, scoreTotal: 40, total: 10 })
    ).toBe("Generating 3/10");
    expect(
      evalProgressLabel({ phase: "scoring", prepared: 10, scored: 12, scoreTotal: 40, total: 10 })
    ).toBe("Scoring 12/40");
  });

  it("labels scoring with scoreTotal, not sample total", () => {
    expect(
      evalProgressLabel({ phase: "scoring", prepared: 27, scored: 153, scoreTotal: 162, total: 27 })
    ).toBe("Scoring 153/162");
  });

  it("shows percent when aggregating", () => {
    expect(evalProgressLabel({ phase: "aggregating", scored: 40, scoreTotal: 40 })).toBe("100%");
  });
});

describe("evalHeadlineScore", () => {
  it("picks overall mean via scorePct from a minimal summary", () => {
    const summary: ComparisonSummary = {
      metrics: ["accuracy"],
      variants: {
        default: {
          label: "default",
          metrics: { accuracy: { mean: 0.823, n: 10, pass_rate: 0.9 } },
        },
      },
    };
    expect(evalHeadlineScore(summary)).toEqual({ label: "Score", pct: 82 });
  });

  it("falls back to pass rate when mean is absent", () => {
    const summary: ComparisonSummary = {
      metrics: ["pass"],
      variants: {
        default: {
          label: "default",
          metrics: { pass: { mean: null, n: 10, pass_rate: 0.75 } },
        },
      },
    };
    expect(evalHeadlineScore(summary)).toEqual({ label: "Pass rate", pct: 75 });
  });

  it("omits when summary is empty", () => {
    expect(evalHeadlineScore(null)).toBeNull();
    expect(evalHeadlineScore({})).toBeNull();
  });
});

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
