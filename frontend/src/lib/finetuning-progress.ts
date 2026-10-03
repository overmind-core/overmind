export type FinetuningProgress = {
  preparation?: {
    id?: string;
    state?: string;
    stage?: string;
    completed_rows?: number;
    total_rows?: number;
    reused_rows?: number;
    committed_shards?: number;
    updated_at?: number;
  };
  diagnostics?: {
    stage?: string;
    completed?: number;
    total?: number;
    unit?: string;
    heartbeat_at?: number;
    attempt?: number;
    restored_step?: number;
    checkpoint_step?: number;
    checkpoint_bytes?: number;
    checkpoint_at?: number;
    attempt_elapsed_seconds?: number;
    attempt_started_at?: number;
    overall_started_at?: number;
  };
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
    brier?: number;
    decisions?: number;
    hard_label_accuracy?: number;
    hard_label_decisions?: number;
    argmax_target_agreement?: number;
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

export function stageStatusLine(progress: FinetuningProgress | null | undefined): string | null {
  const prep = progress?.preparation;
  if (prep && ["queued", "starting", "running"].includes(prep.state ?? "")) {
    if (prep.completed_rows == null)
      return prep.state === "starting"
        ? "Exporting and transferring data"
        : "Waiting for preprocessing worker";
    return `Tokenizing · ${prep.completed_rows.toLocaleString()} / ${(prep.total_rows ?? 0).toLocaleString()} rows${prep.reused_rows ? ` · ${prep.reused_rows.toLocaleString()} reused` : ""}`;
  }
  const detail = progress?.diagnostics;
  const stage = detail?.stage || progress?.stage;
  if (!stage) return null;
  const labels: Record<string, string> = {
    checkpointing: "Saving checkpoint",
    initial_validation: "Initial validation",
    loading_model: "Loading model",
    materializing: "Selecting prepared rows",
    resuming: "Restoring checkpoint",
    tokenizing: "Tokenizing",
    training: "Training",
    transferring: "Transferring data",
    validation: "Validation",
    verifying_checkpoint: "Verifying checkpoint",
    waiting: "Waiting for worker",
  };
  const label = labels[stage] ?? stage.replaceAll("_", " ");
  if (detail?.completed == null) return label;
  const count = detail.completed.toLocaleString();
  return `${label} · ${count}${detail.total == null ? "" : ` / ${detail.total.toLocaleString()}`} ${detail.unit ?? ""}`.trim();
}
