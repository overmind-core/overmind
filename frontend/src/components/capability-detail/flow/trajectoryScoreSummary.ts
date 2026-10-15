import { metricResults } from "@/components/evaluations/metric-list";
import type { EvalScore } from "@/openapi";

export interface TrajectoryScoreSummary {
  mean: number | null;
  scored: number;
  errors: number;
  abstained: number;
}

export function summarizeTrajectoryScores(receipts: EvalScore[]) {
  const latest = new Map<string, EvalScore>();
  for (const receipt of receipts) {
    if (receipt.sourceType !== "span") continue;
    const previous = latest.get(receipt.sourceId);
    if (!previous || receipt.id > previous.id) latest.set(receipt.sourceId, receipt);
  }
  const result = new Map<string, TrajectoryScoreSummary>();
  for (const receipt of latest.values()) {
    for (const [name, metric] of metricResults(receipt.metrics)) {
      const row = result.get(name) ?? { abstained: 0, errors: 0, mean: null, scored: 0 };
      if (metric.error) row.errors += 1;
      else if (metric.verdict === "abstain") row.abstained += 1;
      else if (typeof metric.score === "number" && Number.isFinite(metric.score)) {
        row.mean = ((row.mean ?? 0) * row.scored + metric.score) / (row.scored + 1);
        row.scored += 1;
      }
      result.set(name, row);
    }
  }
  return result;
}
