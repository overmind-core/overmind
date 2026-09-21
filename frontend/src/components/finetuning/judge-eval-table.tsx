import { type ReactNode, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { EntityRef } from "@/components/entity-ref";
import {
  aggregateEvaluator,
  type ComparisonSummary,
  primaryValue,
} from "@/components/evaluations/run-comparison";
import type { EvaluationDisplayRow } from "@/components/finetuning/evaluation-plan";
import { DeltaChip, EvalScoreChip, FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import type { ExperimentSnapshot } from "@/components/finetuning/job-snapshot";
import { meanMetricScore } from "@/components/finetuning/judge-eval-score";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Button } from "@/components/ui/button";
import { DateTime } from "@/components/ui/datetime";
import { fromScore } from "@/components/ui/delta-chip";
import { Icon } from "@/components/ui/icons";
import { TableCell, TableRow } from "@/components/ui/table";
import type { ClassMetrics, FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import { cn, scorePct } from "@/lib/utils";

function ExperimentCell({ snapshot }: { snapshot: ExperimentSnapshot }) {
  return <ModelProviderChip className="max-w-full" compact model={snapshot.job.baseModel} />;
}

function judgeEvalStepLabel(row: FinetuningJudgeEvalRow): string {
  if (row.label) return row.label;
  if (row.kind === "baseline") return "Incumbent · before";
  if (row.kind === "incumbent_after") return "Incumbent · after";
  if (row.kind === "model_before") return "Base model · before";
  if (row.kind === "final") return "Trained model · after";
  return row.checkpoint_step != null ? String(row.checkpoint_step) : "—";
}

function BreakdownNoteRow({ children, cols }: { children: ReactNode; cols: number }) {
  return (
    <TableRow className="bg-wash-subtle hover:bg-wash-subtle">
      <TableCell className="py-1.5 pl-10 text-xs text-muted-foreground" colSpan={cols}>
        {children}
      </TableCell>
    </TableRow>
  );
}

/** One parent-table row per score, so dividers run full width; the colSpans
    line each score up under the parent's Score column. */
function MetricScoreRows({
  metrics,
  isAll,
}: {
  metrics: Array<{ name: string; score: number }>;
  isAll: boolean;
}) {
  return (
    <>
      {metrics.map((m) => (
        <TableRow className="bg-wash-subtle hover:bg-wash-subtle" key={m.name}>
          <TableCell className="px-1" />
          <TableCell
            className="overflow-hidden truncate px-2 py-1.5 text-xs text-muted-foreground"
            colSpan={isAll ? 3 : 2}
            title={m.name}
          >
            {m.name}
          </TableCell>
          <TableCell
            className={cn(
              "px-2 py-1.5 font-mono text-xs font-semibold tabular-nums",
              scorePct(m.score) >= 70 && "text-success",
              scorePct(m.score) >= 40 && scorePct(m.score) < 70 && "text-warning",
              scorePct(m.score) < 40 && "text-destructive"
            )}
          >
            {scorePct(m.score)}%
          </TableCell>
          <TableCell colSpan={3} />
        </TableRow>
      ))}
    </>
  );
}

/** Fallback for rows mirrored from a `job.progress` snapshot that predates
    `metric_scores`. Query key is the run hover card's, so the cache is shared. */
function EvalRunBreakdownRows({
  runId,
  cols,
  isAll,
}: {
  runId: string;
  cols: number;
  isAll: boolean;
}) {
  const { data, isPending } = useQuery({
    queryFn: () => apiClient.evalRuns.evalRunsRetrieve({ id: runId }),
    queryKey: ["eval-run", runId],
  });
  if (isPending) return <BreakdownNoteRow cols={cols}>Loading test scores…</BreakdownNoteRow>;
  const summary = (data?.summary ?? null) as ComparisonSummary | null;
  const metrics = (summary?.metrics ?? [])
    .map((name) => ({ name, score: primaryValue(aggregateEvaluator(summary!, name)) }))
    .filter((m): m is { name: string; score: number } => m.score != null);
  if (metrics.length === 0) {
    return <BreakdownNoteRow cols={cols}>No per-test scores for this run.</BreakdownNoteRow>;
  }
  return <MetricScoreRows isAll={isAll} metrics={metrics} />;
}

export function JudgeEvalTableRow({
  row,
  snapshot,
  isAll,
  projectId,
  deployedUuidByServingId,
}: {
  row: EvaluationDisplayRow;
  snapshot: ExperimentSnapshot;
  isAll: boolean;
  projectId: string;
  deployedUuidByServingId: Map<string, string>;
}) {
  const [expanded, setExpanded] = useState(false);
  const metrics = row.metric_scores ?? [];
  const canExpand = metrics.length > 0 || row.eval_run_id != null;
  // Expand + (Experiment?) + Step + Status + Score + Model + Samples + Created
  const cols = 7 + (isAll ? 1 : 0);
  const mean = meanMetricScore(row);

  return (
    <>
      <TableRow>
        <TableCell className="w-10 px-1">
          {canExpand ? (
            <Button
              aria-expanded={expanded}
              aria-label={expanded ? "Hide test scores" : "Show test scores"}
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((v) => !v);
              }}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronDown
                className={cn("size-3.5 transition-transform", !expanded && "-rotate-90")}
              />
            </Button>
          ) : (
            <span aria-hidden className="inline-block size-7" />
          )}
        </TableCell>
        {isAll ? (
          <TableCell className="whitespace-nowrap px-2">
            <ExperimentCell snapshot={snapshot} />
          </TableCell>
        ) : null}
        <TableCell className="w-56 whitespace-nowrap px-2 font-mono text-xs tabular-nums">
          {row.eval_run_id ? (
            <EntityRef
              id={row.eval_run_id}
              kind="evalRun"
              name={judgeEvalStepLabel(row)}
              projectId={projectId}
            />
          ) : (
            judgeEvalStepLabel(row)
          )}
        </TableCell>
        <TableCell className="w-44 whitespace-nowrap px-2" title={row.waitingReason}>
          <FtStatusBadge fallback={row.status} status={row.status} />
        </TableCell>
        <TableCell className="w-36 whitespace-nowrap px-2">
          <span className="inline-flex items-center gap-1.5">
            <EvalScoreChip value={mean} />
            {row.baseline_delta != null ? (
              <span
                title={row.comparison_label ? `Compared with ${row.comparison_label}` : undefined}
              >
                <DeltaChip value={fromScore(row.baseline_delta)} />
              </span>
            ) : null}
          </span>
        </TableCell>
        <TableCell
          className="min-w-0 overflow-hidden px-2"
          title={row.error_message || row.model_id || undefined}
        >
          <div className="min-w-0 max-w-full overflow-hidden">
            {row.planned && row.model_id ? (
              <ModelProviderChip className="max-w-full" compact model={row.model_id} />
            ) : row.model_id ? (
              <FinetuningModelChip
                className="max-w-full"
                compact
                deployedModelUuid={deployedUuidByServingId.get(row.model_id)}
                model={row.model_id}
                projectId={projectId}
              />
            ) : (
              <span className="text-xs text-muted-foreground">
                {row.planned ? "Incumbent model" : "—"}
              </span>
            )}
          </div>
        </TableCell>
        <TableCell className="w-20 whitespace-nowrap px-2 text-center font-mono text-xs tabular-nums">
          {row.sample_count ?? "—"}
        </TableCell>
        <TableCell className="w-28 whitespace-nowrap px-2 font-mono text-xs tabular-nums">
          <DateTime value={row.created_at} />
        </TableCell>
      </TableRow>
      {expanded && canExpand ? (
        metrics.length > 0 ? (
          <MetricScoreRows isAll={isAll} metrics={metrics} />
        ) : (
          <EvalRunBreakdownRows cols={cols} isAll={isAll} runId={row.eval_run_id!} />
        )
      ) : null}
    </>
  );
}

export function latestClassMetricsOf(
  rows: FinetuningJudgeEvalRow[]
): { metrics: ClassMetrics; row: FinetuningJudgeEvalRow } | null {
  const scored = rows.filter((r) => r.class_metrics?.classes?.length);
  if (scored.length === 0) return null;
  const rank = (r: FinetuningJudgeEvalRow) =>
    r.kind === "final"
      ? Number.MAX_SAFE_INTEGER
      : r.kind === "incumbent_after"
        ? Number.MAX_SAFE_INTEGER - 1
        : r.kind === "baseline"
          ? -1
          : (r.checkpoint_step ?? 0);
  const best = [...scored].sort((a, b) => rank(a) - rank(b)).at(-1);
  return best ? { metrics: best.class_metrics as ClassMetrics, row: best } : null;
}

export function classMetricsSourceLabel(row: FinetuningJudgeEvalRow): string {
  if (row.kind === "incumbent_after") return "incumbent after training";
  if (row.kind === "model_before") return "base model baseline";
  if (row.kind === "final") return "final eval";
  if (row.kind === "baseline") return "baseline eval";
  return row.checkpoint_step != null ? `checkpoint step ${row.checkpoint_step}` : "checkpoint eval";
}
