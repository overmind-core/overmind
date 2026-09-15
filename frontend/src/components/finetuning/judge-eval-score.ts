import type { FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";

/** `aggregate_score` is already this mean; averaging is only the fallback. */
export function meanMetricScore(row: FinetuningJudgeEvalRow): number | null {
  if (row.aggregate_score != null) return row.aggregate_score;
  const metrics = row.metric_scores ?? [];
  if (metrics.length === 0) return null;
  return metrics.reduce((sum, m) => sum + m.score, 0) / metrics.length;
}
