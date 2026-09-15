export interface ScoreLike {
  id?: string;
  name?: string | null;
  value?: number | null;
  passed?: boolean | null;
  reasoning?: string | null;
  scope?: string | null;
}

export type ScoreState = "passed" | "failed" | "ungraded" | "scored";

// A null value is an abstention, not a 0% fail.
export function scoreState(score: ScoreLike): ScoreState {
  if (score.value == null) return "ungraded";
  if (score.passed === true) return "passed";
  if (score.passed === false) return "failed";
  return "scored";
}
