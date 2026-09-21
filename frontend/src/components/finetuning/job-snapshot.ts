// Progress reaches the UI from three sources at different freshness: the 3s
// loss-curves poll, the jobs-list `progress` blob, then wall-clock. Every `??`
// chain below is that precedence, resolved once so no component repeats it.

import type { MetricPoint } from "@/components/finetuning/loss-chart";
import {
  buildEvalLossPoints,
  buildJudgeScorePoints,
  buildTrainLossPoints,
  emptyLossData,
} from "@/components/finetuning/loss-series";
import { primaryNameWithoutBase } from "@/components/inference/model-display-name";
import { getModelProviderInfo } from "@/components/model-provider-chip";
import type {
  FinetuningCheckpointRow,
  FinetuningJudgeEvalRow,
  LossCurveData,
} from "@/hooks/use-finetuning";
import { parseFinetuningProgress } from "@/lib/finetuning-progress";
import type { FinetuningJobList } from "@/openapi";

export function isTerminalStatus(status: string): boolean {
  return ["succeeded", "failed", "cancelled"].includes(status);
}

export function jobElapsedSeconds(job: FinetuningJobList): number | null {
  const start = job.startedAt ?? job.createdAt;
  if (!start) return null;
  const end = isTerminalStatus(job.status as string) ? (job.completedAt ?? job.updatedAt) : null;
  const secs = Math.floor(((end?.getTime() ?? Date.now()) - start.getTime()) / 1000);
  return secs > 0 ? secs : null;
}

export interface ExperimentProgress {
  elapsed_seconds: number | null;
  eta_seconds: number | null;
  percent: number | null;
  tokens_processed: number | null;
  total_steps: number | null;
  trained_steps: number | null;
}

export interface ExperimentSnapshot {
  job: FinetuningJobList;
  color: string;
  liveProgress: ReturnType<typeof parseFinetuningProgress>;
  progress: ExperimentProgress;
  percent: number | null;
  terminal: boolean;
  trainPoints: ReturnType<typeof buildTrainLossPoints>;
  evalPoints: MetricPoint[];
  lrPoints: MetricPoint[];
  gradPoints: MetricPoint[];
  scorePoints: MetricPoint[];
  checkpoints: FinetuningCheckpointRow[];
  judgeEvals: FinetuningJudgeEvalRow[];
  currentLoss: number | null;
  baselineScore: number | null;
  latestScored: FinetuningJudgeEvalRow | null;
}

export function experimentLabel(job: FinetuningJobList): string {
  const info = getModelProviderInfo(job.baseModel);
  return info.modelLabel || job.baseModel;
}

export function buildExperimentSnapshot(
  job: FinetuningJobList,
  curves: LossCurveData | undefined,
  color: string
): ExperimentSnapshot {
  const progressFromJob = (job.progress ?? {}) as Record<string, unknown>;
  const live = curves?.progress;
  const progress: ExperimentProgress = {
    elapsed_seconds:
      live?.elapsed_seconds ??
      (progressFromJob.elapsed_seconds as number | null | undefined) ??
      jobElapsedSeconds(job),
    eta_seconds:
      live?.eta_seconds ?? (progressFromJob.eta_seconds as number | null | undefined) ?? null,
    percent: live?.percent ?? (progressFromJob.percent as number | null | undefined) ?? null,
    tokens_processed:
      live?.tokens_processed ??
      (progressFromJob.tokens_processed as number | null | undefined) ??
      null,
    total_steps:
      live?.total_steps ?? (progressFromJob.total_steps as number | null | undefined) ?? null,
    trained_steps:
      live?.trained_steps ??
      (progressFromJob.trained_steps as number | null | undefined) ??
      (progressFromJob.epochs_completed as number | null | undefined) ??
      null,
  };

  const lossData = curves ?? emptyLossData();
  const trainPoints = buildTrainLossPoints(lossData);
  const judgeEvals: FinetuningJudgeEvalRow[] =
    curves?.judge_evals ??
    (progressFromJob.judge_evals as FinetuningJudgeEvalRow[] | undefined) ??
    [];

  return {
    baselineScore:
      judgeEvals.find((e) => e.kind === "model_before" && e.aggregate_score != null)
        ?.aggregate_score ??
      judgeEvals.find((e) => e.kind === "baseline" && e.aggregate_score != null)?.aggregate_score ??
      judgeEvals.find((e) => e.kind === "incumbent_after" && e.aggregate_score != null)
        ?.aggregate_score ??
      null,
    checkpoints:
      curves?.checkpoints ??
      (progressFromJob.checkpoints as FinetuningCheckpointRow[] | undefined) ??
      [],
    color,
    currentLoss: [...trainPoints].reverse().find((p) => p.train != null)?.train ?? null,
    evalPoints: buildEvalLossPoints(lossData),
    gradPoints: (curves?.grad_norm ?? [])
      .filter((p): p is { step: number; value: number } => p.step != null && p.value != null)
      .map((p) => ({ step: p.step, value: p.value })),
    job,
    judgeEvals,
    latestScored:
      [...judgeEvals].reverse().find((e) => e.kind === "final" && e.aggregate_score != null) ??
      null,
    liveProgress:
      (live?.metrics_history?.length ?? 0) > 0 ||
      (live?.eval_history?.length ?? 0) > 0 ||
      (live?.activity?.length ?? 0) > 0
        ? parseFinetuningProgress(live)
        : parseFinetuningProgress(job.progress),
    lrPoints: (curves?.learning_rate ?? [])
      .filter((p): p is { step: number; value: number } => p.step != null && p.value != null)
      .map((p) => ({ step: p.step, value: p.value })),
    percent:
      live?.percent ??
      (progressFromJob.percent as number | null | undefined) ??
      (progress.trained_steps != null && progress.total_steps
        ? Math.min(100, (100 * progress.trained_steps) / progress.total_steps)
        : null),
    progress,
    scorePoints: buildJudgeScorePoints(judgeEvals),
    terminal: isTerminalStatus(job.status as string),
    trainPoints,
  };
}

export function stepsLabelFor(p: ExperimentProgress): string {
  if (p.trained_steps == null) return "—";
  return p.total_steps != null ? `${p.trained_steps} / ${p.total_steps}` : String(p.trained_steps);
}

/** Precedence matches StatusBadge's: live > failed > cancelled > succeeded. */
export function groupStatusOf(jobs: FinetuningJobList[]): string {
  const statuses = jobs.map((j) => j.status as string);
  const nonTerminal = statuses.filter((s) => !isTerminalStatus(s));
  if (nonTerminal.length > 0) return nonTerminal.includes("running") ? "running" : nonTerminal[0];
  if (statuses.includes("failed")) return "failed";
  if (statuses.every((s) => s === "cancelled")) return "cancelled";
  return "succeeded";
}

export function groupPercent(jobs: FinetuningJobList[]): number | null {
  let trained = 0;
  let total = 0;
  const percents: number[] = [];
  for (const j of jobs) {
    const p = (j.progress ?? {}) as Record<string, unknown>;
    if (
      typeof p.trained_steps === "number" &&
      typeof p.total_steps === "number" &&
      p.total_steps > 0
    ) {
      trained += p.trained_steps;
      total += p.total_steps;
    } else if (typeof p.percent === "number") {
      percents.push(p.percent);
    }
  }
  if (total > 0) return Math.min(100, (100 * trained) / total);
  return percents.length ? percents.reduce((a, b) => a + b, 0) / percents.length : null;
}

/** Stored job names carry the job's own base model; strip it so only the run
 *  name shared across the group remains. */
export function groupRunName(jobs: FinetuningJobList[]): string {
  if (jobs.length === 0) return "";
  const prefixes = jobs
    .map((j) => primaryNameWithoutBase(j.name, j.baseModel).trim())
    .filter((p) => p && p !== "Fine-tuned model");
  return prefixes[0] ?? "";
}
