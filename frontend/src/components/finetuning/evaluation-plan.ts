import type { ExperimentSnapshot } from "@/components/finetuning/job-snapshot";
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

export function groupEvaluationDisplayRows(snapshots: ExperimentSnapshot[]) {
  const groups = new Map<string, { row: EvaluationDisplayRow; snapshots: ExperimentSnapshot[] }>();
  for (const snapshot of snapshots) {
    for (const row of evaluationDisplayRows(snapshot.job, snapshot.judgeEvals)) {
      // Matching model names alone do not establish identical data or grading context.
      const key = row.eval_run_id
        ? `${row.kind}:${row.eval_run_id}`
        : `${snapshot.job.id}:${row.id}`;
      const group = groups.get(key);
      if (!group) {
        groups.set(key, { row, snapshots: [snapshot] });
        continue;
      }
      const current = group.row;
      const preferIncoming =
        current.status === "cancelled"
          ? row.status !== "cancelled"
          : row.status !== "cancelled" && (row.updated_at ?? "") > (current.updated_at ?? "");
      group.row = {
        ...(preferIncoming ? row : current),
        // Deltas belong to each experiment's comparison, not the shared run.
        baseline_delta: null,
        comparison_label: null,
        created_at:
          current.created_at && row.created_at
            ? current.created_at < row.created_at
              ? current.created_at
              : row.created_at
            : (current.created_at ?? row.created_at),
      };
      group.snapshots.push(snapshot);
    }
  }
  return [...groups.values()].sort(
    (a, b) =>
      evaluationKindRank(a.row.kind) - evaluationKindRank(b.row.kind) ||
      (a.row.checkpoint_step ?? 0) - (b.row.checkpoint_step ?? 0)
  );
}
