import { useId, useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { EvalScoreChip, FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import { ModelProviderChip } from "@/components/model-provider-chip";
import {
  type Comparison,
  type Comparisons,
  formatNativeMetric,
  nativeMetricLabel,
  nativePercentageMetrics,
} from "@/components/model-workflows/comparison-metrics";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CountChip } from "@/components/ui/count-chip";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SortableHeader } from "@/components/ui/sortable-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useTableSort } from "@/hooks/use-table-sort";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";
import type { FinetuningJobList, NativeEvaluation } from "@/openapi";

type Participant = { key: string; name: string; model: string; kind: string; job?: string };
type Coverage = Comparison["candidate"]["benchmarks"][string];
type ScoreMode = "raw" | "calibrated";
type EvaluationRow = {
  plan: NativeEvaluation;
  participant: Participant;
  job?: FinetuningJobList;
  raw: Record<string, Coverage>;
  calibrated: Record<string, Coverage>;
};

function participantScores(plan: NativeEvaluation, key: string, mode: ScoreMode) {
  const comparisons = (plan.results?.comparisons ?? {
    candidate: { calibrated: plan.results?.calibrated, raw: plan.results?.raw },
  }) as Comparisons;
  const own = comparisons[key]?.[mode]?.candidate;
  const baseline =
    key === (plan.config?.baseline ?? "base")
      ? Object.values(comparisons).find((modes) => modes[mode]?.baseline)?.[mode]?.baseline
      : undefined;
  return (own ?? baseline)?.benchmarks ?? {};
}

function averageScore(row: EvaluationRow, metric: string, mode: ScoreMode) {
  const scores = Object.values(row[mode]).flatMap((card) => {
    const score = card.metrics[metric];
    return score?.decisions > 0 && typeof score.mean === "number" && Number.isFinite(score.mean)
      ? [score.mean]
      : [];
  });
  return scores.length ? scores.reduce((sum, score) => sum + score, 0) / scores.length : undefined;
}

function evaluationState({ plan, participant, job }: EvaluationRow) {
  if (plan.deletedAt) return { label: "Deleted", variant: "neutral" as const };
  if (plan.state === "completed") return { label: "Completed", variant: "success" as const };
  if (["failed", "submission_unknown"].includes(plan.state))
    return {
      label: plan.state === "failed" ? "Failed" : "Needs attention",
      variant: "error" as const,
    };
  if (plan.state === "paused") return { label: "Paused", variant: "warning" as const };
  if (["draft", "prepared"].includes(plan.state))
    return { label: plan.state === "draft" ? "Draft" : "Prepared", variant: "neutral" as const };
  if (participant.kind === "trained" && job && ["failed", "cancelled"].includes(job.status))
    return { label: `Training ${job.status}`, variant: "error" as const };
  const final = plan.calls?.[`final_${participant.key}`]?.state;
  const calibration = plan.calls?.[`calibration_${participant.key}`]?.state;
  const preparations = new Map<string, string>();
  for (const p of (plan.config?.participants ?? []) as Participant[]) {
    const identity = p.job ?? p.key;
    if (p.kind !== "external" && !plan.config?.reuse?.[p.key] && !preparations.has(identity)) {
      preparations.set(identity, preparations.size === 0 ? "prepare" : `prepare_${p.key}`);
    }
  }
  const preparation = preparations.get(participant.job ?? participant.key);
  if ([final, calibration].some((state) => ["failed", "submission_unknown"].includes(state)))
    return { label: "Needs attention", variant: "error" as const };
  if (final === "completed") return { label: "Awaiting scores", variant: "info" as const };
  if (["running", "submitting"].includes(final))
    return { label: "Evaluating", variant: "info" as const };
  if (["running", "submitting"].includes(calibration))
    return { label: "Calibrating", variant: "info" as const };
  if (plan.calls?.fit_calibration?.state === "running")
    return { label: "Fitting calibration", variant: "info" as const };
  if (
    preparation &&
    ["calibration", "final"].some((role) =>
      ["running", "submitting"].includes(plan.calls?.[`${role}_${preparation}`]?.state)
    )
  )
    return { label: "Preparing evaluation", variant: "info" as const };
  if (participant.kind === "trained" && job && job.status !== "succeeded")
    return { label: "Waiting for training", variant: "info" as const };
  if (plan.state === "waiting_for_checkpoint" && !final && !calibration)
    return { label: "Waiting for checkpoint", variant: "info" as const };
  return { label: "Queued", variant: "info" as const };
}

function ScoresRow({
  row,
  metric,
  mode,
  projectId,
}: {
  row: EvaluationRow;
  metric: string;
  mode: ScoreMode;
  projectId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = useId();
  const state = evaluationState(row);
  const benchmarks = [...new Set([...Object.keys(row.raw), ...Object.keys(row.calibrated)])];
  const details = benchmarks.flatMap((benchmark) => {
    const raw = row.raw[benchmark];
    const calibrated = row.calibrated[benchmark];
    return [
      ...new Set([...Object.keys(raw?.metrics ?? {}), ...Object.keys(calibrated?.metrics ?? {})]),
    ].map((name) => ({
      benchmark,
      calibrated: calibrated?.metrics[name],
      card: mode === "raw" ? raw : calibrated,
      name,
      raw: raw?.metrics[name],
    }));
  });
  const cards = Object.values(row[mode]);
  const expected = cards.length
    ? cards.reduce((sum, card) => sum + card.expected, 0)
    : row.plan.config?.suites?.final?.rows;
  const scored = cards.length ? cards.reduce((sum, card) => sum + card.scored, 0) : null;
  const missing = cards.reduce((sum, card) => sum + card.missing_predictions, 0);
  const invalid = cards.reduce((sum, card) => sum + card.invalid_predictions, 0);
  const incompatible = cards.reduce((sum, card) => sum + card.incompatible_inputs, 0);
  return (
    <>
      <TableRow aria-label={row.participant.name}>
        <TableCell className="w-10 px-1">
          {details.length > 1 && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-controls={detailsId}
                  aria-expanded={expanded}
                  aria-label={`${expanded ? "Hide" : "Show"} scores for ${row.participant.name}`}
                  onClick={() => setExpanded(!expanded)}
                  size="icon-sm"
                  variant="ghost"
                >
                  <Icon.chevronDown
                    className={cn("size-3.5 transition-transform", !expanded && "-rotate-90")}
                  />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Benchmark scores</TooltipContent>
            </Tooltip>
          )}
        </TableCell>
        <TableCell className="px-2">
          <EntityRef
            className="max-w-full text-xs"
            evaluationFormat="decision"
            id={row.plan.id}
            kind="evalRun"
            name={row.plan.name || "Final benchmark"}
            projectId={projectId}
          />
        </TableCell>
        <TableCell className="px-2">
          <FtStatusBadge
            fallback={row.plan.state}
            label={state.label}
            status={
              state.variant === "success"
                ? "succeeded"
                : state.variant === "error"
                  ? "failed"
                  : state.variant === "info"
                    ? "running"
                    : row.plan.state
            }
          />
        </TableCell>
        <TableCell className="px-2 font-mono text-xs tabular-nums">
          {nativePercentageMetrics.has(metric) ? (
            <EvalScoreChip value={averageScore(row, metric, mode)} />
          ) : (
            formatNativeMetric(averageScore(row, metric, mode), metric)
          )}
        </TableCell>
        <TableCell className="min-w-0 overflow-hidden px-2" title={row.participant.name}>
          <div className="flex min-w-0 items-center gap-1.5">
            <ModelProviderChip className="max-w-full" compact model={row.participant.model} />
            {row.participant.key === row.plan.config?.baseline && (
              <Badge size="chip" variant="neutral">
                Baseline
              </Badge>
            )}
            {row.participant.kind !== "trained" && (
              <span className="text-xs text-muted-foreground">Base</span>
            )}
          </div>
        </TableCell>
        <TableCell className="px-2 text-center font-mono text-xs tabular-nums">
          <span>
            {scored ?? "—"} / {expected ?? "—"}
          </span>
          {(missing > 0 || invalid > 0 || incompatible > 0) && (
            <p className="text-warning">
              {missing} missing · {invalid} invalid · {incompatible} incompatible
            </p>
          )}
        </TableCell>
        <TableCell className="text-xs">
          <DateTime value={row.plan.updatedAt} />
        </TableCell>
      </TableRow>
      {expanded &&
        details.length > 1 &&
        details.map((detail, index) => (
          <TableRow
            aria-label={`${row.participant.name}: ${detail.benchmark} ${detail.name}`}
            className="bg-wash-subtle hover:bg-wash-subtle"
            id={index === 0 ? detailsId : undefined}
            key={`${detail.benchmark}:${detail.name}`}
          >
            <TableCell className="px-1" />
            <TableCell
              className="overflow-hidden truncate px-2 py-1.5 text-xs text-muted-foreground"
              colSpan={2}
              title={`${detail.benchmark} · ${nativeMetricLabel(detail.name)}`}
            >
              {detail.benchmark} · {nativeMetricLabel(detail.name)}{" "}
              {nativePercentageMetrics.has(detail.name) ? "↑" : "↓"}
            </TableCell>
            <TableCell className="px-2 py-1.5 font-mono text-xs font-semibold tabular-nums">
              {formatNativeMetric(detail[mode]?.mean, detail.name)}
            </TableCell>
            <TableCell className="px-2 py-1.5 text-xs text-muted-foreground">
              {detail[mode]?.interval_95
                ? `95% interval: ${detail[mode].interval_95?.map((value) => formatNativeMetric(value, detail.name)).join(" to ")}`
                : null}
            </TableCell>
            <TableCell className="px-2 py-1.5 text-center font-mono text-xs tabular-nums">
              {detail[mode]?.decisions ?? "—"} / {detail.card?.expected ?? "—"}
            </TableCell>
            <TableCell />
          </TableRow>
        ))}
    </>
  );
}

export function NativeEvaluationTable({
  plans,
  jobs,
  projectId,
  focusJobId,
  loading,
  error,
}: {
  plans: NativeEvaluation[];
  jobs: FinetuningJobList[];
  projectId: string;
  focusJobId?: string | null;
  loading?: boolean;
  error?: unknown;
}) {
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const [mode, setMode] = useState<ScoreMode>("raw");
  const { sort, onSort, sortRows } = useTableSort<
    "model" | "evaluation" | "state" | "score" | "scored" | "updated"
  >();
  const rows: EvaluationRow[] = plans.flatMap((plan) =>
    ((plan.config?.participants ?? []) as Participant[])
      .filter(
        (participant) =>
          !focusJobId || participant.kind !== "trained" || participant.job === focusJobId
      )
      .map((participant) => ({
        calibrated: participantScores(plan, participant.key, "calibrated"),
        job: jobs.find((job) => job.id === participant.job),
        participant,
        plan,
        raw: participantScores(plan, participant.key, "raw"),
      }))
  );
  const metrics = [
    ...new Set(
      rows.flatMap((row) => Object.values(row[mode]).flatMap((card) => Object.keys(card.metrics)))
    ),
  ];
  const metric =
    selectedMetric && metrics.includes(selectedMetric)
      ? selectedMetric
      : (["accuracy", "cross_entropy", "brier"].find((name) => metrics.includes(name)) ??
        metrics[0] ??
        "accuracy");
  const hasCalibrated = rows.some((row) => Object.keys(row.calibrated).length > 0);
  const sortedRows = sortRows(rows, (row, key) => {
    if (key === "model") return row.participant.model;
    if (key === "evaluation") return row.plan.name;
    if (key === "state") return evaluationState(row).label;
    if (key === "score") return averageScore(row, metric, mode);
    if (key === "updated") return row.plan.updatedAt?.getTime();
    const cards = Object.values(row[mode]);
    return cards.length ? cards.reduce((sum, card) => sum + card.scored, 0) : undefined;
  });
  return (
    <TooltipProvider>
      <Card
        aria-label="Run evaluations"
        className="flex min-w-0 flex-col gap-3 overflow-hidden p-4 pb-0"
        role="region"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Icon.successDouble className="size-4 text-muted-foreground" />
          <h3 className="text-xs">Evals</h3>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {hasCalibrated && (
              <Tabs
                className="w-auto"
                onValueChange={(value) => setMode(value as ScoreMode)}
                value={mode}
              >
                <TabsList aria-label="Score mode" className="h-7">
                  {(["raw", "calibrated"] as const).map((value) => (
                    <TabsTrigger className="px-2 py-0.5 text-xs" key={value} value={value}>
                      {value === "raw" ? "Raw" : "Calibrated"}
                    </TabsTrigger>
                  ))}
                </TabsList>
              </Tabs>
            )}
            {metrics.length > 1 && (
              <Select onValueChange={setSelectedMetric} value={metric}>
                <SelectTrigger aria-label="Average score metric" className="w-40 text-xs" size="sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {metrics.map((name) => (
                    <SelectItem key={name} value={name}>
                      {nativeMetricLabel(name)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
            <CountChip count={rows.length} />
          </div>
        </div>
        {error != null && (
          <Alert variant="destructive">
            {errorMessage(error, "Couldn't load this run's evaluations.")}
          </Alert>
        )}
        {loading && !plans.length ? (
          <Skeleton className="h-36 w-full" />
        ) : !rows.length ? (
          !error && (
            <EmptyState
              icon={Icon.successDouble}
              size="section"
              title="No evaluations pinned to this run"
            />
          )
        ) : (
          <>
            <div className="-mx-4">
              <Table aria-label="Evaluation jobs" className="w-full min-w-[960px] table-fixed">
                <TableHeader className="border-t border-border">
                  <TableRow>
                    <TableHead aria-label="Expand scores" className="w-10 border-r-0 px-1" />
                    <SortableHeader
                      className="w-56 whitespace-nowrap px-2"
                      label="Evaluation"
                      onSort={onSort}
                      sort={sort}
                      sortKey="evaluation"
                    />
                    <SortableHeader
                      className="w-44 whitespace-nowrap px-2"
                      label="Status"
                      onSort={onSort}
                      sort={sort}
                      sortKey="state"
                    />
                    <SortableHeader
                      className="w-28 whitespace-nowrap px-2"
                      label="Average score"
                      onSort={onSort}
                      sort={sort}
                      sortKey="score"
                    >
                      <span
                        title={`Equal-weight average of measured ${nativeMetricLabel(metric).toLowerCase()} across final benchmarks. ${nativePercentageMetrics.has(metric) ? "Higher" : "Lower"} is better.`}
                      >
                        Avg. score
                      </span>
                    </SortableHeader>
                    <SortableHeader
                      className="px-2"
                      label="Model"
                      onSort={onSort}
                      sort={sort}
                      sortKey="model"
                    />
                    <SortableHeader
                      centered
                      className="w-24 whitespace-nowrap px-2 text-center"
                      label="Scored"
                      onSort={onSort}
                      sort={sort}
                      sortKey="scored"
                    />
                    <SortableHeader
                      className="w-28 whitespace-nowrap px-2"
                      label="Updated"
                      onSort={onSort}
                      sort={sort}
                      sortKey="updated"
                    />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedRows.map((row) => (
                    <ScoresRow
                      key={`${row.plan.id}:${row.participant.key}`}
                      metric={metric}
                      mode={mode}
                      projectId={projectId}
                      row={row}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
            {plans.map((plan) => (
              <div
                className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2 pb-4 text-xs text-muted-foreground"
                key={plan.id}
              >
                {Object.entries(plan.config?.suites ?? {}).map(([role, value]) => {
                  const suite = value as { cell: string; dataset?: string; rows: number };
                  return (
                    <div className="flex min-w-0 max-w-full items-center gap-1.5" key={role}>
                      <span className="shrink-0 capitalize">{role}</span>
                      {suite.dataset ? (
                        <EntityRef
                          cellId={suite.cell}
                          className="max-w-64 text-xs"
                          id={suite.dataset}
                          kind="dataset"
                          name={
                            role === "final"
                              ? (plan.finalSuite?.datasetName ?? "Final suite")
                              : (plan.calibrationSuite?.datasetName ?? "Calibration suite")
                          }
                          projectId={projectId}
                        />
                      ) : null}
                      <span className="shrink-0 tabular-nums">
                        {suite.rows.toLocaleString()} decisions
                      </span>
                    </div>
                  );
                })}
                {plan.error && <Alert variant="destructive">{plan.error}</Alert>}
              </div>
            ))}
          </>
        )}
      </Card>
    </TooltipProvider>
  );
}
