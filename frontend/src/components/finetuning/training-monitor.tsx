import { type ReactNode, useMemo, useState } from "react";

import { type Query, useQueries } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import apiClient from "@/client";
import {
  groupEvaluationDisplayRows,
  hasPendingEvaluations,
} from "@/components/finetuning/evaluation-plan";
import {
  ClassMetricsTable,
  ClassSeriesChart,
  ConfusionMatrixGrid,
  MetricSeriesChart,
  MultiSeriesChart,
} from "@/components/finetuning/finetuning-charts";
import {
  buildConfigChips,
  buildConfigItems,
  DeltaChip,
  experimentColor,
  FtStatusBadge,
  HeaderStat,
  HelpTip,
  METRIC_HELP,
  ProgressBar,
  RunConfigDisclosure,
} from "@/components/finetuning/finetuning-chrome";
import {
  buildExperimentSnapshot,
  type ExperimentSnapshot,
  experimentLabel,
  isTerminalStatus,
  stepsLabelFor,
} from "@/components/finetuning/job-snapshot";
import {
  classMetricsSourceLabel,
  JudgeEvalTableRow,
  latestClassMetricsOf,
} from "@/components/finetuning/judge-eval-table";
import type { MetricPoint, MetricSeries } from "@/components/finetuning/loss-chart";
import { liveSeriesPoints } from "@/components/finetuning/loss-series";
import { ModelLiveAction } from "@/components/finetuning/model-live-action";
import { scrubInfraLeak, userFacingJobError } from "@/components/finetuning/train/model-config";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { CountChip } from "@/components/ui/count-chip";
import { CreditsAmount } from "@/components/ui/credits";
import { DateTime } from "@/components/ui/datetime";
import { fromScore } from "@/components/ui/delta-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  type LossCurveData,
  useCancelFinetuningJobMutation,
  useFinetuningJobQuery,
  useFinetuningRunJobsQuery,
  useProjectDatasetsQuery,
  useRetryFinetuningJobMutation,
} from "@/hooks/use-finetuning";
import { useDeployedModelsQuery } from "@/hooks/use-inference";
import { seriesColor } from "@/lib/colors";
import { groupCostUsd, jobCostUsd } from "@/lib/finetuning-cost";
import { downloadStatusLine, progressPhaseLabel } from "@/lib/finetuning-progress";
import { formatElapsed } from "@/lib/formatters";
import { errorMessage } from "@/lib/notify";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";
import type { FinetuningJobList } from "@/openapi";

export function WizardMonitor({
  projectId,
  groupId,
  jobId,
  onFocusJob,
}: {
  projectId: string;
  groupId: string;
  /** Focused experiment from the URL; null = "All". Derived, not latched, so the
   *  header jobs popover can switch experiments without remounting this route. */
  jobId?: string | null;
  onFocusJob: (jobId: string | null) => void;
}) {
  const { data, isLoading, error } = useFinetuningRunJobsQuery(groupId);
  const jobs = data ?? [];

  const focusJob = jobs.length === 1 ? jobs[0] : (jobs.find((j) => j.id === jobId) ?? null);

  return (
    <TooltipProvider>
      <PageShell
        className="min-h-0"
        header={
          <PageHeader
            icon={
              <Icon.finetuning
                aria-hidden
                className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
              />
            }
            title="Training run"
          />
        }
        variant="full"
      >
        {isLoading ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-40 w-full" />
            <Skeleton className="h-56 w-full" />
            <Skeleton className="h-56 w-full" />
          </div>
        ) : error && jobs.length === 0 ? (
          <Alert variant="destructive">
            {errorMessage(error, "Couldn't load this training run.")}
          </Alert>
        ) : jobs.length > 0 ? (
          <div className="min-h-0 flex-1 overflow-auto">
            <TrainingMonitorPanel
              focusJobId={focusJob?.id ?? null}
              groupJobs={jobs}
              onFocusJob={onFocusJob}
              projectId={projectId}
            />
          </div>
        ) : (
          <EmptyState
            action={
              <Button asChild size="sm" variant="secondary">
                <Link to="/training">Back to training</Link>
              </Button>
            }
            className="flex-1"
            description="This training run doesn't exist, or its jobs were removed."
            icon={Icon.training}
            size="section"
            title="No jobs found"
          />
        )}
      </PageShell>
    </TooltipProvider>
  );
}

export function TrainingMonitorPanel({
  focusJobId,
  projectId,
  groupJobs,
  onFocusJob,
}: {
  focusJobId: string | null;
  projectId: string;
  groupJobs: FinetuningJobList[];
  onFocusJob: (jobId: string | null) => void;
}) {
  const multi = groupJobs.length > 1;
  const anchorJobId = focusJobId ?? groupJobs[0]?.id;
  const jobQuery = useFinetuningJobQuery(anchorJobId);
  const job = jobQuery.data;
  const datasetsQuery = useProjectDatasetsQuery(projectId);
  // Scoped to the run's capability (its experiments share one): a project-wide page
  // hid any deployment outside the project's newest rows.
  const deployedQuery = useDeployedModelsQuery({
    capability: groupJobs[0]?.capability ?? undefined,
    pageSize: 100,
    projectId,
  });
  const deployedUuidByServingId = useMemo(() => {
    const map = new Map<string, string>();
    for (const m of deployedQuery.data?.results ?? []) {
      if (m.modelId) map.set(m.modelId, m.id);
    }
    return map;
  }, [deployedQuery.data]);

  const curvesQueries = useQueries({
    queries: groupJobs.map((j) => ({
      queryFn: async () =>
        (await apiClient.finetuningJobs.finetuningJobsLossCurvesRetrieve({
          id: j.id,
        })) as unknown as LossCurveData,
      queryKey: ["finetuning-loss-curves", j.id] as const,
      refetchInterval: (query: Query<LossCurveData>) =>
        !isTerminalStatus(j.status as string) ||
        hasPendingEvaluations(
          j,
          query.state.data?.judge_evals ?? (j.progress as LossCurveData | null)?.judge_evals ?? []
        )
          ? 3_000
          : false,
    })),
  });

  if (jobQuery.error && !job) {
    return (
      <Alert variant="destructive">
        {errorMessage(jobQuery.error, "Couldn't load this job. It may have been deleted.")}
      </Alert>
    );
  }
  if (!job) return <Skeleton className="h-64" />;

  const snapshots = groupJobs.map((j, i) =>
    buildExperimentSnapshot(j, curvesQueries[i]?.data, experimentColor(i))
  );
  const focus = focusJobId
    ? (snapshots.find((s) => s.job.id === focusJobId) ?? snapshots[0])
    : multi
      ? null
      : snapshots[0];
  const isAll = focus == null;

  const metricsLoading = isAll
    ? curvesQueries.some((q) => q.isLoading)
    : (curvesQueries[snapshots.indexOf(focus)]?.isLoading ?? false);

  const judgeEvalRows = groupEvaluationDisplayRows(focus ? [focus] : snapshots);

  // The backend stamps class_metrics only for label-referenced eval datasets.
  const hasClassMetrics = snapshots.some((s) =>
    s.judgeEvals.some((e) => e.class_metrics?.classes?.length)
  );
  const focusClassMetrics = focus ? latestClassMetricsOf(focus.judgeEvals) : null;
  const classRowsForFocus = focus
    ? focus.judgeEvals.filter((e) => e.class_metrics?.classes?.length)
    : [];

  const liveProgressOf = (s: ExperimentSnapshot) => s.liveProgress;

  const pct = (v: number) => `${v.toFixed(1)}%`;
  const seriesOf = (pick: (s: ExperimentSnapshot) => MetricPoint[]): MetricSeries[] =>
    snapshots.map((s) => ({
      color: s.color,
      id: s.job.id,
      name: experimentLabel(s.job),
      points: pick(s),
    }));
  const trainLossPointsOf = (s: ExperimentSnapshot): MetricPoint[] =>
    s.trainPoints
      .filter((p) => p.train != null)
      .map((p) => ({ step: p.step, value: p.train as number }));
  const accTrainPoints = (s: ExperimentSnapshot) =>
    liveSeriesPoints(
      liveProgressOf(s)?.metrics_history,
      (r) => r.step,
      (r) => r.token_accuracy,
      100
    );
  const lossSeries = seriesOf(trainLossPointsOf);
  const lrSeries = seriesOf((s) => s.lrPoints);
  const gradSeries = seriesOf((s) => s.gradPoints);
  const accSeries = seriesOf(accTrainPoints);
  const hasPoints = (series: MetricSeries[]) => series.some((s) => s.points.length > 0);

  const datasetName =
    datasetsQuery.data?.results?.find((d) => d.id === job.dataset)?.name ??
    (job.dataset ? `${job.dataset.slice(0, 8)}…` : "—");

  // BasetenRunner.submit persists the derived plan back onto job.hyperparameters,
  // so these are the trained-with values, never "auto".
  const hp = (job.hyperparameters ?? {}) as Record<string, unknown>;
  const configChips = buildConfigChips(hp, job);
  const configItems = buildConfigItems(hp, job);

  return (
    <div className="flex flex-col gap-5 pb-6">
      {multi && (
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="flex min-w-0 flex-col gap-2">
            <h3 className="inline-flex items-center gap-1.5 text-xs text-foreground">
              Experiments
              <CountChip count={groupJobs.length} />
            </h3>
            <div
              aria-label="Experiments"
              className="flex flex-wrap items-center gap-1.5"
              role="group"
            >
              <button
                aria-pressed={isAll}
                className={cn(EXPERIMENT_CHIP_CLASS, isAll && EXPERIMENT_CHIP_SELECTED_CLASS)}
                onClick={() => onFocusJob(null)}
                type="button"
              >
                All
              </button>
              {snapshots.map((s) => (
                <ExperimentChip
                  key={s.job.id}
                  onSelect={() => onFocusJob(s.job.id)}
                  selected={focus?.job.id === s.job.id}
                  snapshot={s}
                />
              ))}
            </div>
          </div>
          <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Dataset</dt>
              <dd className="max-w-[240px] truncate font-medium" title={job.dataset}>
                {datasetName}
              </dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Started</dt>
              <dd className="font-medium">
                <DateTime value={job.startedAt ?? job.createdAt} />
              </dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Credits used</dt>
              <dd className="font-mono font-medium tabular-nums">
                {(() => {
                  const c = groupCostUsd(groupJobs);
                  return c != null ? <CreditsAmount usd={c} /> : "—";
                })()}
              </dd>
            </div>
            {job.completedAt && focus && (
              <div className="flex items-baseline gap-2">
                <dt className="text-muted-foreground">Completed</dt>
                <dd className="font-medium">
                  <DateTime value={job.completedAt} />
                </dd>
              </div>
            )}
          </dl>
        </div>
      )}

      {isAll ? (
        <div className="flex flex-col gap-3">
          {snapshots.map((s) => (
            <ExperimentRunCard key={s.job.id} projectId={projectId} snapshot={s} />
          ))}
        </div>
      ) : (
        <ExperimentRunCard
          datasetName={multi ? undefined : datasetName}
          projectId={projectId}
          showIdentityMeta={!multi}
          snapshot={focus}
        />
      )}

      {/* Focus mode only: per-experiment configs differ in All mode. */}
      {focus && <RunConfigDisclosure chips={configChips} items={configItems} />}

      <div className="flex flex-col gap-2">
        <h3 className="text-xs text-muted-foreground">Metrics</h3>
        <div className="grid gap-3 md:grid-cols-2">
          <MonitorChartCard
            emptyHint="No train loss from provider yet"
            help={METRIC_HELP.loss}
            isLoading={metricsLoading}
            subtitle={isAll ? "train · one line per experiment" : "train loss · per step"}
            title="Loss"
          >
            {isAll ? (
              hasPoints(lossSeries) ? (
                <MultiSeriesChart height={180} series={lossSeries} />
              ) : null
            ) : trainLossPointsOf(focus).length > 0 ? (
              <MetricSeriesChart
                color={seriesColor(0)}
                data={trainLossPointsOf(focus)}
                height={180}
                name="train loss"
              />
            ) : null}
          </MonitorChartCard>

          <MonitorChartCard
            emptyHint="No learning-rate series from provider yet"
            help={METRIC_HELP.learningRate}
            isLoading={metricsLoading}
            subtitle="schedule over steps"
            title="Learning rate"
          >
            {isAll ? (
              hasPoints(lrSeries) ? (
                <MultiSeriesChart
                  height={180}
                  series={lrSeries}
                  valueFormatter={(v) => v.toExponential(2)}
                />
              ) : null
            ) : focus.lrPoints.length > 0 ? (
              <MetricSeriesChart
                color={seriesColor(1)}
                data={focus.lrPoints}
                height={180}
                name="learning rate"
                valueFormatter={(v) => v.toExponential(2)}
              />
            ) : null}
          </MonitorChartCard>

          <MonitorChartCard
            emptyHint="No token accuracy from the training callback yet"
            help={METRIC_HELP.accuracy}
            isLoading={metricsLoading}
            subtitle={isAll ? "train · one line per experiment" : "train token accuracy · per step"}
            title="Token accuracy"
          >
            {isAll ? (
              hasPoints(accSeries) ? (
                <MultiSeriesChart height={180} series={accSeries} valueFormatter={pct} />
              ) : null
            ) : accTrainPoints(focus).length > 0 ? (
              <MetricSeriesChart
                color={seriesColor(2)}
                data={accTrainPoints(focus)}
                height={180}
                name="train accuracy"
                valueFormatter={pct}
              />
            ) : null}
          </MonitorChartCard>

          <MonitorChartCard
            emptyHint="No grad norm from provider yet"
            help={METRIC_HELP.gradNorm}
            isLoading={metricsLoading}
            subtitle="gradient norm · per step"
            title="Grad norm"
          >
            {isAll ? (
              hasPoints(gradSeries) ? (
                <MultiSeriesChart height={180} series={gradSeries} />
              ) : null
            ) : focus.gradPoints.length > 0 ? (
              <MetricSeriesChart
                color={seriesColor(3)}
                data={focus.gradPoints}
                height={180}
                name="grad norm"
              />
            ) : null}
          </MonitorChartCard>

          {/* Tables and the matrix can't overlay experiments, so All mode asks
              for one instead. */}
          {hasClassMetrics && (
            <MonitorChartCard
              emptyHint={
                isAll
                  ? "Select an experiment above to view class metrics"
                  : "Per-class metrics appear once a checkpoint eval finishes on a labelled dataset"
              }
              help={METRIC_HELP.classTable}
              isLoading={metricsLoading}
              subtitle={
                focusClassMetrics ? classMetricsSourceLabel(focusClassMetrics.row) : undefined
              }
              title="Class metrics"
            >
              {focusClassMetrics ? <ClassMetricsTable metrics={focusClassMetrics.metrics} /> : null}
            </MonitorChartCard>
          )}

          {hasClassMetrics && (
            <MonitorChartCard
              emptyHint={
                isAll
                  ? "Select an experiment above to view the confusion matrix"
                  : "The confusion matrix appears once a checkpoint eval finishes on a labelled dataset"
              }
              help={METRIC_HELP.confusion}
              isLoading={metricsLoading}
              subtitle={
                focusClassMetrics ? classMetricsSourceLabel(focusClassMetrics.row) : undefined
              }
              title="Confusion matrix"
            >
              {focusClassMetrics?.metrics.confusion_matrix?.labels?.length ? (
                <ConfusionMatrixGrid data={focusClassMetrics.metrics.confusion_matrix} />
              ) : null}
            </MonitorChartCard>
          )}

          {hasClassMetrics && (
            <MonitorChartCard
              className="md:col-span-2"
              emptyHint={
                isAll
                  ? "Select an experiment above to view per-class trends"
                  : "Per-class trends appear once two or more evals finish on a labelled dataset"
              }
              help={METRIC_HELP.classSeries}
              isLoading={metricsLoading}
              subtitle="per class · across checkpoint evals"
              title="Precision / recall over checkpoints"
            >
              {classRowsForFocus.length > 0 ? <ClassSeriesChart rows={classRowsForFocus} /> : null}
            </MonitorChartCard>
          )}
        </div>
      </div>

      <Card className="flex flex-col gap-3 overflow-hidden p-4 pb-0">
        <div className="flex items-center gap-2">
          <Icon.successDouble className="size-4 text-muted-foreground" />
          <h3 className="text-xs">Evals</h3>
          <CountChip className="ml-auto" count={judgeEvalRows.length} />
        </div>
        {judgeEvalRows.length === 0 ? (
          <p className={cn(PROSE, "pb-4 text-sm text-muted-foreground")}>
            {job.evalDataset && job.evalSet
              ? "No evaluations selected."
              : "No eval dataset or eval set linked. Judge scores stay empty."}
          </p>
        ) : (
          // No outer overflow-x-auto: Table already scrolls; a second nested
          // scroller fights it. -mx-4 cancels the Card padding so the separators
          // reach the card edges.
          <div className="-mx-4">
            {/* min-w-0 overrides Table's min-w-max; Model is the only column
                without a width, so it absorbs the leftover card width. */}
            <Table className="w-full min-w-0 table-fixed">
              <TableHeader className="border-t border-border">
                <TableRow>
                  {/* border-r-0: merge with the next header so the chevron
                      gutter doesn't read as an empty column. */}
                  <TableHead aria-label="Expand" className="w-10 border-r-0 px-1" />
                  {isAll && (
                    <TableHead className="w-48 whitespace-nowrap px-2">Experiment</TableHead>
                  )}
                  <TableHead className="w-56 whitespace-nowrap px-2">Evaluation</TableHead>
                  <TableHead className="w-44 whitespace-nowrap px-2">Status</TableHead>
                  <TableHead className="w-36 whitespace-nowrap px-2">Score</TableHead>
                  <TableHead className="px-2">Model</TableHead>
                  <TableHead className="w-20 whitespace-nowrap px-2 text-center">Samples</TableHead>
                  <TableHead className="w-28 whitespace-nowrap px-2">Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {judgeEvalRows.map(({ row, snapshots: rowSnapshots }) => (
                  <JudgeEvalTableRow
                    deployedUuidByServingId={deployedUuidByServingId}
                    isAll={isAll}
                    key={`${row.kind}-${row.eval_run_id ?? `${rowSnapshots[0].job.id}-${row.id}`}`}
                    projectId={projectId}
                    row={row}
                    snapshots={rowSnapshots}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </Card>
    </div>
  );
}

function ExperimentRunCard({
  snapshot,
  projectId,
  datasetName,
  showIdentityMeta = false,
}: {
  snapshot: ExperimentSnapshot;
  projectId: string;
  datasetName?: string;
  showIdentityMeta?: boolean;
}) {
  const job = snapshot.job;
  const { progress, percent, terminal, currentLoss, latestScored, baselineScore } = snapshot;
  const stepsLabel = stepsLabelFor(progress);
  const tokens = progress.tokens_processed;
  const percentLabel = percent != null ? `${percent.toFixed(0)}%` : terminal ? "100%" : "—";
  const timeLabel = experimentTimeLabel(snapshot);
  const tokenAcc = experimentTokenAccLabel(snapshot);

  return (
    <Card className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-xs text-foreground">Model</h3>
          <ModelProviderChip className="max-w-[260px]" model={job.baseModel} />
        </div>
        <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
          {showIdentityMeta && datasetName != null && (
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Dataset</dt>
              <dd className="max-w-[240px] truncate font-medium" title={job.dataset}>
                {datasetName}
              </dd>
            </div>
          )}
          {showIdentityMeta && (
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Started</dt>
              <dd className="font-medium">
                <DateTime value={job.startedAt ?? job.createdAt} />
              </dd>
            </div>
          )}
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">Credits used</dt>
            <dd className="font-mono font-medium tabular-nums">
              {(() => {
                const c = jobCostUsd(job);
                return c != null ? <CreditsAmount usd={c} /> : "—";
              })()}
            </dd>
          </div>
          {job.completedAt && (
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Completed</dt>
              <dd className="font-medium">
                <DateTime value={job.completedAt} />
              </dd>
            </div>
          )}
        </dl>
      </div>

      <div className="flex flex-wrap items-stretch border-t border-border/70 pt-3.5">
        <HeaderStat label="Current loss" primary>
          {currentLoss != null ? currentLoss.toFixed(4) : "—"}
        </HeaderStat>
        <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
        <HeaderStat label="Tokens">{tokens != null ? tokens.toLocaleString() : "—"}</HeaderStat>
        {tokenAcc != null && (
          <>
            <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
            <HeaderStat label="Token accuracy">{tokenAcc}</HeaderStat>
          </>
        )}
        <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
        <HeaderStat
          label={latestScored?.baseline_delta != null ? "Score vs baseline" : "Eval score"}
        >
          {latestScored?.aggregate_score != null ? (
            <span className="inline-flex items-center gap-2">
              {scorePct(latestScored.aggregate_score)}%
              {latestScored.baseline_delta != null && (
                <DeltaChip value={fromScore(latestScored.baseline_delta)} />
              )}
            </span>
          ) : baselineScore != null ? (
            `${scorePct(baselineScore)}%`
          ) : (
            "—"
          )}
        </HeaderStat>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <div className="flex items-baseline gap-2.5">
            <span className="font-mono text-xl font-semibold leading-none tabular-nums">
              {percentLabel}
            </span>
            <span className="text-xs tabular-nums text-muted-foreground">
              {stepsLabel !== "—"
                ? `Step ${stepsLabel}`
                : terminal
                  ? "Done"
                  : "Waiting for the first step…"}
            </span>
          </div>
          {timeLabel && (
            <span className="text-xs tabular-nums text-muted-foreground">{timeLabel}</span>
          )}
        </div>
        <ProgressBar label="Training progress" percent={percent} terminal={terminal} />
      </div>

      <RunActivity projectId={projectId} snapshot={snapshot} />
    </Card>
  );
}

function experimentTokenAccLabel(s: ExperimentSnapshot): string | null {
  const p = s.liveProgress;
  if (typeof p?.token_accuracy !== "number") return null;
  const train = `train ${(p.token_accuracy * 100).toFixed(1)}%`;
  return typeof p.eval_token_accuracy === "number"
    ? `${train} · val ${(p.eval_token_accuracy * 100).toFixed(1)}%`
    : train;
}

function experimentTimeLabel(s: ExperimentSnapshot): string | null {
  const elapsed = s.progress.elapsed_seconds;
  const eta = s.progress.eta_seconds;
  if (s.terminal) {
    return elapsed != null ? `${formatElapsed(elapsed)} total` : null;
  }
  return (
    [
      elapsed != null && elapsed > 0 ? `${formatElapsed(elapsed)} elapsed` : null,
      eta != null && eta > 0 ? `~${formatElapsed(eta)} left` : null,
    ]
      .filter(Boolean)
      .join(" · ") || null
  );
}

function experimentStatusLine(s: ExperimentSnapshot): string {
  const progress = s.liveProgress;
  const activity = progress?.activity ?? [];
  const status = s.job.status as string;
  const phase = progress?.phase || status;
  const isDeploying = status === "deploying";
  const isTraining =
    !isDeploying && !s.terminal && phase === "training" && s.progress.trained_steps != null;
  const downloadLine = downloadStatusLine(progress);
  if (s.terminal) {
    // A failed job's persisted reason beats the last provider log line — jobs
    // that die before emitting activity otherwise show a bare "Failed" chip.
    if (status === "failed" && s.job.errorMessage?.trim()) {
      return userFacingJobError(s.job.errorMessage);
    }
    return activity.at(-1)?.message ?? "";
  }
  if (isTraining) {
    const { trained_steps, total_steps } = s.progress;
    return `Training — step ${trained_steps}${total_steps != null ? ` / ${total_steps}` : ""}`;
  }
  if (downloadLine) return downloadLine;
  const label = isDeploying ? "Deploying" : progressPhaseLabel(progress, status);
  const latest = activity.at(-1)?.message;
  return latest
    ? `${label} — ${scrubInfraLeak(latest)}`
    : `${label} — waiting for provider activity…`;
}

function RunActivity({ snapshot, projectId }: { snapshot: ExperimentSnapshot; projectId: string }) {
  const [expanded, setExpanded] = useState(false);
  const cancelMutation = useCancelFinetuningJobMutation(projectId);
  const retryMutation = useRetryFinetuningJobMutation(projectId);

  const activity = snapshot.liveProgress?.activity ?? [];
  const status = snapshot.job.status as string;
  const statusLine = experimentStatusLine(snapshot);
  const deployedModelId = snapshot.job.deployedModelId;

  return (
    <div className="flex flex-col gap-1.5 border-t border-border/70 pt-3">
      {/* flex-wrap: the action cluster alone is wider than a ~340px card. */}
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2">
        <FtStatusBadge fallback="queued" solidProgress status={status} />
        {!snapshot.terminal && (
          <span aria-hidden className="size-1.5 shrink-0 animate-pulse rounded-xs bg-primary" />
        )}
        <p
          className="min-w-0 flex-1 truncate font-mono text-xs leading-none tabular-nums text-muted-foreground"
          title={statusLine}
        >
          {statusLine}
        </p>
        <div className="flex shrink-0 items-center gap-2">
          <ModelLiveAction
            capabilityId={snapshot.job.capability}
            jobs={[snapshot.job]}
            projectId={projectId}
          />
          {status === "succeeded" && deployedModelId && (
            <Button asChild size="sm">
              <Link
                params={{ modelId: deployedModelId }}
                search={{ projectId }}
                to="/inference/$modelId"
              >
                <Icon.inference className="size-3.5" />
                Inference
              </Link>
            </Button>
          )}
          {(status === "failed" || status === "cancelled") && (
            <Button
              disabled={retryMutation.isPending}
              onClick={() => retryMutation.mutate(snapshot.job.id)}
              size="sm"
              variant="secondary"
            >
              <Icon.refresh />
              Retry
            </Button>
          )}
          {!snapshot.terminal && (
            <ConfirmDialog
              cancelLabel="Keep running"
              confirmLabel="Cancel Job"
              description="Stops this fine-tuning job locally and on the training provider. Progress so far will be lost."
              destructive
              isPending={cancelMutation.isPending}
              onConfirm={() => cancelMutation.mutate(snapshot.job.id)}
              title="Cancel this job?"
              trigger={
                <Button size="sm" variant="secondary">
                  <Icon.close />
                  Cancel
                </Button>
              }
            />
          )}
          {activity.length > 0 && (
            <Button
              aria-expanded={expanded}
              aria-label={expanded ? "Hide activity log" : "Show activity log"}
              onClick={() => setExpanded((v) => !v)}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.chevronDown className={cn("transition-transform", expanded && "rotate-180")} />
            </Button>
          )}
        </div>
      </div>
      {/* column-reverse pins the newest line to the bottom as lines stream in. */}
      {expanded && activity.length > 0 && (
        <div
          aria-live="polite"
          className="flex max-h-40 flex-col-reverse overflow-y-auto rounded-md border border-border bg-wash-subtle px-2.5 py-1.5"
          role="log"
        >
          {[...activity].reverse().map((line, i) => {
            const message = scrubInfraLeak(line.message ?? "");
            // shrink-0 is load-bearing: in a column-reverse flex container with
            // max-h + overflow, default flex-shrink squashes rows below their
            // line height and the text renders overlapping instead of scrolling.
            return (
              <p
                className="shrink-0 truncate font-mono text-xs leading-5 text-muted-foreground"
                key={`${line.ts ?? "t"}-${i}`}
                title={message}
              >
                {line.ts != null && (
                  <span className="text-muted-foreground/50">
                    {new Date(line.ts).toLocaleTimeString()}{" "}
                  </span>
                )}
                {message}
              </p>
            );
          })}
        </div>
      )}
    </div>
  );
}

const EXPERIMENT_CHIP_CLASS =
  "inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2 text-xs font-medium text-muted-foreground transition-colors hover:border-border hover:bg-wash-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const EXPERIMENT_CHIP_SELECTED_CLASS = "border-primary/50 bg-primary/5 text-foreground";

function ExperimentChip({
  snapshot,
  selected,
  onSelect,
}: {
  snapshot: ExperimentSnapshot;
  selected: boolean;
  onSelect: () => void;
}) {
  const { job, progress } = snapshot;
  const stepLabel =
    !snapshot.terminal && progress.trained_steps != null
      ? `${progress.trained_steps}/${progress.total_steps ?? "—"}`
      : null;
  return (
    <button
      aria-pressed={selected}
      className={cn(EXPERIMENT_CHIP_CLASS, selected && EXPERIMENT_CHIP_SELECTED_CLASS)}
      onClick={onSelect}
      title={job.name || job.baseModel}
      type="button"
    >
      <ModelProviderChip
        className="h-auto border-0 bg-transparent p-0"
        compact
        model={job.baseModel}
      />
      {stepLabel && (
        <span className="font-mono text-xs tabular-nums text-muted-foreground">{stepLabel}</span>
      )}
    </button>
  );
}

function MonitorChartCard({
  title,
  subtitle,
  children,
  emptyHint,
  help,
  isLoading,
  className,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  emptyHint: string;
  help: string;
  isLoading?: boolean;
  className?: string;
}) {
  const hasContent = children != null && children !== false && children !== true;
  return (
    <Card className={cn("flex flex-col gap-2 p-3", className)}>
      <div className="flex items-center gap-2">
        <Icon.chart className="size-4 shrink-0 text-muted-foreground" />
        <h3 className="text-xs leading-none">{title}</h3>
        {subtitle && (
          <span className="truncate text-xs leading-none text-muted-foreground">{subtitle}</span>
        )}
        <span className="ml-auto flex shrink-0 items-center">
          <HelpTip label={title} text={help} />
        </span>
      </div>
      {isLoading ? (
        <Skeleton className="h-40" />
      ) : hasContent ? (
        children
      ) : (
        <div className="flex h-40 items-center justify-center text-center text-sm text-muted-foreground">
          {emptyHint}
        </div>
      )}
    </Card>
  );
}
