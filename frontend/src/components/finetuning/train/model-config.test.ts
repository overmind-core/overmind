import { describe, expect, it } from "vitest";

import type { FinetuningEvidence, FinetuningExperiment, FinetuningSkillScore } from "@/openapi";
import {
  applyCandidateGrade,
  confidenceLabel,
  draftFromRecommendation,
  formatScore,
  gradeFromCandidate,
  isLabClaimedOnly,
  type ModelDraft,
  matchStanding,
  ordinal,
} from "./model-config";

const evidence = (
  benchmark: string,
  provenance: FinetuningEvidence["provenance"]
): FinetuningEvidence => ({
  benchmark,
  cohortN: 450,
  percentile: 88.2,
  provenance,
  skill: "Instruction Following",
  source: "Artificial Analysis",
  url: "https://artificialanalysis.ai/evaluations/ifbench",
});

const SKILL_SCORES: FinetuningSkillScore[] = [
  {
    fieldN: 30,
    percentileGlobal: 88.2,
    percentileInField: 98.3,
    rankInField: 1,
    skill: "Instruction Following",
    weight: 0.5,
  },
];

const candidate = (over: Partial<FinetuningExperiment> = {}): FinetuningExperiment =>
  ({
    confidence: "high",
    evidence: [evidence("IFBench", "measured")],
    grade: 78,
    match: 98,
    matchPool: 30,
    matchRank: 1,
    model: "Qwen/Qwen3.5-9B",
    nBenchmarks: 12,
    skillScores: SKILL_SCORES,
    ...over,
  }) as FinetuningExperiment;

describe("formatScore", () => {
  it("renders two decimals so a total adds up against its parts", () => {
    expect(formatScore(78)).toBe("78.00");
    expect(formatScore(77.649)).toBe("77.65");
  });

  it("marks an ungraded model rather than inventing a number", () => {
    expect(formatScore(null)).toBe("—");
    expect(formatScore(0)).toBe("0.00");
  });
});

describe("ordinal", () => {
  it("suffixes the ordinal", () => {
    expect(ordinal(88.2)).toBe("88th");
    expect(ordinal(1)).toBe("1st");
    expect(ordinal(22)).toBe("22nd");
    expect(ordinal(63)).toBe("63rd");
  });

  it("keeps the teens on th", () => {
    expect(["11th", "12th", "13th"]).toEqual([11, 12, 13].map(ordinal));
    expect(ordinal(111)).toBe("111th");
  });
});

describe("matchStanding", () => {
  it("names the population the match is a standing within", () => {
    expect(matchStanding(1, 30)).toBe("1st of 30 models you can train");
    expect(matchStanding(12, 30)).toBe("12th of 30 models you can train");
  });

  it("keeps the population singular at one model", () => {
    expect(matchStanding(1, 1)).toBe("1st of 1 model you can train");
  });

  it("says nothing when the model is unranked or the pool is empty", () => {
    expect(matchStanding(null, 30)).toBeNull();
    expect(matchStanding(1, 0)).toBeNull();
  });
});

describe("confidenceLabel", () => {
  it("names the band the evidence supports", () => {
    expect(confidenceLabel("high", 12)).toBe("high confidence");
    expect(confidenceLabel("low", 1)).toBe("low confidence");
  });

  it("says not graded when there is no data", () => {
    expect(confidenceLabel("none", 0)).toBe("not graded");
    expect(confidenceLabel("medium", 0)).toBe("not graded");
  });
});

describe("isLabClaimedOnly", () => {
  it("is true only when every row is self-reported", () => {
    expect(isLabClaimedOnly([evidence("a", "lab_claimed")])).toBe(true);
    expect(isLabClaimedOnly([evidence("a", "lab_claimed"), evidence("b", "measured")])).toBe(false);
    expect(isLabClaimedOnly([])).toBe(false);
  });
});

describe("gradeFromCandidate", () => {
  it("carries the standing off the candidate row", () => {
    expect(gradeFromCandidate(candidate())).toEqual({
      confidence: "high",
      evidence: [evidence("IFBench", "measured")],
      grade: 78,
      labClaimedOnly: false,
      match: 98,
      matchPool: 30,
      matchRank: 1,
      nBenchmarks: 12,
      skillScores: SKILL_SCORES,
    });
  });

  it("reads ungraded while keeping the pool it was measured against", () => {
    const row = gradeFromCandidate(
      candidate({
        confidence: "none",
        evidence: [],
        grade: null,
        match: null,
        matchRank: null,
        nBenchmarks: 0,
        skillScores: [],
      })
    );
    expect([row.match, row.matchRank, row.grade]).toEqual([null, null, null]);
    expect(row.matchPool).toBe(30);
    expect(row.nBenchmarks).toBe(0);
  });
});

describe("applyCandidateGrade", () => {
  const draft = { ...gradeFromCandidate(candidate()), model: "other" } as ModelDraft;

  it("swaps in the standing of the model now on the draft", () => {
    const next = applyCandidateGrade(
      draft,
      candidate({ match: 62, matchRank: 11, nBenchmarks: 2 })
    );
    expect([next.match, next.matchRank, next.matchPool]).toEqual([62, 11, 30]);
    expect(next.nBenchmarks).toBe(2);
    expect(next.model).toBe("other");
  });

  it("clears the previous model's standing when the new one has none", () => {
    const next = applyCandidateGrade(draft, undefined);
    expect([next.match, next.matchRank, next.matchPool]).toEqual([null, null, 0]);
    expect(next.grade).toBeNull();
    expect(next.confidence).toBe("none");
    expect(next.evidence).toEqual([]);
  });

  it("switches off Full when the dataset's rows only fit LoRA's context", () => {
    const withFullOn = { ...draft, supportsFull: true, supportsLora: true, useLora: false };
    const next = applyCandidateGrade(
      withFullOn,
      candidate({
        trainingType: {
          full: { contextLength: 4096, enabled: false, validatedContextLength: true },
          lora: { contextLength: 32768, enabled: true, validatedContextLength: true },
        },
      })
    );
    expect(next.supportsFull).toBe(false);
    expect(next.supportsLora).toBe(true);
    expect(next.useLora).toBe(true);
  });
});

describe("draftFromRecommendation", () => {
  it("only offers the training kinds this dataset's rows fit, not the raw catalog", () => {
    const draft = draftFromRecommendation(
      candidate({
        trainingType: {
          full: { contextLength: 4096, enabled: false, validatedContextLength: true },
          lora: { contextLength: 32768, enabled: true, validatedContextLength: true },
        },
      }),
      undefined
    );
    expect(draft.supportsLora).toBe(true);
    expect(draft.supportsFull).toBe(false);
    expect(draft.useLora).toBe(true);
  });
});
