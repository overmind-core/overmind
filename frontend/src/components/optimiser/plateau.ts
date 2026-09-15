import { asNumber } from "@/components/optimiser/experiment-status";
import type { OptimizerExperiment } from "@/openapi";

type ScoredExperiment = {
  at: number;
  best: number;
};

function createdAtMs(value: Date | string | undefined): number {
  const ms = value instanceof Date ? +value : Date.parse(String(value ?? ""));
  return Number.isFinite(ms) ? ms : 0;
}

/** Baseline is the first completed experiment: nothing after it may set a new best. */
export function isCrossExperimentPlateau(experiments: OptimizerExperiment[]): boolean {
  const completed = experiments
    .filter((e) => e.status === "completed")
    .map((e) => ({
      at: createdAtMs(e.createdAt),
      best: asNumber((e.scores as Record<string, unknown> | null)?.best),
    }))
    .filter((e): e is ScoredExperiment => e.best != null)
    .sort((a, b) => a.at - b.at);
  if (completed.length < 2) return false;
  let peak = completed[0].best;
  for (let i = 1; i < completed.length; i++) {
    if (completed[i].best > peak) return false;
    peak = Math.max(peak, completed[i].best);
  }
  return true;
}
