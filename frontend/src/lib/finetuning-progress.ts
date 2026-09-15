/** The loosely-typed ``progress`` JSON on FinetuningJob. The live training
    metrics and download/stage fields are Baseten-only. */
export type FinetuningProgress = {
  epochs_completed?: number | null;
  tokens_processed?: number | null;
  trained_steps?: number | null;
  total_steps?: number | null;
  /** Unix seconds. */
  estimated_finish?: number | null;
  phase?: string | null;
  provider_status?: string | null;
  train_loss?: number | null;
  eval_loss?: number | null;
  learning_rate?: number | null;
  token_accuracy?: number | null;
  eval_token_accuracy?: number | null;
  /** Fractional: 0.5 = halfway through epoch 1. */
  current_epoch?: number | null;
  eta_s?: number | null;
  metrics_history?: Array<{
    step: number;
    epoch?: number;
    train_loss?: number;
    token_accuracy?: number;
    lr?: number;
  }> | null;
  eval_history?: Array<{
    step: number;
    epoch?: number;
    eval_loss?: number;
    eval_token_accuracy?: number;
  }> | null;
  /** Filtered provider log lines; `ts` in epoch ms. */
  activity?: Array<{ ts?: number | null; message: string }> | null;
  /** Pre-training sub-stage: "downloading_base_model" → "loading_model" →
      "model_loaded". */
  stage?: string | null;
  download?: {
    pct?: number | null;
    downloaded_gb?: number | null;
    total_gb?: number | null;
  } | null;
  /** Stamped by the loss-curves endpoint; absent on old jobs → derive it. */
  lifecycle_stage?: LifecycleStage | string | null;
  judge_evals?: Array<{ kind?: string; status?: string }> | null;
};

type LifecycleStage =
  | "setup"
  | "training"
  | "deployment"
  | "evaluation"
  | "completed"
  | "failed"
  | "cancelled";

const PHASE_LABELS: Record<string, string> = {
  cancelled: "Cancelled",
  completed: "Completed",
  deploying: "Deploying",
  failed: "Failed",
  // Provider-side training is already done — never surface "Finalising".
  finalizing: "Completed",
  processing_dataset: "Queued",
  queued: "Queued",
  running: "Running",
  submitted: "Queued",
  succeeded: "Succeeded",
  training: "Training",
  validating_files: "Queued",
};

export function parseFinetuningProgress(raw: unknown): FinetuningProgress | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  return raw as FinetuningProgress;
}

export function progressPhaseLabel(
  progress: FinetuningProgress | null | undefined,
  status?: string | null
): string {
  const phase = progress?.phase || status || "";
  if (!phase) return "In progress";
  return PHASE_LABELS[phase] ?? phase.replace(/_/g, " ");
}

export function downloadStatusLine(progress: FinetuningProgress | null | undefined): string | null {
  if (progress?.stage !== "downloading_base_model") return null;
  const d = progress?.download;
  if (!d) return "Downloading base model…";
  const dl = d.downloaded_gb;
  const total = d.total_gb;
  if (typeof total === "number" && total > 0 && typeof dl === "number") {
    const pct = d.pct ?? Math.round((dl / total) * 100);
    return `Downloading base model — ${pct}% (${dl.toFixed(1)}/${total.toFixed(1)} GB)`;
  }
  if (typeof dl === "number") return `Downloading base model — ${dl.toFixed(1)} GB`;
  return "Downloading base model…";
}
