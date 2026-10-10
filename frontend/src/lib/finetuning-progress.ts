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
    source_at?: number;
    stage_started_at?: number;
    last_progress_at?: number;
    files_completed?: number;
    files_total?: number;
    acknowledged_bytes?: number;
    measurement?: string;
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
  activity?: Array<{ ts?: number | null; kind?: string; message: string }> | null;
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

const STARTUP_LABELS: Record<string, string> = {
  building_training_dataset: "Building training dataset",
  building_validation_dataset: "Building validation dataset",
  configuring_adapters: "Configuring adapters",
  initializing_trainer: "Initialising trainer",
  initializing_training_runtime: "Initialising training runtime",
  loading_model: "Loading model",
  loading_training_dataset: "Loading training dataset",
  staging_training_files: "Staging training files",
  starting_optimizer: "Starting optimiser",
  verifying_training_tokenizer: "Verifying training tokenizer",
};

export function hasStageProgress(progress: FinetuningProgress | null | undefined): boolean {
  if (!progress) return false;
  const stage = progress.diagnostics?.stage || progress.stage;
  return progress.stage === "transferring" || (stage != null && stage in STARTUP_LABELS);
}

const PHASE_LABELS: Record<string, string> = {
  cancelled: "Cancelled",
  completed: "Completed",
  deploying: "Deploying",
  failed: "Failed",
  // Provider-side training is already done — never surface "Finalising".
  finalizing: "Completed",
  preparing: "Preparing training data",
  processing_dataset: "Queued",
  queued: "Queued",
  running: "Running",
  submission_unknown: "Reconciling training submission",
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
    if (prep.stage === "uploading")
      return `Uploading training data · ${(prep.completed_rows ?? prep.total_rows ?? 0).toLocaleString()} rows prepared`;
    if (prep.stage === "exporting") {
      const completed = prep.completed_rows ?? 0;
      const total = prep.total_rows;
      const ratio = total && total > 0 ? ` (${Math.floor((completed / total) * 100)}%)` : "";
      return `Exporting training rows · ${completed.toLocaleString()}${total ? ` / ${total.toLocaleString()}` : ""}${ratio}`;
    }
    if (prep.completed_rows == null)
      return prep.state === "starting"
        ? "Checking and exporting training data"
        : "Waiting for preprocessing worker";
    const total = prep.total_rows;
    const ratio =
      total && total > 0 ? ` (${Math.floor((prep.completed_rows / total) * 100)}%)` : "";
    return `Tokenizing · ${prep.completed_rows.toLocaleString()}${total ? ` / ${total.toLocaleString()}` : ""} rows${ratio}${prep.reused_rows ? ` · ${prep.reused_rows.toLocaleString()} reused` : ""}`;
  }
  const detail = progress?.diagnostics;
  const stage = detail?.stage || progress?.stage;
  if (!stage) return null;
  const labels: Record<string, string> = {
    ...STARTUP_LABELS,
    building_selections: "Building row selections",
    checking_training_configuration: "Checking training data and model",
    checkpointing: "Saving checkpoint",
    committing_training_data: "Saving training files",
    exporting_training_rows: "Splitting and exporting training rows",
    indexing_prepared_rows: "Indexing prepared rows",
    initial_validation: "Pre-training baseline evaluation",
    loading_model: "Loading model",
    materializing: "Selecting prepared rows",
    preparing_base_model: "Preparing base model weights",
    resuming: "Restoring checkpoint",
    selecting_prepared_rows: "Selecting prepared rows",
    starting_training_worker: "Starting training worker",
    submitting_training_job: "Submitting training job",
    tokenizing: "Tokenizing",
    training: "Training",
    training_data_ready: "Training files ready",
    transferring: "Transferring data",
    uploading_selections: "Uploading row selections",
    validating_training_files: "Validating training files",
    validation: "Validation",
    verifying_checkpoint: "Verifying checkpoint",
    verifying_prepared_data: "Verifying prepared data",
    waiting: "Waiting for worker",
    waiting_for_materialization: "Row selections uploaded · awaiting provider progress",
  };
  const label = labels[stage] ?? stage.replaceAll("_", " ");
  if (detail?.completed == null) return label;
  if (detail.unit === "bytes") {
    const count = `${transferBytes(detail.completed)}${detail.total == null ? "" : ` / ${transferBytes(detail.total)}`}`;
    const acknowledged = detail.measurement === "provider_acknowledged_files";
    return `${label} · ${count}${acknowledged ? ` acknowledged · ${detail.files_completed ?? 0} / ${detail.files_total ?? "—"} files` : ""}`;
  }
  const count = detail.completed.toLocaleString();
  return `${label} · ${count}${detail.total == null ? "" : ` / ${detail.total.toLocaleString()}`} ${detail.unit ?? ""}`.trim();
}

const STAGE_LABELS: Record<string, string> = {
  ...STARTUP_LABELS,
  checking_training_configuration: "Preparing training data",
  checkpointing: "Saving checkpoint",
  downloading_base_model: "Loading model",
  exporting_training_rows: "Preparing training data",
  initial_validation: "Validating base model",
  loading_model: "Loading model",
  preparing_base_model: "Preparing model",
  starting_training_worker: "Starting training",
  submitting_training_job: "Starting training",
  training: "Training",
  validating_training_files: "Validating files",
  validation: "Validating checkpoint",
  verifying_checkpoint: "Verifying checkpoint",
};

export function stageLabel(progress: FinetuningProgress | null | undefined): string | null {
  const preparation = progress?.preparation;
  if (preparation && ["queued", "starting", "running"].includes(preparation.state ?? "")) {
    return "Preparing training data";
  }
  if (progress?.stage === "transferring") return "Transferring data";
  const stage = progress?.diagnostics?.stage || progress?.stage;
  return stage ? (STAGE_LABELS[stage] ?? null) : null;
}

export function stageDetailLine(progress: FinetuningProgress | null | undefined): string | null {
  const line = downloadStatusLine(progress) ?? stageStatusLine(progress);
  if (progress?.stage === "transferring") return progress.diagnostics?.stage ? line : "";
  if (line?.includes(" · ")) return line.slice(line.indexOf(" · ") + 3);
  const stage = progress?.diagnostics?.stage || progress?.stage;
  const details: Record<string, string> = {
    building_training_dataset: "Validating and packing training rows",
    building_validation_dataset: "Validating and packing validation rows",
    checking_training_configuration: "Checking pinned data and model configuration",
    configuring_adapters: "Attaching trainable adapter weights",
    exporting_training_rows: "Splitting and exporting training rows",
    initial_validation: "Measuring starting model on development set",
    initializing_trainer: "Configuring the trainer and batch settings",
    initializing_training_runtime: "Loading training libraries",
    loading_model: "Base weights ready · loading onto training GPU",
    loading_training_dataset: "Reading the prepared training and validation data",
    preparing_base_model: "Checking cached weights and downloading missing files",
    staging_training_files: "Copying prepared files into the training worker",
    starting_optimizer: "Initialising the optimiser before the first training step",
    starting_training_worker: "Base model ready · waiting for training GPU",
    verifying_training_tokenizer: "Checking tokenizer identity against the prepared data",
  };
  return stage ? (details[stage] ?? line) : line;
}

function transferBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes.toLocaleString()} B`;
  if (bytes < 1024 * 1024) return `${Number((bytes / 1024).toFixed(1))} KiB`;
  if (bytes < 1024 * 1024 * 1024) return `${Number((bytes / (1024 * 1024)).toFixed(1))} MiB`;
  return `${Number((bytes / (1024 * 1024 * 1024)).toFixed(1))} GiB`;
}

export function stageDescription(progress: FinetuningProgress | null | undefined): string | null {
  if (["queued", "starting", "running"].includes(progress?.preparation?.state ?? "")) return null;
  const stage = progress?.diagnostics?.stage || progress?.stage;
  const descriptions: Record<string, string> = {
    initial_validation:
      "Measuring the starting model on the development set before training begins.",
    loading_model: "Base weights are ready. Loading them onto the training GPU.",
    preparing_base_model:
      "Checking cached weights and downloading missing files before GPU training starts.",
    starting_training_worker: "The base model is ready. Waiting for the training GPU to start.",
    transferring: "Transferring the prepared rows to the training provider.",
  };
  return stage ? (descriptions[stage] ?? null) : null;
}
