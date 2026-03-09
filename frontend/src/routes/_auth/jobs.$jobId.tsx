import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { WarningDiamond as AlertTriangle, ArrowLeft, Check as CheckCircle, Clock, Loader as Loader2, Cancel as XCircle } from "pixelarticons/react";

import apiClient from "@/client";
import { BacktestRecommendations, JobMetricColumn } from "@/components/jobs/JobCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SheetWrapper } from "@/components/sheet-wrapper";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/_auth/jobs/$jobId")({
  component: () => (
    <SheetWrapper>
      <JobDetailPage />
    </SheetWrapper>
  ),
});

const STATUS_CONFIG: Record<
  string,
  {
    variant: "default" | "secondary" | "destructive" | "success" | "warning";
    icon: React.ReactNode;
    label: string;
  }
> = {
  cancelled: { icon: <XCircle className="size-3.5" />, label: "Cancelled", variant: "default" },
  completed: { icon: <CheckCircle className="size-3.5" />, label: "Completed", variant: "success" },
  failed: { icon: <XCircle className="size-3.5" />, label: "Failed", variant: "destructive" },
  pending: { icon: <Clock className="size-3.5" />, label: "Pending", variant: "warning" },
  running: {
    icon: <Loader2 className="size-3.5 animate-spin" />,
    label: "Running",
    variant: "secondary",
  },
  skipped: { icon: <AlertTriangle className="size-3.5" />, label: "Skipped", variant: "default" },
};

const JOB_TYPE_LABELS: Record<string, string> = {
  agent_discovery: "Agent Discovery",
  judge_scoring: "LLM Judge Scoring",
  model_backtesting: "Model Backtesting",
  prompt_tuning: "Prompt Tuning",
  scoring: "LLM Judge Scoring",
  template_extraction: "Template Extraction",
};

const VERDICT_LABELS: Record<string, { label: string; variant: "success" | "warning" | "secondary" }> = {
  current_is_best: { label: "Current model is best", variant: "success" },
  switch_recommended: { label: "Switch recommended", variant: "warning" },
  consider_top_performer: { label: "Consider top performer", variant: "secondary" },
  insufficient_data: { label: "Insufficient data", variant: "secondary" },
};

function formatDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    year: "numeric",
  });
}

function humanSlug(slug?: string | null): string {
  if (!slug) return "—";
  return slug.replace(/-/g, " ").replace(/_/g, " ");
}

function fmtScore(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

function fmtMs(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} ms`;
}

function fmtCost(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toFixed(4)}`;
}

function fmtRate(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function BacktestResultContent({ result }: { result: Record<string, unknown> }) {
  const currentModel = result.current_model as string | undefined;
  const modelsTested = result.models_tested as number | undefined;
  const spansTested = result.spans_tested as number | undefined;
  const spansSucceeded = result.spans_succeeded as number | undefined;
  const spansFailed = result.spans_failed as number | undefined;

  const recommendations = result.recommendations as Record<string, unknown> | undefined;
  const currentModelMetrics = recommendations?.current_model as Record<string, unknown> | undefined;
  const verdict = recommendations?.verdict as string | undefined;
  const summary = recommendations?.summary as string | undefined;

  const modelMetrics = result.model_metrics as Record<string, Record<string, number>> | undefined;
  const modelsList = result.models_list as string[] | undefined;

  const verdictCfg = verdict ? VERDICT_LABELS[verdict] : undefined;

  return (
    <div className="space-y-4">
      {/* Overview stats */}
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Backtesting Overview</h2>
            {verdictCfg && (
              <Badge variant={verdictCfg.variant}>{verdictCfg.label}</Badge>
            )}
          </div>
          {summary && (
            <p className="text-sm text-muted-foreground leading-relaxed mt-1">{summary}</p>
          )}
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-8">
            <StatBlock label="Current Model" value={currentModel ?? "—"} mono />
            <StatBlock label="Models Tested" value={String(modelsTested ?? 0)} />
            <StatBlock label="Spans Tested" value={String(spansTested ?? 0)} />
            <StatBlock label="Spans Succeeded" value={String(spansSucceeded ?? 0)} />
            {(spansFailed ?? 0) > 0 && (
              <StatBlock label="Spans Failed" value={String(spansFailed)} error />
            )}
          </div>
        </CardContent>
      </Card>

      {/* Current model baseline */}
      {currentModelMetrics && (
        <Card>
          <CardHeader>
            <h2 className="text-base font-semibold">Current Model Baseline</h2>
          </CardHeader>
          <CardContent>
            <div className="rounded border border-border bg-muted/30 p-4">
              <div className="flex items-center gap-3 mb-3">
                <span className="text-sm font-bold text-foreground">
                  {String(currentModelMetrics.name ?? currentModel ?? "—")}
                </span>
                <Badge variant="secondary">baseline</Badge>
              </div>
              <div className="flex flex-wrap gap-6">
                <JobMetricColumn
                  format="percent"
                  label="Avg Score"
                  newVal={currentModelMetrics.avg_eval_score as number | undefined}
                  oldVal={undefined}
                />
                <JobMetricColumn
                  format="ms"
                  label="Avg Latency"
                  invertColor
                  newVal={currentModelMetrics.avg_latency_ms as number | undefined}
                  oldVal={undefined}
                />
                <JobMetricColumn
                  format="cost"
                  label="Avg Cost / Request"
                  invertColor
                  newVal={currentModelMetrics.avg_cost_per_request as number | undefined}
                  oldVal={undefined}
                />
                {currentModelMetrics.scored_span_count != null && (
                  <StatBlock
                    label="Scored Spans"
                    value={String(currentModelMetrics.scored_span_count)}
                  />
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Per-model results table */}
      {modelMetrics && Object.keys(modelMetrics).length > 0 && (
        <Card>
          <CardHeader>
            <h2 className="text-base font-semibold">
              Model Comparison
              <span className="ml-2 text-sm font-normal text-muted-foreground">
                ({Object.keys(modelMetrics).length} model{Object.keys(modelMetrics).length !== 1 ? "s" : ""})
              </span>
            </h2>
          </CardHeader>
          <CardContent>
            <ModelComparisonTable
              modelMetrics={modelMetrics}
              modelsList={modelsList}
              currentModel={currentModel}
              baselineScore={currentModelMetrics?.avg_eval_score as number | undefined}
              baselineLatency={currentModelMetrics?.avg_latency_ms as number | undefined}
              baselineCost={currentModelMetrics?.avg_cost_per_request as number | undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* Models list (fallback when no metrics available — e.g. pending/running jobs) */}
      {!modelMetrics && modelsList && modelsList.length > 0 && (
        <Card>
          <CardHeader>
            <h2 className="text-base font-semibold">Models to Test</h2>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {modelsList.map((m) => (
                <Badge key={m} variant="outline" className="font-mono text-xs">
                  {m}
                </Badge>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Pending job — show models from parameters */}
      {!modelMetrics && !modelsList && result.parameters && (
        <PendingBacktestInfo parameters={result.parameters as Record<string, unknown>} />
      )}

      {/* Recommendations detail — only shown when there are actual candidate models */}
      {recommendations && typeof recommendations === "object" && (
        recommendations.top_performer || recommendations.fastest ||
        recommendations.cheapest || recommendations.best_overall
      ) && (
        <Card>
          <CardHeader>
            <h2 className="text-base font-semibold">Detailed Recommendations</h2>
          </CardHeader>
          <CardContent>
            <BacktestRecommendations data={recommendations} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ModelComparisonTable({
  modelMetrics,
  modelsList,
  currentModel,
  baselineScore,
  baselineLatency,
  baselineCost,
}: {
  modelMetrics: Record<string, Record<string, number>>;
  modelsList?: string[];
  currentModel?: string;
  baselineScore?: number;
  baselineLatency?: number;
  baselineCost?: number;
}) {
  const models = modelsList ?? Object.keys(modelMetrics);
  const sorted = [...models].filter((m) => modelMetrics[m]).sort((a, b) => {
    const aScore = modelMetrics[a]?.avg_eval_score ?? 0;
    const bScore = modelMetrics[b]?.avg_eval_score ?? 0;
    return bScore - aScore;
  });

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead className="text-right">Avg Score</TableHead>
            <TableHead className="text-right">vs Baseline</TableHead>
            <TableHead className="text-right">Avg Latency</TableHead>
            <TableHead className="text-right">Avg Cost</TableHead>
            <TableHead className="text-right">Success Rate</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sorted.map((modelName) => {
            const m = modelMetrics[modelName];
            if (!m) return null;
            const isCurrent = currentModel && modelName.startsWith(currentModel);
            const scoreDelta = baselineScore != null && m.avg_eval_score != null
              ? m.avg_eval_score - baselineScore
              : null;

            return (
              <TableRow key={modelName} className={cn(isCurrent && "bg-muted/30")}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm font-medium">{modelName}</span>
                    {isCurrent && (
                      <Badge variant="secondary" className="text-[0.6rem] px-1.5 py-0">current</Badge>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-right font-semibold tabular-nums">
                  {fmtScore(m.avg_eval_score)}
                </TableCell>
                <TableCell className="text-right">
                  {scoreDelta != null && (
                    <span
                      className={cn(
                        "inline-flex items-center rounded-sm px-1.5 py-0.5 text-[0.68rem] font-semibold tabular-nums",
                        scoreDelta > 0.005
                          ? "bg-emerald-500/10 text-emerald-600 dark:bg-emerald-400/10 dark:text-emerald-400"
                          : scoreDelta < -0.005
                            ? "bg-red-500/10 text-red-600 dark:bg-red-400/10 dark:text-red-400"
                            : "bg-muted text-muted-foreground"
                      )}
                    >
                      {scoreDelta >= 0 ? "+" : ""}{(scoreDelta * 100).toFixed(1)}pp
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmtMs(m.avg_latency_ms)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmtCost(m.avg_cost_per_request)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {fmtRate(m.success_rate)}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function PendingBacktestInfo({ parameters }: { parameters: Record<string, unknown> }) {
  const models = parameters.models as string[] | undefined;
  const spanCount = parameters.span_count as number | undefined;

  if (!models || models.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <h2 className="text-base font-semibold">Models Queued for Testing</h2>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {models.map((m) => (
              <Badge key={m} variant="outline" className="font-mono text-xs">
                {m}
              </Badge>
            ))}
          </div>
          {spanCount != null && (
            <p className="text-xs text-muted-foreground">
              Will test against up to {spanCount} historical spans
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function StatBlock({
  label,
  value,
  mono,
  error,
}: {
  label: string;
  value: string;
  mono?: boolean;
  error?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "text-sm font-bold",
          mono && "font-mono",
          error ? "text-destructive" : "text-foreground"
        )}
      >
        {value}
      </span>
    </div>
  );
}

function JobResultContent({ result }: { result: Record<string, unknown> }) {
  const recommendations = result.recommendations as Record<string, unknown> | undefined;
  const hasRecommendations =
    recommendations && typeof recommendations === "object" && recommendations.summary;

  const complexKeys = new Set(["status", "raw", "comparison_test", "recommendations"]);
  const simpleEntries = Object.entries(result).filter(([k]) => !complexKeys.has(k));

  return (
    <div className="space-y-4">
      {simpleEntries.length > 0 && (
        <div className="flex flex-wrap gap-6">
          {simpleEntries.map(([k, v]) => {
            let display: string;
            if (v == null) {
              display = "—";
            } else if (typeof v === "object") {
              display = JSON.stringify(v);
              if (display.length > 60) display = `${display.slice(0, 57)}…`;
            } else {
              display = String(v);
            }
            return (
              <div className="min-w-[80px]" key={k}>
                <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
                  {k.replace(/_/g, " ")}
                </span>
                <p className="mt-0.5 text-sm font-semibold text-foreground break-all">{display}</p>
              </div>
            );
          })}
        </div>
      )}

      {hasRecommendations && (
        <div className="rounded-md border border-border bg-muted/30 p-4 space-y-3">
          <p className="text-[0.72rem] font-semibold uppercase tracking-widest text-muted-foreground">
            Recommendations
          </p>
          <BacktestRecommendations data={recommendations} />
        </div>
      )}
    </div>
  );
}

function JobDetailPage() {
  const { jobId } = Route.useParams();

  const {
    data: job,
    isLoading,
    error,
  } = useQuery({
    queryFn: () => apiClient.jobs.getJobApiV1JobsJobIdGet({ jobId }),
    queryKey: ["job", jobId],
    refetchInterval: (query) => {
      const d = query.state.data;
      return d?.status === "running" ? 3000 : false;
    },
  });

  const cfg = job ? (STATUS_CONFIG[job.status] ?? STATUS_CONFIG.pending) : null;
  const isBacktesting = job?.jobType === "model_backtesting";
  const hasResult = job?.result && Object.keys(job.result).length > 0;

  return (
    <div className="space-y-6 pb-8">
      <div className="flex items-center gap-4">
        <Button asChild size="sm" variant="ghost">
          <Link search={(prev) => prev} to="..">
            <ArrowLeft className="size-4" />
          </Link>
        </Button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-8 animate-spin text-muted-foreground" />
        </div>
      )}

      {error && <p className="text-destructive">Failed to load job: {(error as Error).message}</p>}

      {!isLoading && !error && job && (
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-4">
                <h2 className="text-base font-semibold">Job Details</h2>
                {cfg && (
                  <span className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-sm font-medium">
                    {cfg.icon}
                    {cfg.label}
                  </span>
                )}
              </div>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[35%]">Property</TableHead>
                    <TableHead>Value</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Job ID
                    </TableCell>
                    <TableCell className="font-mono text-sm">{job.jobId}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">Type</TableCell>
                    <TableCell>
                      {JOB_TYPE_LABELS[job.jobType ?? ""] ?? humanSlug(job.jobType ?? undefined)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">Agent</TableCell>
                    <TableCell>{job.promptDisplayName ?? (humanSlug(job.promptSlug ?? undefined) || "All agents")}</TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Started
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatDate(job.createdAt ?? undefined)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Updated
                    </TableCell>
                    <TableCell className="text-sm">
                      {formatDate(job.updatedAt ?? undefined)}
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell className="font-mono text-xs text-muted-foreground">
                      Triggered By
                    </TableCell>
                    <TableCell className="text-sm">
                      {job.triggeredBy === "scheduled" ? "System" : "User"}
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          {isBacktesting && hasResult ? (
            <BacktestResultContent result={job.result as Record<string, unknown>} />
          ) : hasResult ? (
            <Card>
              <CardHeader>
                <h2 className="text-base font-semibold">Result</h2>
              </CardHeader>
              <CardContent>
                <JobResultContent result={job.result as Record<string, unknown>} />
              </CardContent>
            </Card>
          ) : null}
        </div>
      )}
    </div>
  );
}
