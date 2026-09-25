import { Badge } from "@/components/ui/badge";
import { ResolvedStatusBadge, resolveStatus } from "@/lib/job-status";
import { humanizeKey } from "@/lib/label-case";
import type { ModeCd6Enum, OptimizerExperiment } from "@/openapi";

// Keys mirror overbae/models/optimizer.py OptimizerExperiment.Status.
export const EXPERIMENT_STATUS_LABELS: Record<string, string> = {
  baseline: "Running baseline",
  cancelled: "Cancelled",
  completed: "Completed",
  evaluated_baseline_outputs: "Baseline evaluated",
  evaluated_candidate_outputs: "Candidates evaluated",
  evaluating_baseline_outputs: "Evaluating baseline",
  evaluating_candidate_outputs: "Evaluating candidates",
  failed: "Failed",
  iterating: "Iterating",
  paused: "Paused",
  scheduled: "Scheduled",
};

export function prettyStatus(status: string): string {
  return EXPERIMENT_STATUS_LABELS[status] ?? humanizeKey(status);
}

const OPTIMIZER_RUN_TYPE_META: Record<
  ModeCd6Enum,
  {
    description: string;
    label: string;
    variant: "info" | "secondary";
  }
> = {
  hybrid: {
    description: "Tests prompt and code changes with each selected model.",
    label: "Code + model optimisation",
    variant: "info",
  },
  model_comparison: {
    description: "Compares the current model with selected models on the same dataset.",
    label: "Model comparison",
    variant: "info",
  },
  optimize: {
    description: "Varies prompts, tools, and code across iterations.",
    label: "Prompt & code optimisation",
    variant: "secondary",
  },
};

export function optimizerRunType(experiment: OptimizerExperiment): ModeCd6Enum {
  return experiment.mode === "model_comparison" || experiment.mode === "hybrid"
    ? experiment.mode
    : "optimize";
}

export function optimizerRunTypeMeta(type: ModeCd6Enum) {
  return OPTIMIZER_RUN_TYPE_META[type];
}

export function optimizerModelIds(experiment: OptimizerExperiment): string[] {
  return Array.isArray(experiment.modelIds)
    ? experiment.modelIds.filter((modelId): modelId is string => typeof modelId === "string")
    : [];
}

export function OptimizerRunTypeBadge({
  experiment,
  showModelCount = false,
  size = "default",
}: {
  experiment: OptimizerExperiment;
  showModelCount?: boolean;
  size?: "chip" | "default";
}) {
  const type = optimizerRunType(experiment);
  const meta = optimizerRunTypeMeta(type);
  const modelCount = optimizerModelIds(experiment).length;

  return (
    <Badge
      aria-label={modelCount > 0 ? `${meta.label}, ${modelCount} selected models` : meta.label}
      className="max-w-full"
      size={size}
      title={meta.description}
      variant={meta.variant}
    >
      <span className="truncate">{meta.label}</span>
      {showModelCount && modelCount > 0 && (
        <span className="ml-1 shrink-0 font-mono text-xs font-normal opacity-80">
          · {modelCount} model{modelCount === 1 ? "" : "s"}
        </span>
      )}
    </Badge>
  );
}

/** Tone comes from the shared job-status registry; only the label stays
 * optimiser-specific. */
export function ExperimentStatusChip({
  status,
  progress,
}: {
  status: string;
  progress?: number | null;
}) {
  const key =
    status === "completed" || status === "failed" || status === "cancelled"
      ? status
      : status === "scheduled"
        ? "queued"
        : status === "paused"
          ? "paused"
          : "running";
  const cfg = resolveStatus(key, "queued");
  return <ResolvedStatusBadge cfg={{ ...cfg, label: prettyStatus(status) }} progress={progress} />;
}

const TERMINAL = new Set(["completed", "failed", "cancelled"]);

export function isExperimentLive(status: string): boolean {
  return !TERMINAL.has(status) && !status.startsWith("failed") && status !== "paused";
}

export function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Scores are 0–100. */
export function experimentScores(experiment: OptimizerExperiment): {
  baseline: number | null;
  best: number | null;
  delta: number | null;
} {
  const scores = (experiment.scores ?? {}) as Record<string, unknown>;
  const baseline = asNumber(scores.baseline);
  const best = asNumber(scores.best);
  return {
    baseline,
    best,
    delta: baseline != null && best != null ? best - baseline : null,
  };
}
