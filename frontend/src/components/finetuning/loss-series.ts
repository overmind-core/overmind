// Providers return the same curves under different field names — `epochs` or
// `steps`, `valid_loss` or `eval_loss` — hence the pairs below.

import type { MetricPoint } from "@/components/finetuning/loss-chart";
import type { FinetuningJudgeEvalRow, LossCurveData } from "@/hooks/use-finetuning";

export function emptyLossData(): LossCurveData {
  return { epochs: [], train_loss: [], valid_loss: [] };
}

export function buildTrainLossPoints(data: LossCurveData) {
  const steps = data.epochs?.length ? data.epochs : (data.steps ?? []);
  const evals = data.valid_loss?.length ? data.valid_loss : (data.eval_loss ?? []);
  return steps
    .map((step, i) => ({
      eval: evals[i] ?? null,
      step: step as number,
      train: data.train_loss?.[i] ?? null,
    }))
    .filter((p) => p.step != null && (p.train != null || p.eval != null));
}

export function buildEvalLossPoints(data: LossCurveData) {
  return buildTrainLossPoints(data)
    .filter((p) => p.eval != null)
    .map((p) => ({ step: p.step, value: p.eval as number }));
}

export function buildJudgeScorePoints(rows: FinetuningJudgeEvalRow[]) {
  const scored = rows.filter((r) => r.aggregate_score != null);
  const maxStep = Math.max(
    0,
    ...scored.map((r) => (typeof r.checkpoint_step === "number" ? r.checkpoint_step : 0))
  );
  return scored
    .map((r) => ({
      step:
        r.kind === "baseline"
          ? 0
          : typeof r.checkpoint_step === "number"
            ? r.checkpoint_step
            : r.kind === "final" || r.kind === "incumbent_after"
              ? maxStep + 1
              : 0,
      value: r.aggregate_score as number,
    }))
    .sort((a, b) => a.step - b.step);
}

export function liveSeriesPoints<T>(
  rows: T[] | null | undefined,
  x: (r: T) => number | null | undefined,
  y: (r: T) => number | null | undefined,
  scale = 1
): MetricPoint[] {
  return (rows ?? []).flatMap((r) => {
    const xv = x(r);
    const yv = y(r);
    return xv != null && yv != null ? [{ step: +xv.toFixed(2), value: yv * scale }] : [];
  });
}
