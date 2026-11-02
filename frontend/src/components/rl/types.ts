// Shapes of the JSON fields on RLRun / RLRunDetail; the generated client types them as `any`.

export interface CheckOutcome {
  outcome: "scored" | "abstained" | "error";
  score: number | null;
  explanation: string;
}

export interface CheckStats {
  mean: number | null;
  scored: number;
  abstained: number;
  errors: number;
}

export interface StageMetrics {
  pass_rate: number;
  mean_reward: number;
  count: number;
  per_check: Record<string, CheckStats>;
}

export interface Attempt {
  text: string;
  reward: number;
  passed: boolean;
  checks: Record<string, CheckOutcome>;
}

export interface RLStepRecord {
  step: number;
  metrics: Record<string, number>;
  batch: Partial<StageMetrics>;
  group: { prompt?: string; attempts?: Attempt[] };
}

export interface SampleSide {
  output: string;
  reward: number;
  passed: boolean;
  checks: Record<string, CheckOutcome>;
}

export interface HeldOutSample {
  index: number;
  prompt: string;
  baseline?: SampleSide;
  final?: SampleSide;
}

export interface RewardConfig {
  key: string;
  check: string;
  params: Record<string, unknown>;
  bind: Record<string, string>;
  attributes: Record<string, string>;
  expected: string;
  weight: number;
}

export interface Verdict {
  decision: "promote" | "review" | "keep_current";
  reason: string;
}

export interface RewardCheckEntry {
  check: string;
  description: string;
  parameters: Record<string, string>;
}

export interface RLModelEntry {
  id: string;
  label: string;
  parameters: string;
}

export interface HyperparameterRange {
  default: number;
  min: number;
  max: number;
}
