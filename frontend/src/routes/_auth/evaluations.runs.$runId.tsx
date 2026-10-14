import { type ReactNode, useMemo, useState } from "react";

import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { UsedVersionChip } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import { MetricList, metricResults } from "@/components/evaluations/metric-list";
import { renderPayload } from "@/components/evaluations/payload-format";
import { ProbabilityEvaluationPage } from "@/components/evaluations/probability-evaluation-page";
import {
  asRunSummary,
  metricEntries,
  RunComparison,
  ScoreChip,
} from "@/components/evaluations/run-comparison";
import { ResultsChart } from "@/components/evaluations/run-results-chart";
import { StatusBadge } from "@/components/evaluations/runs-table";
import { HeaderStat } from "@/components/finetuning/finetuning-chrome";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { DetailErrorState } from "@/components/route-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { TooltipProvider } from "@/components/ui/tooltip";
import { usedVersionOf } from "@/hooks/use-datasets";
import { useElapsedSeconds } from "@/hooks/use-elapsed-seconds";
import {
  isActiveRunStatus,
  useEvalRunGroupQuery,
  useEvalRunQuery,
  useEvalRunRowQuery,
  useEvalRunRowsQuery,
  useRelaunchEvalRunMutation,
} from "@/hooks/use-evaluations";
import { formatElapsed } from "@/lib/formatters";
import { evaluationRunSearchSchema } from "@/lib/schemas";
import { cn } from "@/lib/utils";
import type { EvalRun, EvalRunDetail, EvalRunRow } from "@/openapi";

export const Route = createFileRoute("/_auth/evaluations/runs/$runId")({
  component: EvaluationRunPage,
  validateSearch: evaluationRunSearchSchema,
});

const MODE_LABEL: Record<string, string> = {
  generate: "Replay",
  provided: "Provided",
  recorded: "Recorded",
};

function modeLabel(run: Pick<EvalRun, "mode" | "params">) {
  const strategy =
    run.params && typeof run.params === "object" && "generation_strategy" in run.params
      ? run.params.generation_strategy
      : "";
  if (run.mode === "generate" && strategy === "single_completion") return "Single completion";
  return MODE_LABEL[run.mode] ?? run.mode;
}

const PHASE_LABEL: Record<string, string> = {
  generating: "Generating",
  preparing: "Preparing",
  scoring: "Scoring",
};

const targetLabel = (run: Pick<EvalRun, "label" | "mode">) =>
  run.label || MODE_LABEL[run.mode] || run.mode;

function EvaluationRunPage() {
  const { runId } = Route.useParams();
  const { format } = Route.useSearch();
  return format === "decision" ? <ProbabilityEvaluationPage id={runId} /> : <EvalRunDetailPage />;
}

function EvalRunDetailPage() {
  const { runId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const { data: run, isLoading, error } = useEvalRunQuery(runId);
  const group = useEvalRunGroupQuery(projectId, run?.group || undefined);
  const [view, setView] = useState<"results" | "compare">("results");

  const header = (
    <PageHeader
      description="Scores and per-row results for this run."
      icon={
        <Icon.evaluations
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] invert"
        />
      }
      title={run?.name || "Evaluation run"}
    />
  );

  if (error && !run) {
    return (
      <PageShell header={header} variant="full">
        <DetailErrorState
          action={
            <Button asChild size="sm" variant="secondary">
              {/* `search={true}` keeps the list's filters, which live only in this
                  URL. Not `(prev) => prev`: a cross-route `prev` is typed as every
                  route's search unioned, widening `run_status` past the enum. */}
              <Link search={true} to="/evaluations">
                Back to evaluations
              </Link>
            </Button>
          }
          error={error}
          fallback="Couldn't load this run."
        />
      </PageShell>
    );
  }

  if (isLoading || !run) {
    return (
      <PageShell header={header} variant="full">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton className="h-20" key={i} />
          ))}
        </div>
      </PageShell>
    );
  }

  const active = isActiveRunStatus(run.status);
  const siblings = group.data?.results ?? [];
  const hasBaseline = !run.isBaseline && siblings.some((r) => r.isBaseline && r.id !== run.id);

  return (
    <TooltipProvider delayDuration={200}>
      <PageShell header={header} variant="full">
        {run.error ? (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {run.error}
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto">
          <RunHeaderCard active={active} projectId={projectId} run={run} />

          {siblings.length > 1 && (
            <GroupStrip current={run.id} projectId={projectId} runs={siblings} />
          )}

          {!run.isBaseline && siblings.length > 1 && (
            <Tabs
              className="w-auto self-start"
              onValueChange={(v) => setView(v as "results" | "compare")}
              value={view}
            >
              <TabsList aria-label="Run view">
                <TabsTrigger value="results">Results</TabsTrigger>
                <TabsTrigger value="compare">Baseline comparison</TabsTrigger>
              </TabsList>
            </Tabs>
          )}

          {view === "compare" && !run.isBaseline ? (
            <RunComparison active={active} hasBaseline={hasBaseline} runId={runId} />
          ) : (
            <>
              <ResultsChart
                active={active}
                run={run}
                runs={siblings.length > 0 ? siblings : [run]}
              />
              <SummarySection run={run} />
              <RowsSection active={active} projectId={projectId} runId={runId} />
            </>
          )}
        </div>
      </PageShell>
    </TooltipProvider>
  );
}

function RunElapsed({ run }: { run: EvalRunDetail }) {
  const running = isActiveRunStatus(run.status);
  const live = useElapsedSeconds(running ? run.createdAt : undefined);
  const seconds = running
    ? live
    : Math.floor(((run.completedAt ?? run.updatedAt).getTime() - run.createdAt.getTime()) / 1000);
  if (seconds == null || seconds <= 0) return null;
  return (
    <span className="tabular-nums text-xs text-muted-foreground">{formatElapsed(seconds)}</span>
  );
}

function Divider() {
  return <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />;
}

function RelaunchButton({ runId, visible }: { runId: string; visible: boolean }) {
  const relaunch = useRelaunchEvalRunMutation();
  if (!visible) return null;
  return (
    <Button
      disabled={relaunch.isPending}
      onClick={() =>
        relaunch.mutate(runId, {
          onSuccess: () => toast.success("Re-running"),
        })
      }
      size="sm"
      variant="secondary"
    >
      <Icon.refresh />
      Re-run
    </Button>
  );
}

function replayFacts(metadata: unknown): [string, string][] {
  if (!metadata || typeof metadata !== "object") return [];
  const record = metadata as Record<string, unknown>;
  if (record.generation_strategy == null && record.replay_misses == null && record.steps == null) {
    return [];
  }
  const facts: [string, string][] = [];
  if (typeof record.generation_strategy === "string") {
    facts.push([
      "Strategy",
      record.generation_strategy === "replay" ? "Replay" : record.generation_strategy,
    ]);
  }
  if (typeof record.steps === "number") facts.push(["Steps", String(record.steps)]);
  if (typeof record.replay_misses === "number") {
    facts.push(["Replay misses", String(record.replay_misses)]);
  }
  if (typeof record.latency_ms === "number")
    facts.push(["Latency", `${Math.round(record.latency_ms)} ms`]);
  if (typeof record.cost === "number") facts.push(["Cost", record.cost.toFixed(4)]);
  return facts;
}

function RunHeaderCard({
  run,
  projectId,
  active,
}: {
  run: EvalRunDetail;
  projectId: string | undefined;
  active: boolean;
}) {
  const progress = run.progress;
  const model = run.resolvedModel || run.modelName;
  return (
    <Card className="flex flex-col gap-4 p-4">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="text-xs text-foreground">Target</h3>
          <div className="flex flex-wrap items-center gap-2">
            {model ? (
              <ModelProviderChip model={model} />
            ) : (
              <span className="text-sm font-medium">{targetLabel(run)}</span>
            )}
            <Badge size="chip" variant="neutral">
              {modeLabel(run)}
            </Badge>
            {run.isBaseline && (
              <Badge size="chip" variant="neutral">
                Baseline
              </Badge>
            )}
          </div>
        </div>
        <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">Created</dt>
            <dd className="font-medium">
              <DateTime value={run.createdAt} />
            </dd>
          </div>
          {run.completedAt && (
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Completed</dt>
              <dd className="font-medium">
                <DateTime value={run.completedAt} />
              </dd>
            </div>
          )}
        </dl>
      </div>

      <div className="flex flex-wrap items-stretch gap-y-3 border-t border-border/70 pt-3.5">
        {run.capabilityId && (
          <>
            <HeaderStat label="Capability">
              <EntityRef id={run.capabilityId} kind="capability" name={run.capabilityName} />
            </HeaderStat>
            <Divider />
          </>
        )}
        {run.dataset && (
          <>
            <HeaderStat label="Dataset">
              <span className="inline-flex items-center gap-1.5">
                <EntityRef
                  id={run.dataset}
                  kind="dataset"
                  name={run.datasetName}
                  projectId={projectId}
                />
                <UsedVersionChip info={usedVersionOf(run.cellInfo)} />
              </span>
            </HeaderStat>
            <Divider />
          </>
        )}
        {run.maxItems > 0 && (
          <>
            <HeaderStat label="Max items">{run.maxItems}</HeaderStat>
            <Divider />
          </>
        )}
        <HeaderStat label="Matrix rows">
          {Array.isArray(run.matrix) ? run.matrix.length : 0}
        </HeaderStat>
        {run.judgeModel && (
          <>
            <Divider />
            <HeaderStat label="Judge">
              <span className="font-mono text-xs">{run.judgeModel}</span>
            </HeaderStat>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2.5 border-t border-border/70 pt-3">
        <StatusBadge status={run.status} />
        {active && progress && (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {PHASE_LABEL[progress.phase] ?? progress.phase} · {progress.done}/{progress.rows}
            {progress.errored > 0 && ` · ${progress.errored} errored`}
          </span>
        )}
        <RunElapsed run={run} />
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <RelaunchButton runId={run.id} visible={!active} />
          {run.status === "completed" && (
            <Button asChild size="sm">
              <Link
                search={{
                  optimize: true,
                  projectId,
                  ...(run.capabilityId ? { capabilityId: run.capabilityId } : {}),
                  ...(run.dataset ? { datasetId: run.dataset } : {}),
                }}
                to="/optimiser"
              >
                <Icon.optimiser />
                Optimise
              </Link>
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function GroupStrip({
  runs,
  current,
  projectId,
}: {
  runs: EvalRun[];
  current: string;
  projectId: string | undefined;
}) {
  return (
    <section aria-label="Targets in this group" className="flex flex-wrap gap-2">
      {runs.map((r) => (
        <Link
          className={cn(
            "flex items-center gap-2 rounded-md border px-3 py-2 text-sm transition-colors hover:bg-wash-subtle",
            r.id === current ? "border-primary/60 bg-wash-subtle" : "border-border"
          )}
          key={r.id}
          params={{ runId: r.id }}
          search={{ projectId }}
          to="/evaluations/runs/$runId"
        >
          <span className="max-w-48 truncate">{targetLabel(r)}</span>
          {r.isBaseline && (
            <Badge size="chip" variant="neutral">
              Baseline
            </Badge>
          )}
          {isActiveRunStatus(r.status) ? (
            <StatusBadge status={r.status} />
          ) : (
            <ScoreChip value={asRunSummary(r.summary).score} />
          )}
        </Link>
      ))}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border bg-wash-subtle px-4 py-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="font-mono text-lg font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function SummarySection({ run }: { run: EvalRunDetail }) {
  const summary = asRunSummary(run.summary);
  const metrics = metricEntries(summary);
  const classMetrics = Object.entries(summary.class_metrics ?? {}).filter(
    ([, value]) => typeof value === "number"
  ) as [string, number][];
  if (!run.summary) {
    return isActiveRunStatus(run.status) ? <LoadingState label="Scoring…" /> : null;
  }
  return (
    <section aria-label="Summary" className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Score" value={<ScoreChip value={summary.score} />} />
        <Stat label="Rows" value={summary.rows ?? 0} />
        <Stat label="Scored" value={summary.scored ?? 0} />
        <Stat label="Errored" value={summary.errored ?? 0} />
        <Stat label="Degraded" value={summary.degraded ?? 0} />
      </div>
      {classMetrics.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {classMetrics.map(([name, value]) => (
            <Stat
              key={name}
              label={name.replaceAll("_", " ")}
              value={value <= 1 ? `${Math.round(value * 100)}%` : value}
            />
          ))}
        </div>
      )}
      {metrics.length > 0 && (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Metric</TableHead>
                <TableHead>Type</TableHead>
                <TableHead className="text-center">Mean</TableHead>
                <TableHead className="text-center">Scored</TableHead>
                <TableHead className="text-center">Failed</TableHead>
                <TableHead className="text-center">Errors</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {metrics.map(([name, metric]) => (
                <TableRow key={name}>
                  <TableCell className="text-sm font-medium">{name}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {metric.type === "llm" ? "LLM judge" : "Deterministic"}
                  </TableCell>
                  <TableCell className="text-center">
                    <ScoreChip value={metric.mean} />
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums">
                    {metric.n}
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums">
                    {metric.failed ?? 0}
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums">
                    {metric.errors ?? 0}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

type RowFilter = "all" | "failing" | "errored";

const FAIL_BELOW = 0.5;

function rowMatches(row: EvalRunRow, filter: RowFilter) {
  if (filter === "errored") return !!row.error || row.degraded;
  if (filter === "failing") return row.score != null && row.score < FAIL_BELOW;
  return true;
}

function RowsSection({
  runId,
  active,
  projectId,
}: {
  runId: string;
  active: boolean;
  projectId: string | undefined;
}) {
  const { data, isLoading } = useEvalRunRowsQuery(runId, active);
  const [filter, setFilter] = useState<RowFilter>("all");
  const [openId, setOpenId] = useState<number | undefined>();
  const rows = useMemo(
    () => (data?.results ?? []).filter((row) => rowMatches(row, filter)),
    [data, filter]
  );

  return (
    <section aria-label="Rows" className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">
          Rows <span className="font-mono text-muted-foreground">{data?.count ?? 0}</span>
        </h3>
        <Select onValueChange={(v) => setFilter(v as RowFilter)} value={filter}>
          <SelectTrigger aria-label="Filter rows" className="w-40" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All rows</SelectItem>
            <SelectItem value="failing">Failing</SelectItem>
            <SelectItem value="errored">Errored or degraded</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {isLoading ? (
        <LoadingState />
      ) : rows.length === 0 ? (
        <EmptyState
          className="rounded-md border border-dashed bg-wash-subtle"
          icon={Icon.evaluations}
          iconClassName="invert [image-rendering:pixelated]"
          size="section"
          title="No rows"
        />
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-16">#</TableHead>
                <TableHead className="w-24">State</TableHead>
                <TableHead className="w-24 text-center">Score</TableHead>
                <TableHead>Failing metrics</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const failing = metricResults(row.metrics).filter(
                  ([, m]) => !!m.error || (m.score != null && m.score < FAIL_BELOW)
                );
                return (
                  <TableRow
                    className="cursor-pointer"
                    key={row.id}
                    onClick={() => setOpenId(row.id)}
                  >
                    <TableCell className="font-mono text-xs tabular-nums">{row.rowIndex}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {row.error ? (
                        <span className="text-destructive">Error</span>
                      ) : row.degraded ? (
                        <span className="text-warning">Degraded</span>
                      ) : (
                        row.state
                      )}
                    </TableCell>
                    <TableCell className="text-center">
                      <ScoreChip value={row.score} />
                    </TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground">
                      {row.error || failing.map(([name]) => name).join(", ") || "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
      <RowSheet id={openId} onClose={() => setOpenId(undefined)} projectId={projectId} />
    </section>
  );
}

function ReplayFacts({ metadata }: { metadata: unknown }) {
  const facts = replayFacts(metadata);
  if (facts.length === 0) return null;
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-1 rounded-md border border-border bg-wash-subtle px-3 py-2 text-xs">
      {facts.map(([label, value]) => (
        <div className="flex justify-between gap-2" key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="font-mono tabular-nums">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Payload({ label, value }: { label: string; value: unknown }) {
  if (value == null || value === "") return null;
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-border bg-wash-subtle p-3 font-mono text-xs">
        {renderPayload(text, "formatted")}
      </pre>
    </div>
  );
}

function RowSheet({
  id,
  projectId,
  onClose,
}: {
  id: number | undefined;
  projectId: string | undefined;
  onClose: () => void;
}) {
  const { data: row, isLoading } = useEvalRunRowQuery(id);
  return (
    <Sheet onOpenChange={(open) => !open && onClose()} open={id !== undefined}>
      <SheetContent size="lg">
        <SheetHeader>
          <SheetTitle>Row {row?.rowIndex ?? ""}</SheetTitle>
          <SheetDescription>
            {row?.score != null ? `Score ${Math.round(row.score * 100)}%` : "Not scored"}
          </SheetDescription>
        </SheetHeader>
        <SheetBody className="flex flex-col gap-4">
          {isLoading || !row ? (
            <LoadingState />
          ) : (
            <>
              {row.error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {row.error}
                </div>
              )}
              {row.degraded && row.degradedReason && (
                <div className="rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">
                  {row.degradedReason}
                </div>
              )}
              <MetricList metrics={row.metrics} />
              <ReplayFacts metadata={row.metadata} />
              <Payload label="Input" value={row.input} />
              <Payload label="Reference" value={row.reference} />
              <Payload label="Output" value={row.output} />
              {row.sourceTraceId && (
                <EntityRef
                  id={row.sourceTraceId}
                  kind="trace"
                  name={row.sourceTraceId.slice(0, 12)}
                  projectId={projectId}
                />
              )}
              <span className="text-xs text-muted-foreground">
                {row.finishedAt ? <DateTime value={row.finishedAt} /> : null}
              </span>
            </>
          )}
        </SheetBody>
      </SheetContent>
    </Sheet>
  );
}
