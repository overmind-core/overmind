// Query keys are shared with each vertical's own detail query where the shape
// matches, so opening the real page after a hover is already warm.

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { SOURCE_KIND_LABEL } from "@/components/datasets/badges";
import {
  EntityCardMissing,
  EntityCardShell,
  EntityCardSkeleton,
  Row,
} from "@/components/entity-ref/shell";
import { type ComparisonSummary, overallAggregate } from "@/components/evaluations/run-comparison";
import { userFacingJobError } from "@/components/finetuning/train/model-config";
import { CreditsAmount } from "@/components/ui/credits";
import { DateTime } from "@/components/ui/datetime";
import { DeltaChip, fromScore } from "@/components/ui/delta-chip";
import { INTENT_LABEL, intentOf } from "@/hooks/use-datasets";
import { jobCostUsd } from "@/lib/finetuning-cost";
import { StatusBadge } from "@/lib/job-status";
import { scorePct } from "@/lib/utils";

const TERMINAL = new Set(["cancelled", "completed", "failed", "partially_completed", "succeeded"]);

function livePoll(status: unknown) {
  return typeof status === "string" && TERMINAL.has(status) ? (false as const) : 5_000;
}

function pct(value: number | null | undefined) {
  return value == null ? null : `${Math.round(value * 100)}%`;
}

function isTerminal(status: unknown) {
  return typeof status === "string" && TERMINAL.has(status);
}

export type EvalProgressSnapshot = {
  phase?: string | null;
  prepared?: number;
  total?: number;
  scored?: number;
  scoreTotal?: number;
};

export function evalProgressCounts(
  progress: EvalProgressSnapshot | null
): { done: number; total: number } | null {
  if (!progress?.phase) return null;
  const phase = progress.phase;
  const isGenerating = phase === "generating" || phase === "pending";
  const done = isGenerating ? (progress.prepared ?? 0) : (progress.scored ?? 0);
  const total = isGenerating ? (progress.total ?? 0) : (progress.scoreTotal ?? 0);
  if (total <= 0) return null;
  return { done, total };
}

/** Mirrors the runs-table RunRowProgress labelling. */
export function evalProgressLabel(progress: EvalProgressSnapshot | null): string | null {
  if (!progress?.phase) return null;
  const phase = progress.phase;
  const counts = evalProgressCounts(progress);
  if (phase === "pending") return "Queued";
  if (phase === "generating" && counts) return `Generating ${counts.done}/${counts.total}`;
  if (phase === "scoring" && counts) return `Scoring ${counts.done}/${counts.total}`;
  if (phase === "aggregating") return "100%";
  return phase;
}

export function evalHeadlineScore(
  summary: ComparisonSummary | null | undefined
): { label: "Score" | "Pass rate"; pct: number } | null {
  if (!summary?.metrics?.length) return null;
  const agg = overallAggregate(summary);
  if (agg.mean != null) return { label: "Score", pct: scorePct(agg.mean) };
  if (agg.passRate != null) return { label: "Pass rate", pct: scorePct(agg.passRate) };
  return null;
}

type JudgeRow = {
  kind: string;
  aggregate_score?: number | null;
  baseline_delta?: number | null;
};

export function pickJudgeHeadline(rows: JudgeRow[]): JudgeRow | null {
  const final = rows.find((e) => e.kind === "final" && e.aggregate_score != null) ?? null;
  if (final) return final;
  return (
    [...rows].reverse().find((e) => e.kind !== "baseline" && e.aggregate_score != null) ?? null
  );
}

export function CapabilityCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.capabilities.capabilitiesRetrieve({ id }),
    queryKey: ["capability-detail", id],
  });
  if (isPending) return <EntityCardSkeleton kind="capability" name={name} />;
  if (!data) return <EntityCardMissing kind="capability" name={name} />;
  return (
    <EntityCardShell kind="capability" name={data.name || name}>
      {data.description ? (
        <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {data.description}
        </p>
      ) : null}
      <Row label="Model">{data.model}</Row>
      <Row label="Datapoints">{data.datasetSize?.toLocaleString()}</Row>
      <Row label="Source">{data.sourcePath}</Row>
    </EntityCardShell>
  );
}

export function DatasetCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.datasets.datasetsRetrieve({ id }),
    queryKey: ["dataset", id],
  });
  if (isPending) return <EntityCardSkeleton kind="dataset" name={name} />;
  if (!data) return <EntityCardMissing kind="dataset" name={name} />;
  return (
    <EntityCardShell
      kind="dataset"
      name={data.name || name}
      subtitle={data.activeVersion ? `version ${data.activeVersion}` : "no version yet"}
    >
      <Row label="Rows">{(data.rows ?? 0).toLocaleString()}</Row>
      <Row label="Intent">{INTENT_LABEL[intentOf(data)]}</Row>
      <Row label="Source">{SOURCE_KIND_LABEL[data.sourceKind] ?? data.sourceKind}</Row>
      <Row label="Created">{data.createdAt ? <DateTime value={data.createdAt} /> : null}</Row>
    </EntityCardShell>
  );
}

export function EvalRunCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.evalRuns.evalRunsRetrieve({ id }),
    queryKey: ["eval-run", id],
    refetchInterval: (q) => livePoll((q.state.data as { status?: string } | undefined)?.status),
  });
  if (isPending) return <EntityCardSkeleton kind="evalRun" name={name} />;
  if (!data) return <EntityCardMissing kind="evalRun" name={name} />;

  const terminal = isTerminal(data.status);
  const progressLabel = !terminal ? evalProgressLabel(data.progress) : null;
  const headline = terminal
    ? evalHeadlineScore(data.summary as ComparisonSummary | null | undefined)
    : null;
  const partialMean = !terminal
    ? data.progress?.evaluatorStats?.find((s) => s.mean != null)?.mean
    : null;

  return (
    <EntityCardShell
      footer={
        <>
          <StatusBadge fallback="pending" status={data.status} />
          {progressLabel ? (
            <span className="text-xs tabular-nums text-muted-foreground">{progressLabel}</span>
          ) : headline ? (
            <span className="text-xs tabular-nums text-foreground">{headline.pct}%</span>
          ) : null}
        </>
      }
      kind="evalRun"
      name={data.name || name}
    >
      <Row label="Capability">{data.capabilityName}</Row>
      <Row label="Dataset">{data.datasetName}</Row>
      {partialMean != null ? <Row label="Partial">{`${scorePct(partialMean)}%`}</Row> : null}
      {headline ? <Row label={headline.label}>{`${headline.pct}%`}</Row> : null}
    </EntityCardShell>
  );
}

export function JobCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.finetuningJobs.finetuningJobsRetrieve({ id }),
    queryKey: ["finetuning-job", id],
    refetchInterval: (q) => livePoll((q.state.data as { status?: string } | undefined)?.status),
  });
  if (isPending) return <EntityCardSkeleton kind="job" name={name} />;
  if (!data) return <EntityCardMissing kind="job" name={name} />;

  const progress = (data.progress ?? {}) as Record<string, unknown>;
  const terminal = isTerminal(data.status);
  const percent = typeof progress.percent === "number" ? `${progress.percent}%` : null;
  const cost = jobCostUsd(data);

  const trained = typeof progress.trained_steps === "number" ? progress.trained_steps : null;
  const totalSteps = typeof progress.total_steps === "number" ? progress.total_steps : null;
  const stepLabel =
    !terminal && trained != null
      ? totalSteps != null
        ? `${trained}/${totalSteps}`
        : String(trained)
      : null;
  const lossRaw =
    typeof progress.train_loss === "number"
      ? progress.train_loss
      : typeof progress.latest_train_loss === "number"
        ? progress.latest_train_loss
        : null;
  const lossLabel = !terminal && lossRaw != null ? lossRaw.toFixed(4) : null;

  const judgeRows = (Array.isArray(progress.judge_evals) ? progress.judge_evals : []) as JudgeRow[];
  const judge = terminal && data.status !== "failed" ? pickJudgeHeadline(judgeRows) : null;
  const failedMsg =
    data.status === "failed" && data.errorMessage?.trim()
      ? userFacingJobError(data.errorMessage).slice(0, 120)
      : null;

  return (
    <EntityCardShell
      footer={
        <>
          <StatusBadge fallback="queued" status={data.status} />
          {!terminal && percent ? (
            <span className="text-xs tabular-nums text-foreground">{percent}</span>
          ) : judge?.aggregate_score != null ? (
            <span className="text-xs tabular-nums text-foreground">
              {scorePct(judge.aggregate_score)}%
            </span>
          ) : null}
        </>
      }
      kind="job"
      name={data.name || name}
    >
      <Row label="Base model">{data.baseModel}</Row>
      {stepLabel ? <Row label="Step">{stepLabel}</Row> : null}
      {lossLabel ? <Row label="Loss">{lossLabel}</Row> : null}
      {failedMsg ? <Row label="Error">{failedMsg}</Row> : null}
      {judge?.aggregate_score != null ? (
        <Row label="Score">{`${scorePct(judge.aggregate_score)}%`}</Row>
      ) : null}
      {judge?.baseline_delta != null ? (
        <Row label="vs baseline">
          <DeltaChip value={fromScore(judge.baseline_delta)} />
        </Row>
      ) : null}
      <Row label="Credits">{cost != null ? <CreditsAmount usd={cost} /> : null}</Row>
      {terminal ? (
        <Row label="Started">{data.startedAt ? <DateTime value={data.startedAt} /> : null}</Row>
      ) : null}
    </EntityCardShell>
  );
}

export function ModelCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.deployedModels.deployedModelsRetrieve({ id }),
    queryKey: ["deployed-model", id],
    refetchInterval: (q) => livePoll((q.state.data as { status?: string } | undefined)?.status),
  });
  if (isPending) return <EntityCardSkeleton kind="model" name={name} />;
  if (!data) return <EntityCardMissing kind="model" name={name} />;
  return (
    <EntityCardShell
      footer={<StatusBadge fallback="pending" status={data.status} />}
      kind="model"
      name={data.modelId || name}
      subtitle={data.isLora ? "LoRA" : undefined}
    >
      <Row label="Base">{data.baseModelId}</Row>
      <Row label="GPU">{data.gpuType}</Row>
      <Row label="Requests">{data.requestCount?.toLocaleString()}</Row>
      <Row label="Last active">
        {data.lastActiveAt ? <DateTime value={data.lastActiveAt} /> : null}
      </Row>
    </EntityCardShell>
  );
}

export function ExperimentCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.optimizerExperiments.optimizerExperimentsRetrieve({ id }),
    queryKey: ["optimizer-experiment", id],
    refetchInterval: (q) => livePoll((q.state.data as { status?: string } | undefined)?.status),
  });
  if (isPending) return <EntityCardSkeleton kind="experiment" name={name} />;
  if (!data) return <EntityCardMissing kind="experiment" name={name} />;

  const scores = (data.scores ?? {}) as Record<string, unknown>;
  const best = typeof scores.best === "number" ? scores.best : null;

  return (
    <EntityCardShell
      footer={
        <>
          <StatusBadge fallback="pending" status={data.status} />
          <span className="text-xs tabular-nums text-muted-foreground">
            Iteration {data.currentIteration ?? 0}/{data.numIterations ?? 0}
          </span>
        </>
      }
      kind="experiment"
      name={data.capabilityName || name}
    >
      <Row label="Dataset">{data.datasetName}</Row>
      <Row label="Eval set">{data.evalSetName}</Row>
      <Row label="Best score">{pct(best)}</Row>
      <Row label="Started">{data.createdAt ? <DateTime value={data.createdAt} /> : null}</Row>
    </EntityCardShell>
  );
}

export function ProjectCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.projects.projectsRetrieve({ id }),
    queryKey: ["project", id],
  });
  if (isPending) return <EntityCardSkeleton kind="project" name={name} />;
  if (!data) return <EntityCardMissing kind="project" name={name} />;
  return (
    <EntityCardShell kind="project" name={data.name || name}>
      {data.slug ? <p className="font-mono text-xs text-muted-foreground">{data.slug}</p> : null}
    </EntityCardShell>
  );
}

export function TraceCard({ id, name }: { id: string; name: string }) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.traces.tracesRetrieve({ traceId: id }),
    queryKey: ["trace-summary", id],
  });
  if (isPending) return <EntityCardSkeleton kind="trace" name={name} />;
  if (!data) return <EntityCardMissing kind="trace" name={name} />;
  const spans = data.spans?.length;
  return (
    <EntityCardShell kind="trace" name={name}>
      <Row label="Spans">{spans?.toLocaleString()}</Row>
    </EntityCardShell>
  );
}
