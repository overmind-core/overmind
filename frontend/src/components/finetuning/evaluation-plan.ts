import type { FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import type { FinetuningJobList } from "@/openapi";

export interface EvaluationDisplayRow extends FinetuningJudgeEvalRow {
  planned?: boolean;
  waitingReason?: string;
}

const EVALUATION_CHOICES = [
  { flag: "evalIncumbentBefore", kind: "baseline", label: "Incumbent · before" },
  { flag: "evalModelBefore", kind: "model_before", label: "Base model · before" },
  { flag: "evalIncumbentAfter", kind: "incumbent_after", label: "Incumbent · after" },
  { flag: "evalModelAfter", kind: "final", label: "Trained model · after" },
] as const;

export function evaluationKindRank(kind: string): number {
  return ["baseline", "model_before", "checkpoint", "incumbent_after", "final"].indexOf(kind);
}

export function evaluationDisplayRows(
  job: FinetuningJobList,
  actual: FinetuningJudgeEvalRow[]
): EvaluationDisplayRow[] {
  const rows: EvaluationDisplayRow[] = [...actual];
  if (!job.evalDataset || !job.evalSet) return rows;

  const stopped = job.status === "failed" || job.status === "cancelled";
  const trainingFinished = job.status === "deploying" || job.status === "succeeded";
  for (const choice of EVALUATION_CHOICES) {
    if (!job[choice.flag] || actual.some((row) => row.kind === choice.kind)) continue;
    const after = choice.kind === "final" || choice.kind === "incumbent_after";
    const baseBefore =
      choice.kind === "model_before" || (choice.kind === "baseline" && !job.capability);
    const waitingReason = stopped
      ? job.status === "cancelled"
        ? "Training cancelled"
        : "Training failed"
      : after && !trainingFinished
        ? "Waiting for training"
        : choice.kind === "final" && job.status === "deploying"
          ? "Waiting for deployment"
          : !after && job.status !== "queued"
            ? "Waiting for model"
            : "Waiting to start";

    rows.push({
      id: `planned-${choice.kind}`,
      kind: choice.kind,
      label: baseBefore ? "Base model · before" : choice.label,
      model_id: baseBefore || choice.kind === "final" ? job.baseModel : undefined,
      planned: true,
      status: stopped ? "skipped" : "pending",
      waitingReason,
    });
  }
  return rows.sort((a, b) => evaluationKindRank(a.kind) - evaluationKindRank(b.kind));
}

export function hasPendingEvaluations(job: FinetuningJobList, rows: FinetuningJudgeEvalRow[]) {
  return evaluationDisplayRows(job, rows).some((row) =>
    ["pending", "queued", "running"].includes(row.status)
  );
}
