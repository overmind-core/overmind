import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";

import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { UsedVersionChip } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import {
  groupSampleRows,
  matchesSearch,
  scoreVerdict,
  sortRows,
  type DatapointRow,
  type VerdictFilter,
} from "@/components/evaluations/datapoint-filter";
import { EvalWinnerCallout, PerModelOps } from "@/components/evaluations/eval-results-overview";
import { renderPayload, type ViewMode } from "@/components/evaluations/payload-format";
import { RunComparison } from "@/components/evaluations/run-comparison";
import { StatusBadge } from "@/components/evaluations/runs-table";
import {
  type DistributionScore,
  ScoreDistributions,
} from "@/components/evaluations/score-distributions";
import {
  type ScoreLike,
  type ScoreState,
  scoreState,
} from "@/components/evaluations/score-reasoning";
import { FailureCard } from "@/components/failure-card";
import { isFinetunedServingId } from "@/components/finetuning/finetuned-serving-id";
import { HeaderStat, HelpTip } from "@/components/finetuning/finetuning-chrome";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { DetailErrorState } from "@/components/route-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Chip } from "@/components/ui/chip";
import { CountChip } from "@/components/ui/count-chip";
import { CreditsAmount } from "@/components/ui/credits";
import { DateTime } from "@/components/ui/datetime";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { SortableHeader, type SortState } from "@/components/ui/sortable-header";
import { LoadingState, Spinner } from "@/components/ui/spinner";
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
  useCancelEvalRunMutation,
  useEvalRunComparisonQuery,
  useEvalRunQuery,
  useEvalSampleQuery,
  useEvalSamplesQuery,
  useEvalScoresQuery,
  useRelaunchEvalRunMutation,
} from "@/hooks/use-evaluations";
import { scoreChipClass, scoreFillClass, TONE_CHIP } from "@/lib/colors";
import { formatElapsed } from "@/lib/formatters";
import { humanizeKey } from "@/lib/label-case";
import { notify } from "@/lib/notify";
import { projectIdSearchSchema } from "@/lib/schemas";
import { PROSE } from "@/lib/typography";
import { cn, paginationFromPageLimit, paginationItems, scorePct } from "@/lib/utils";
import type {
  EvalRunEvaluatorStat,
  EvalRunOperationalStat,
  EvalRunProgress,
  EvalRunVariantProgress,
  EvalSampleList,
} from "@/openapi";

const DATA_SOURCE_LABEL: Record<string, string> = {
  dataset: "Dataset",
  trace_filter: "Trace filter",
};

export const Route = createFileRoute("/_auth/evaluations/runs/$runId")({
  component: EvalRunDetailPage,
  validateSearch: projectIdSearchSchema,
});

// Per-turn judge dimension columns (see per_turn_judge.py) — their presence is
// what marks a run as teacher-forced replay.
const TURN_DIM_RE = /:\s*(progress|turn match|tool choice|args grounded|safety)\s*$/i;

// True when every variant runs in "existing" mode: captured traces are graded
// with no golden reference, so an expected_output on the dataset row is not a
// grading target here and showing it misleads.
const TraceScoringRunContext = createContext(false);

function ViewModeToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
}) {
  return (
    <div
      aria-label="Toggle payload format"
      className="inline-flex items-center gap-0.5 rounded-sm border border-border/60 p-0.5"
      role="group"
    >
      {(["formatted", "raw"] as const).map((option) => (
        <button
          aria-pressed={value === option}
          className={cn(
            "rounded-sm px-1.5 py-0.5 text-xs font-medium capitalize transition-colors",
            value === option
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
          key={option}
          onClick={() => onChange(option)}
          type="button"
        >
          {option}
        </button>
      ))}
    </div>
  );
}

interface MetricCell {
  mean: number | null;
  /**
   * Ratio of sums, present only for proportional evaluators. Their rows carry
   * different denominators, so the mean of per-row fractions favours whichever
   * model writes least — prefer this whenever it is set.
   */
  pooled?: number | null;
  n: number;
  pass_rate: number | null;
  delta?: number;
  regression?: boolean;
  ci_low?: number | null;
  ci_high?: number | null;
  separable?: boolean;
}
interface VariantSummary {
  label: string;
  metrics: Record<string, MetricCell>;
}
interface ErrorCounts {
  total: number;
  errored: number;
  error_rate: number;
  evaluator_errors?: number;
  by_variant: Record<
    string,
    { label: string; total: number; errored: number; evaluator_errors?: number }
  >;
}
interface TrustVariant {
  label: string;
  total: number;
  degraded: number;
  reasons: Record<string, number>;
  replay_misses: number;
  replay_fuzzy_hits: number;
}
interface TrustSignal {
  total: number;
  degraded: number;
  trusted: number;
  degraded_rate: number;
  by_variant: Record<string, TrustVariant>;
}
interface ApplicabilitySignal {
  total: number;
  by_variant: Record<string, { label: string; not_applicable: number }>;
  by_evaluator: Record<string, number>;
}
interface ItemRate {
  id: string;
  passed: number;
  total: number;
  pass_rate: number;
}
interface RunSummary {
  metrics?: string[];
  variants?: Record<string, VariantSummary>;
  baseline_variant_id?: string | null;
  error_counts?: ErrorCounts;
  trust?: TrustSignal;
  applicability?: ApplicabilitySignal;
  /** variant id -> evaluator name -> per-checklist-item pass rates. */
  items?: Record<string, Record<string, ItemRate[]>>;
}
interface ComparisonPayload {
  run_id: string;
  status: string;
  summary: RunSummary;
  variants: Array<{ id: string; label: string }>;
  progress?: EvalRunProgress | null;
}

const humanizeMetricName = humanizeKey;

function EvalRunDetailPage() {
  const { runId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const { data: run, isLoading, error } = useEvalRunQuery(runId);
  const relaunch = useRelaunchEvalRunMutation(projectId);
  const cancel = useCancelEvalRunMutation(projectId);
  const [view, setView] = useState<"results" | "compare">("results");

  const header = (
    <PageHeader
      description="Scores, variants and per-sample results for this run."
      icon={
        <Icon.evaluations
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] dark:invert"
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
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton className="h-20" key={i} />
          ))}
        </div>
      </PageShell>
    );
  }

  const runStatus = run.status ?? "pending";
  const isTerminal = runStatus !== "running" && runStatus !== "pending";
  const isTraceScoringRun =
    (run.variants ?? []).length > 0 && run.variants.every((v) => v.mode === "existing");

  // Fine-tuned variants have no tracked gen cost, so they contribute eval cost
  // only — same rule as the ops table's per-row totals.
  const runCreditsUsd = (() => {
    const ops = run.operational ?? [];
    if (ops.length === 0) return null;
    const byVariant = new Map(ops.map((o) => [o.variantId, o]));
    let total = 0;
    let seen = false;
    for (const v of run.variants) {
      const o = byVariant.get(v.id);
      if (!o) continue;
      const finetuned = isFinetunedServingId(v.resolvedModel || v.label);
      const usd = finetuned ? o.evalCost : o.totalCost;
      if (usd != null) {
        total += usd;
        seen = true;
      }
    }
    return seen ? total : null;
  })();

  return (
    <TraceScoringRunContext.Provider value={isTraceScoringRun}>
      {/* There is no app-wide TooltipProvider; the page's help tips need one. */}
      <TooltipProvider delayDuration={200}>
        <PageShell header={header} variant="full">
          {isTerminal && (
            <Tabs
              className="w-auto self-start"
              onValueChange={(v) => setView(v as "results" | "compare")}
              value={view}
            >
              <TabsList aria-label="Run view">
                <TabsTrigger value="results">Results</TabsTrigger>
                <TabsTrigger value="compare">Compare runs</TabsTrigger>
              </TabsList>
            </Tabs>
          )}

          {runStatus === "failed" && run.error ? (
            <FailureCard
              alternative={
                run.dataset ? (
                  <Link
                    params={{ datasetId: run.dataset }}
                    search={{ projectId }}
                    to="/datasets/$datasetId"
                  >
                    View dataset
                  </Link>
                ) : undefined
              }
              error={run.error}
              onRetry={() =>
                relaunch.mutate(runId, {
                  onSuccess: () => toast.success("Re-launched"),
                })
              }
              retryPending={relaunch.isPending}
              title="Run failed"
            />
          ) : run.error ? (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {run.error}
            </div>
          ) : null}

          {(run.warnings ?? []).length > 0 && (
            <div className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
              <p className="font-medium">Evaluator compatibility warnings</p>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-xs">
                {(run.warnings ?? []).map((warning, index) => (
                  <li key={`${warning.evaluator}-${warning.severity}-${index}`}>
                    {warning.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="min-h-0 flex-1 overflow-auto">
            <Card className="flex flex-col gap-4 p-4">
              <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
                <div className="flex min-w-0 flex-col gap-2">
                  <h3 className="inline-flex items-center gap-1.5 text-xs text-foreground">
                    {run.variants.length === 1 ? "Model" : "Models"}
                    {run.variants.length > 1 && <CountChip count={run.variants.length} />}
                  </h3>
                  <div className="flex flex-wrap gap-2">
                    {[...run.variants]
                      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
                      .map((v) => (
                        <ModelProviderChip key={v.id} model={v.resolvedModel || v.label} />
                      ))}
                  </div>
                </div>
                <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
                  <div className="flex items-baseline gap-2">
                    <dt className="text-muted-foreground">Credits used</dt>
                    <dd className="font-mono font-medium tabular-nums">
                      {runCreditsUsd != null ? <CreditsAmount usd={runCreditsUsd} /> : "—"}
                    </dd>
                  </div>
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
                      <EntityRef
                        id={run.capabilityId}
                        kind="capability"
                        name={run.capabilityName}
                      />
                    </HeaderStat>
                    <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
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
                    <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
                  </>
                )}
                {run.dataSource && (
                  <>
                    <HeaderStat label="Source">
                      {DATA_SOURCE_LABEL[run.dataSource] ?? run.dataSource}
                    </HeaderStat>
                    <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
                  </>
                )}
                {run.maxItems != null && run.maxItems > 0 && (
                  <>
                    <HeaderStat label="Max items">{run.maxItems}</HeaderStat>
                    <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />
                  </>
                )}
                <HeaderStat label="Evaluators">
                  {run.runEvaluators.filter((e) => e.enabled).length}
                </HeaderStat>
              </div>

              <div className="flex flex-wrap items-center gap-2.5 border-t border-border/70 pt-3">
                <StatusBadge status={runStatus} />
                <RunElapsed
                  completedAt={run.completedAt}
                  createdAt={run.createdAt}
                  status={runStatus}
                  updatedAt={run.updatedAt}
                />
                <div className="ml-auto flex shrink-0 items-center gap-2">
                  <Button
                    disabled={relaunch.isPending}
                    onClick={() =>
                      relaunch.mutate(runId, {
                        onError: (e) => notify.error(e, "Couldn't re-launch the run"),
                        onSuccess: () => toast.success("Re-launched"),
                      })
                    }
                    size="sm"
                    variant="secondary"
                  >
                    <Icon.refresh />
                    Re-run
                  </Button>
                  {runStatus === "running" && (
                    <Button
                      disabled={cancel.isPending}
                      onClick={() => cancel.mutate(runId)}
                      size="sm"
                      variant="secondary"
                    >
                      <Icon.close />
                      Cancel
                    </Button>
                  )}
                  {runStatus === "completed" && (
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

            {view === "compare" && isTerminal ? (
              <div className="pt-5">
                <RunComparison
                  baseCreatedAt={run.createdAt}
                  baseName={run.name}
                  baseRunId={runId}
                  dataset={run.dataset ?? null}
                  projectId={projectId}
                />
              </div>
            ) : (
              <ComparisonTable
                operational={run.operational}
                runEvaluators={run.runEvaluators}
                runId={runId}
                runStatus={runStatus}
                runVariants={run.variants}
              />
            )}
          </div>
        </PageShell>
      </TooltipProvider>
    </TraceScoringRunContext.Provider>
  );
}

function SectionHeading({
  title,
  subtitle,
  help,
}: {
  title: string;
  subtitle: string;
  help?: string;
}) {
  return (
    <div className="flex flex-col gap-0.5 pt-1">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        {help && <HelpTip label={title} text={help} />}
      </div>
      <p className="text-xs text-muted-foreground">{subtitle}</p>
    </div>
  );
}

function ScoreChip({
  value,
  size = "sm",
  dimmed = false,
}: {
  value: number | null | undefined;
  size?: "sm" | "lg";
  dimmed?: boolean;
}) {
  if (value == null) return <span className="text-muted-foreground/40">—</span>;
  const pct = scorePct(value);
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-sm border font-mono font-semibold tabular-nums",
        size === "lg" ? "px-2.5 py-1 text-sm" : "px-1.5 py-0.5 text-xs",
        dimmed ? "opacity-50" : "",
        scoreChipClass(pct)
      )}
      title={dimmed ? "Dataset-level aggregate score (same for all rows)" : undefined}
    >
      {pct}%
    </span>
  );
}

/** Per-item pass rates for one evaluator, hidden until asked for: two variants
 *  can share a score while failing different items, which the mean erases. */
function ItemBreakdown({ items }: { items: ItemRate[] }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-1 border-t border-border/60 pt-2">
      <button
        aria-expanded={open}
        className="pixel-label flex w-full items-center gap-1.5 text-left text-xs text-muted-foreground"
        onClick={() => setOpen((v) => !v)}
        type="button"
      >
        {items.length} criteria
        <Icon.chevronDown
          className={cn(
            "size-3 transition-transform duration-200 motion-reduce:transition-none",
            open && "rotate-180"
          )}
        />
      </button>
      {open && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {items.map((item) => (
            <li className="flex items-baseline justify-between gap-2 text-xs" key={item.id}>
              <span className="truncate text-muted-foreground">{humanizeMetricName(item.id)}</span>
              <span
                className={cn(
                  "shrink-0 tabular-nums",
                  item.pass_rate === 1 ? "text-success" : "text-warning"
                )}
              >
                {Math.round(item.pass_rate * 100)}%
                <span className="ml-1 text-muted-foreground">
                  ({item.passed}/{item.total})
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function VariantCard({
  label,
  metrics,
  activeMetric,
  isBaseline,
  status,
  items,
}: {
  label: string;
  metrics: Record<string, MetricCell> | null;
  activeMetric: string;
  isBaseline?: boolean;
  status?: "scored" | "pending" | "no_data";
  items?: ItemRate[];
}) {
  const cell = metrics?.[activeMetric];
  const headline = cell?.pooled ?? cell?.mean ?? null;
  const cardStatus = status ?? (headline != null ? "scored" : "pending");
  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-md border border-border bg-card p-4",
        cardStatus === "pending" && "border-dashed opacity-70"
      )}
    >
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-medium">{label}</span>
        {isBaseline && (
          <Badge className="text-xs" variant="secondary">
            Baseline
          </Badge>
        )}
        {cardStatus === "pending" && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            <Spinner className="size-3" size="sm" /> pending
          </span>
        )}
      </div>
      {headline != null ? (
        <>
          <ScoreChip size="lg" value={headline} />
          {cell?.pass_rate != null && (
            <span className="text-xs text-muted-foreground">
              {Math.round(cell.pass_rate * 100)}% pass · {cell.n} samples
            </span>
          )}
          {cell?.pooled != null && (
            <span className="text-xs text-muted-foreground">pooled across claims</span>
          )}
          {items && items.length > 0 && <ItemBreakdown items={items} />}
        </>
      ) : cardStatus === "pending" ? (
        <div className="flex flex-col gap-1.5">
          <Skeleton className="h-6 w-16" />
          <Skeleton className="h-3 w-24" />
        </div>
      ) : (
        <span className="text-sm text-muted-foreground/50">—</span>
      )}
    </div>
  );
}

type DatapointSortKey = "index" | "input" | "output" | `score:${string}`;

interface ScoreRecord {
  sample?: string | null;
  variant?: string | null;
  name?: string | null;
  value?: number | null;
  passed?: boolean | null;
  scope?: string | null;
  reasoning?: string | null;
}

function buildIndexes(
  samples: Array<{
    id?: string;
    rowIndex?: number | null;
    sourceTraceId?: string;
    variant?: string | null;
  }>,
  scores: Array<ScoreRecord>
) {
  const { datapointRows, sampleMap, sampleVariantMap } = groupSampleRows(samples);

  const scoreIndex = new Map<string, Map<string, number>>();
  const verdictIndex = new Map<string, Map<string, "passed" | "failed">>();
  const errorIndex = new Map<string, Map<string, string>>();
  // Keyed by variant, not sample — dataset-scope scores are one per variant.
  const datasetScoreIndex = new Map<string, Map<string, number>>();
  const datasetMetrics = new Set<string>();

  for (const sc of scores) {
    const name = sc.name;
    if (!name) continue;

    if (sc.scope === "dataset" && sc.variant && sc.value != null) {
      if (!datasetScoreIndex.has(sc.variant)) datasetScoreIndex.set(sc.variant, new Map());
      datasetScoreIndex.get(sc.variant)!.set(name, sc.value);
      datasetMetrics.add(name);
      continue;
    }

    if (!sc.sample) continue;

    if (sc.value != null) {
      if (!scoreIndex.has(sc.sample)) scoreIndex.set(sc.sample, new Map());
      scoreIndex.get(sc.sample)!.set(name, sc.value);
      const verdict = scoreVerdict(sc.value, sc.passed);
      if (verdict) {
        if (!verdictIndex.has(sc.sample)) verdictIndex.set(sc.sample, new Map());
        verdictIndex.get(sc.sample)!.set(name, verdict);
      }
    } else if (sc.reasoning && !name.endsWith("__prediction")) {
      // Null value plus a reasoning is the wire shape for "evaluator errored or skipped".
      if (!errorIndex.has(sc.sample)) errorIndex.set(sc.sample, new Map());
      errorIndex.get(sc.sample)!.set(name, sc.reasoning);
    }
  }

  return {
    datapointRows,
    datasetMetrics,
    datasetScoreIndex,
    errorIndex,
    sampleMap,
    sampleVariantMap,
    scoreIndex,
    verdictIndex,
  };
}

const PAGE_SIZE = 20;

function ComparisonTable({
  runId,
  runStatus,
  runEvaluators,
  runVariants,
  operational,
}: {
  runId: string;
  runStatus: string;
  operational: EvalRunOperationalStat[];
  runEvaluators: Array<{
    id: string;
    name: string;
    kind: string;
    scopeOverride: string;
    enabled: boolean;
    snapshot: unknown;
  }>;
  runVariants: Array<{
    id: string;
    label: string;
    isBaseline?: boolean;
    resolvedModel: string;
    order?: number;
  }>;
}) {
  const isActive = runStatus === "running" || runStatus === "pending";
  const { data: cmp, isLoading } = useEvalRunComparisonQuery(runId, runStatus);
  const { data: samplesData } = useEvalSamplesQuery(runId, isActive);
  const { data: scoresData } = useEvalScoresQuery({ runId }, isActive);

  const payload = cmp as ComparisonPayload | undefined;
  const summary = payload?.summary as RunSummary | undefined;
  const metrics = summary?.metrics ?? [];
  const variants = summary?.variants ?? {};
  const variantIds = Object.keys(variants);
  const isReplayRun = metrics.some((m) => TURN_DIM_RE.test(m));
  const progress = payload?.progress;
  const isRunning = runStatus === "running" || runStatus === "pending";
  const errorCounts = summary?.error_counts;
  const hasErrors = (errorCounts?.errored ?? 0) > 0;
  const hasEvaluatorErrors = (errorCounts?.evaluator_errors ?? 0) > 0;
  const trust = summary?.trust;
  const hasDegraded = (trust?.degraded ?? 0) > 0;
  const applicability = summary?.applicability;
  const hasNotApplicable = (applicability?.total ?? 0) > 0;

  const [activeMetric, setActiveMetric] = useState<string>("");
  const [page, setPage] = useState(0);
  const [openRow, setOpenRow] = useState<DatapointRow | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("all");
  const [sort, setSort] = useState<SortState<DatapointSortKey>>({ dir: "asc", key: "index" });

  const handleSearchChange = (value: string) => {
    setSearchQuery(value);
    setPage(0);
  };
  const handleVerdictChange = (value: VerdictFilter) => {
    setVerdictFilter(value);
    setPage(0);
  };
  const handleSort = (key: DatapointSortKey) => {
    setSort((prev) =>
      prev.key === key
        ? { dir: prev.dir === "asc" ? "desc" : "asc", key }
        : // Scores read best worst/best-first from a first click; text ascends.
          { dir: key.startsWith("score:") ? "desc" : "asc", key }
    );
    setPage(0);
  };

  useEffect(() => {
    if (metrics.length > 0 && !activeMetric) setActiveMetric(metrics[0]);
  }, [metrics, activeMetric]);

  const currentMetric = activeMetric || metrics[0] || "";

  const allSamples = samplesData?.results ?? [];
  const allScores = scoresData?.results ?? [];
  const {
    datapointRows,
    sampleMap,
    sampleVariantMap,
    scoreIndex,
    errorIndex,
    datasetScoreIndex,
    datasetMetrics,
    verdictIndex,
  } = buildIndexes(allSamples, allScores as ScoreRecord[]);

  // Input is shared across a datapoint's variants; output is per-variant, so the
  // Output column only exists for single-model runs.
  const previewBySample = new Map(allSamples.map((s) => [s.id, s]));
  const singleVariantId = variantIds.length === 1 ? variantIds[0] : null;

  // Mirrors the render-time lookup below — keep the two in step.
  const rowSampleId = (row: DatapointRow, vid: string): string | undefined =>
    sampleMap.get(row.key)?.get(vid);

  // The samples query fetches the whole run, so filtering can stay in memory.
  const hasActiveFilter = !!searchQuery.trim() || verdictFilter !== "all";
  const filteredRows = !hasActiveFilter
    ? datapointRows
    : datapointRows.filter((row) => {
        const texts: Array<string | null | undefined> = [
          previewBySample.get(row.fallbackSampleId)?.inputPreview,
        ];
        for (const vid of variantIds) {
          const sid = rowSampleId(row, vid);
          const s = sid ? previewBySample.get(sid) : undefined;
          texts.push(s?.outputPreview, s?.error);
        }
        if (!matchesSearch(searchQuery, texts)) return false;
        if (verdictFilter === "all") return true;
        return variantIds.some((vid) => {
          const sid = rowSampleId(row, vid);
          return sid ? verdictIndex.get(sid)?.get(currentMetric) === verdictFilter : false;
        });
      });

  // Taken from the unfiltered list so a row keeps its "#" wherever it lands.
  const ordinalByRow = new Map(datapointRows.map((r, i) => [r, i]));

  // Same resolution as the rendered cell: per-sample score, else the variant's
  // dataset-level aggregate.
  const rowScoreValue = (row: DatapointRow, vid: string): number | null => {
    const sampleId = rowSampleId(row, vid);
    const perSample = sampleId ? scoreIndex.get(sampleId)?.get(currentMetric) : undefined;
    if (perSample != null) return perSample;
    if (!datasetMetrics.has(currentMetric)) return null;
    const variantId = sampleId ? sampleVariantMap.get(sampleId) : vid;
    return datasetScoreIndex.get(variantId ?? vid)?.get(currentMetric) ?? null;
  };

  const sortValue = (row: DatapointRow): number | string | null => {
    if (sort.key === "index") return ordinalByRow.get(row) ?? null;
    if (sort.key === "input")
      return previewBySample.get(row.fallbackSampleId)?.inputPreview ?? null;
    if (sort.key === "output") {
      const sid = singleVariantId ? rowSampleId(row, singleVariantId) : undefined;
      return sid ? (previewBySample.get(sid)?.outputPreview ?? null) : null;
    }
    return rowScoreValue(row, sort.key.slice("score:".length));
  };
  const sortedRows = sortRows(filteredRows, sortValue, sort.dir);

  const totalPages = Math.max(1, Math.ceil(sortedRows.length / PAGE_SIZE));
  // A live refetch can shrink the row set under the active page.
  const safePage = Math.min(page, totalPages - 1);
  useEffect(() => {
    if (page !== safePage) setPage(safePage);
  }, [page, safePage]);
  const pageRows = sortedRows.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE);
  const pageInfo = paginationFromPageLimit({
    count: sortedRows.length,
    page: safePage + 1,
    pageSize: PAGE_SIZE,
  });

  const scoresLoading = isLoading && !summary;

  return (
    <>
      <div className="flex flex-col gap-5 pt-4">
        {isRunning && (
          <LiveRunMonitor
            evaluatorErrorCount={errorIndex.size}
            progress={progress ?? null}
            samples={allSamples}
          />
        )}

        {!isRunning && hasErrors && errorCounts && (
          <div className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
            <p className="font-medium">
              {errorCounts.errored} of {errorCounts.total} samples failed to process (
              {Math.round(errorCounts.error_rate * 100)}%)
            </p>
            <p className={cn(PROSE, "mt-1 text-xs")}>
              Cells showing — may be due to model API errors or generation failures. Re-run to
              retry.
            </p>
            {Object.values(errorCounts.by_variant).some((v) => v.errored > 0) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.values(errorCounts.by_variant)
                  .filter((v) => v.errored > 0)
                  .map((v) => (
                    <span
                      className="rounded-sm bg-warning/15 px-2 py-0.5 text-xs font-medium"
                      key={v.label}
                    >
                      {v.label}: {v.errored}/{v.total} failed
                    </span>
                  ))}
              </div>
            )}
          </div>
        )}

        {!isRunning && hasEvaluatorErrors && errorCounts && (
          <div className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
            <p className="font-medium">
              {errorCounts.evaluator_errors} evaluator call
              {errorCounts.evaluator_errors === 1 ? "" : "s"} failed (e.g. provider/network errors)
            </p>
            <p className={cn(PROSE, "mt-1 text-xs")}>
              The affected scores are ungraded and excluded from aggregates. Re-run to recover.
            </p>
            {Object.values(errorCounts.by_variant).some((v) => (v.evaluator_errors ?? 0) > 0) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.values(errorCounts.by_variant)
                  .filter((v) => (v.evaluator_errors ?? 0) > 0)
                  .map((v) => (
                    <span
                      className="rounded-sm bg-warning/15 px-2 py-0.5 text-xs font-medium"
                      key={v.label}
                    >
                      {v.label}: {v.evaluator_errors} ungraded
                    </span>
                  ))}
              </div>
            )}
          </div>
        )}

        {!isRunning && hasDegraded && trust && (
          <div className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
            <p className="font-medium">
              {trust.degraded} of {trust.total} samples were ungraded (
              {Math.round(trust.degraded_rate * 100)}%) — untrusted reconstruction
            </p>
            <p className={cn(PROSE, "mt-1 text-xs")}>
              These traces could not be faithfully reconstructed (e.g. trajectory lifted from raw
              I/O, tool steps dropped, or replayed tool calls missed their recorded results), so
              they are excluded from means and pass-rates rather than scored as failures.
            </p>
            {Object.values(trust.by_variant).some((v) => v.degraded > 0) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.values(trust.by_variant)
                  .filter((v) => v.degraded > 0)
                  .map((v) => (
                    <span
                      className="rounded-sm bg-warning/15 px-2 py-0.5 text-xs font-medium"
                      key={v.label}
                    >
                      {v.label}: {v.degraded}/{v.total} ungraded
                      {Object.keys(v.reasons).length > 0
                        ? ` (${Object.entries(v.reasons)
                            .map(([reason, count]) => `${reason}×${count}`)
                            .join(", ")})`
                        : ""}
                    </span>
                  ))}
              </div>
            )}
          </div>
        )}

        {!isRunning && hasNotApplicable && applicability && (
          <div className="rounded-md border border-info/40 bg-info/10 px-4 py-3 text-sm text-info">
            <p className="font-medium">
              {applicability.total} evaluator score
              {applicability.total === 1 ? "" : "s"} marked not applicable
            </p>
            <p className={cn(PROSE, "mt-1 text-xs")}>
              These evaluators need evidence this run's mode cannot produce (e.g. the capability's
              assembled output, only available in existing mode over captured traces). They are
              excluded from means and pass-rates — not scored as failures. Re-run in existing mode
              to grade them.
            </p>
            {Object.keys(applicability.by_evaluator).length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {Object.entries(applicability.by_evaluator).map(([name, count]) => (
                  <span
                    className="rounded-sm bg-info/15 px-2 py-0.5 text-xs font-medium"
                    key={name}
                  >
                    {humanizeMetricName(name)}: {count} N/A
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {!isRunning && variantIds.length > 0 && metrics.length > 0 && (
          <SectionHeading subtitle="Scores by model, evaluator and sample" title="Results" />
        )}
        {!isRunning && variantIds.length > 0 && (
          <EvalWinnerCallout
            metrics={metrics}
            operational={operational ?? []}
            replay={isReplayRun}
            summaryVariants={variants}
            variants={runVariants}
          />
        )}

        {!isRunning && variantIds.length > 0 && (
          <ScoreDistributions
            metrics={metrics}
            scores={allScores as DistributionScore[]}
            variants={runVariants}
          />
        )}

        {!isRunning && variantIds.length > 0 && metrics.length > 0 && (
          <>
            <SectionHeading
              help="Gen is the model under test, in dollars. Eval is LLM-judge scoring, in credits. Generation latency and tokens are recorded for newer runs only. Gen cost for your own fine-tuned models is not tracked and shows as -."
              subtitle="Cost, latency & tokens per model"
              title="Operations"
            />
            <PerModelOps operational={operational ?? []} variants={runVariants} />
          </>
        )}

        {!isRunning && variantIds.length > 0 && (
          <SectionHeading
            subtitle="Pick a test, then open a row for the full trace"
            title="Per-datapoint breakdown"
          />
        )}

        {/* Controls stay on the shared 32px ramp: select/search default, chip lg. */}
        {metrics.length > 0 && (
          <div className="flex w-full flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Test</span>
            <Select
              onValueChange={(m) => {
                setActiveMetric(m);
                setPage(0);
              }}
              value={currentMetric}
            >
              <SelectTrigger aria-label="Evaluation test" className="max-w-72" size="default">
                <SelectValue placeholder="Pick a test" />
              </SelectTrigger>
              <SelectContent>
                {metrics.map((m) => (
                  <SelectItem key={m} value={m}>
                    {humanizeMetricName(m)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <SearchInput
              className="min-w-[220px] flex-1"
              label="Search datapoints"
              onChange={(e) => handleSearchChange(e.target.value)}
              onClear={() => handleSearchChange("")}
              placeholder="Search input & output…"
              value={searchQuery}
            />
            <span className="text-xs font-medium text-muted-foreground">Score</span>
            {(["all", "passed", "failed"] as const).map((v) => (
              <Chip
                key={v}
                onClick={() => handleVerdictChange(v)}
                selected={verdictFilter === v}
                size="lg"
              >
                {v === "all" ? "All" : v === "passed" ? "Passed" : "Failed"}
              </Chip>
            ))}
            {hasActiveFilter && (
              <span aria-live="polite" className="text-xs text-muted-foreground">
                {filteredRows.length} of {datapointRows.length} datapoints
              </span>
            )}
          </div>
        )}

        {isRunning && runVariants.length > 0 && (
          <div
            className="grid gap-3"
            style={{
              gridTemplateColumns: `repeat(${Math.min(runVariants.length, 4)}, minmax(0,1fr))`,
            }}
          >
            {[...runVariants]
              .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
              .map((rv) => {
                const summaryVariant = variants[rv.id];
                return (
                  <VariantCard
                    activeMetric={currentMetric}
                    isBaseline={rv.isBaseline}
                    items={summary?.items?.[rv.id]?.[currentMetric]}
                    key={rv.id}
                    label={rv.label}
                    metrics={summaryVariant?.metrics ?? null}
                    status={summaryVariant ? "scored" : isRunning ? "pending" : "no_data"}
                  />
                );
              })}
          </div>
        )}

        {variantIds.length === 0 ? (
          <div className="overflow-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10">#</TableHead>
                  <TableHead>Input</TableHead>
                  {runEvaluators
                    .filter((e) => e.enabled)
                    .map((e) => (
                      <TableHead className="text-center" key={e.id}>
                        {humanizeMetricName(e.name)}
                      </TableHead>
                    ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {scoresLoading || isRunning ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell className="text-xs text-muted-foreground/30">{i + 1}</TableCell>
                      <TableCell className="text-center">
                        <Skeleton className="mx-auto h-3 w-20" />
                      </TableCell>
                      {runEvaluators
                        .filter((e) => e.enabled)
                        .map((e) => (
                          <TableCell className="text-center" key={e.id}>
                            <Skeleton className="mx-auto h-5 w-10" />
                          </TableCell>
                        ))}
                    </TableRow>
                  ))
                ) : (
                  <TableRow>
                    <TableCell
                      className="py-10 text-center text-sm text-muted-foreground"
                      colSpan={2 + runEvaluators.filter((e) => e.enabled).length}
                    >
                      No results yet. Results appear once the evaluation completes.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
        ) : (
          <>
            {datasetMetrics.has(currentMetric) && (
              <div
                className={cn(
                  PROSE,
                  "rounded-md border border-info/40 bg-info/10 px-4 py-2.5 text-xs text-info"
                )}
              >
                <span className="font-medium">{humanizeMetricName(currentMetric)}</span> is a
                dataset-level metric computed once across all samples — the score shown per row is
                the aggregate for that model variant. BLEU and chrF++ require multi-word texts; they
                may be 0 for single-word classification labels.
              </div>
            )}

            <div className="overflow-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHeader
                      className="w-10"
                      label="#"
                      onSort={handleSort}
                      sort={sort}
                      sortKey="index"
                    />
                    <SortableHeader label="Input" onSort={handleSort} sort={sort} sortKey="input" />
                    {singleVariantId ? (
                      <SortableHeader
                        label="Output"
                        onSort={handleSort}
                        sort={sort}
                        sortKey="output"
                      />
                    ) : null}
                    {variantIds.map((vid) => (
                      <SortableHeader
                        centered
                        key={vid}
                        label={singleVariantId ? "Score" : variants[vid].label}
                        onSort={handleSort}
                        sort={sort}
                        sortKey={`score:${vid}`}
                      />
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageRows.length === 0 && (
                    <TableRow>
                      <TableCell
                        className="py-10 text-center text-sm text-muted-foreground"
                        colSpan={2 + (singleVariantId ? 1 : 0) + variantIds.length}
                      >
                        No datapoints match your search or filter.
                      </TableCell>
                    </TableRow>
                  )}
                  {pageRows.map((row) => {
                    const ordinal = ordinalByRow.get(row) ?? 0;
                    const inputSample = previewBySample.get(row.fallbackSampleId);
                    const outSampleId = singleVariantId
                      ? rowSampleId(row, singleVariantId)
                      : undefined;
                    const outSample = outSampleId ? previewBySample.get(outSampleId) : undefined;
                    return (
                      <TableRow
                        className="cursor-pointer hover:bg-wash-raised"
                        key={row.key}
                        onClick={() => setOpenRow(row)}
                      >
                        <TableCell className="text-xs text-muted-foreground/40">
                          {ordinal + 1}
                        </TableCell>
                        <TableCell>
                          <span
                            className="block max-w-[24rem] truncate text-xs text-muted-foreground"
                            title={inputSample?.inputPreview || undefined}
                          >
                            {inputSample?.inputPreview || "—"}
                          </span>
                        </TableCell>
                        {singleVariantId ? (
                          <TableCell>
                            {outSample?.error ? (
                              <span
                                className="block max-w-[24rem] truncate text-xs text-warning"
                                title={outSample.error}
                              >
                                {outSample.error}
                              </span>
                            ) : (
                              <span
                                className="block max-w-[24rem] truncate text-xs text-foreground/90"
                                title={outSample?.outputPreview || undefined}
                              >
                                {outSample?.outputPreview || "—"}
                              </span>
                            )}
                          </TableCell>
                        ) : null}
                        {variantIds.map((vid) => {
                          const sampleId = rowSampleId(row, vid);
                          const perSampleScore = sampleId
                            ? scoreIndex.get(sampleId)?.get(currentMetric)
                            : undefined;
                          const variantId = sampleId ? sampleVariantMap.get(sampleId) : vid;
                          const datasetScore =
                            perSampleScore == null && datasetMetrics.has(currentMetric)
                              ? datasetScoreIndex.get(variantId ?? vid)?.get(currentMetric)
                              : undefined;
                          const score = perSampleScore ?? datasetScore;
                          const errorReason =
                            score == null && sampleId
                              ? errorIndex.get(sampleId)?.get(currentMetric)
                              : undefined;
                          return (
                            <TableCell className="text-center" key={vid}>
                              {errorReason ? (
                                <span
                                  className="inline-flex items-center justify-center rounded-sm border border-warning/40 bg-warning/10 px-1.5 py-0.5 font-mono text-xs font-semibold text-warning"
                                  title={errorReason}
                                >
                                  !
                                </span>
                              ) : (
                                <ScoreChip
                                  dimmed={datasetScore != null && perSampleScore == null}
                                  value={score}
                                />
                              )}
                            </TableCell>
                          );
                        })}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>

            {totalPages > 1 && (
              <nav
                aria-label="Datapoint pages"
                className="flex items-center justify-between text-xs text-muted-foreground"
              >
                <span>
                  {pageInfo.startItem}–{pageInfo.endItem} of {pageInfo.total} datapoints
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    className="px-2 text-xs"
                    disabled={!pageInfo.hasPrevious}
                    onClick={() => setPage((p) => Math.max(0, p - 1))}
                    size="sm"
                    variant="secondary"
                  >
                    <Icon.chevronLeft />
                    Previous
                  </Button>
                  {paginationItems(safePage + 1, totalPages).map((item, i) =>
                    item === "ellipsis" ? (
                      <span
                        aria-hidden="true"
                        className="px-1 text-muted-foreground/50"
                        key={`ellipsis-${i}`}
                      >
                        …
                      </span>
                    ) : (
                      <button
                        aria-current={item - 1 === safePage ? "page" : undefined}
                        aria-label={`Page ${item}`}
                        className={cn(
                          "h-7 min-w-7 rounded-sm px-1 text-xs font-medium transition-colors",
                          item - 1 === safePage
                            ? "bg-primary text-primary-foreground"
                            : "hover:bg-muted text-muted-foreground"
                        )}
                        key={item}
                        onClick={() => setPage(item - 1)}
                        type="button"
                      >
                        {item}
                      </button>
                    )
                  )}
                  <Button
                    className="px-2 text-xs"
                    disabled={!pageInfo.hasNext}
                    onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                    size="sm"
                    variant="secondary"
                  >
                    Next
                    <Icon.chevronRight />
                  </Button>
                </div>
              </nav>
            )}
          </>
        )}
      </div>

      <SampleModal
        datasetScoreIndex={datasetScoreIndex}
        onClose={() => setOpenRow(null)}
        row={openRow}
        sampleMap={sampleMap}
        variantIds={variantIds}
        variants={variants}
      />
    </>
  );
}

const PHASE_ORDER = ["generating", "scoring", "aggregating"] as const;

function phaseIndex(phase: string): number {
  const idx = (PHASE_ORDER as readonly string[]).indexOf(phase);
  if (idx >= 0) return idx;
  if (phase === "pending") return -1;
  return PHASE_ORDER.length; // terminal — every phase done
}

function RunElapsed({
  status,
  createdAt,
  completedAt,
  updatedAt,
}: {
  status: string;
  createdAt: Date;
  completedAt: Date | null;
  updatedAt: Date;
}) {
  const isRunning = status === "running" || status === "pending";
  const live = useElapsedSeconds(isRunning ? createdAt : undefined);

  if (isRunning) {
    if (live == null) return null;
    return (
      <span
        aria-label={`Running for ${formatElapsed(live)}`}
        className="tabular-nums text-xs text-muted-foreground"
        role="timer"
      >
        {formatElapsed(live)}
      </span>
    );
  }

  const end = completedAt ?? updatedAt;
  const totalSeconds = Math.floor((end.getTime() - createdAt.getTime()) / 1000);
  if (totalSeconds <= 0) return null;
  return (
    <span
      aria-label={`Ran for ${formatElapsed(totalSeconds)}`}
      className="tabular-nums text-xs text-muted-foreground"
      role="img"
    >
      {formatElapsed(totalSeconds)}
    </span>
  );
}

function LiveRunMonitor({
  progress,
  samples,
  evaluatorErrorCount,
}: {
  progress: EvalRunProgress | null;
  samples: EvalSampleList[];
  evaluatorErrorCount: number;
}) {
  const [openSampleId, setOpenSampleId] = useState<string | null>(null);
  const phase = progress?.phase ?? "pending";

  const prepared = progress?.prepared ?? 0;
  const total = progress?.total ?? 0;
  const scored = progress?.scored ?? 0;
  const scoreTotal = progress?.scoreTotal ?? 0;

  return (
    <section aria-label="Live run progress" className="flex flex-col gap-3">
      <PhaseStepper
        phase={phase}
        prepared={prepared}
        scored={scored}
        scoreTotal={scoreTotal}
        total={total}
      />

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-5">
        <div className="flex flex-col gap-3 lg:col-span-2">
          <VariantProgressList variants={progress?.variants ?? []} />
          <PartialEvaluatorStats stats={progress?.evaluatorStats ?? []} />
          <ErrorsPanel
            evaluatorErrorCount={evaluatorErrorCount}
            generationErrorCount={progress?.errors ?? 0}
            samples={samples}
          />
        </div>
        <div className="lg:col-span-3">
          <LiveSampleFeed onOpen={setOpenSampleId} samples={samples} />
        </div>
      </div>

      <SampleQuickView onClose={() => setOpenSampleId(null)} sampleId={openSampleId} />
    </section>
  );
}

function PhaseStepper({
  phase,
  prepared,
  total,
  scored,
  scoreTotal,
}: {
  phase: string;
  prepared: number;
  total: number;
  scored: number;
  scoreTotal: number;
}) {
  const activeIdx = phaseIndex(phase);
  const steps = [
    { done: prepared, key: "generating", label: "Generating responses", total },
    { done: scored, key: "scoring", label: "Scoring", total: scoreTotal },
    { done: 0, key: "aggregating", label: "Aggregating", total: 0 },
  ];

  return (
    <div className="rounded-md border bg-wash-subtle p-4">
      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="flex items-center gap-1.5 font-medium">
          <Spinner className="size-3" size="sm" />
          {phase === "pending" ? "Waiting for the worker to pick up the run…" : "Run in progress"}
        </span>
      </div>
      <ol aria-label="Run phases" className="grid grid-cols-3 gap-4">
        {steps.map((step, i) => {
          const state = i < activeIdx ? "done" : i === activeIdx ? "active" : "pending";
          const value = state === "done" ? step.total : step.done;
          const pct =
            step.total > 0 ? Math.round((value / step.total) * 100) : state === "done" ? 100 : 0;
          return (
            <li className="flex flex-col gap-1.5" key={step.key}>
              <div className="flex items-center gap-1.5 text-xs">
                <span
                  aria-hidden="true"
                  className={cn(
                    "size-2 shrink-0 rounded-xs",
                    state === "done" && "bg-primary",
                    state === "active" && "animate-pulse bg-primary motion-reduce:animate-none",
                    state === "pending" && "bg-muted-foreground/30"
                  )}
                />
                <span
                  className={cn(
                    "truncate font-medium",
                    state === "pending" && "text-muted-foreground/60"
                  )}
                >
                  {step.label}
                </span>
              </div>
              <div
                aria-label={`${step.label} progress`}
                aria-valuemax={step.total || 1}
                aria-valuemin={0}
                aria-valuenow={state === "done" ? step.total || 1 : step.done}
                className="h-1.5 w-full overflow-hidden rounded-xs bg-muted"
                role="progressbar"
              >
                <div
                  className={cn(
                    "h-full rounded-xs transition-all duration-700 motion-reduce:transition-none",
                    state === "pending" ? "bg-transparent" : "bg-primary"
                  )}
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span className="text-xs tabular-nums text-muted-foreground">
                {step.total > 0 ? (
                  <>
                    {value} / {step.total}
                    {state === "active" && step.total > 0 && <> · {pct}%</>}
                  </>
                ) : state === "active" ? (
                  "Computing summary…"
                ) : state === "done" ? (
                  "Done"
                ) : (
                  "Queued"
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function VariantProgressList({ variants }: { variants: EvalRunVariantProgress[] }) {
  if (variants.length === 0) return null;
  return (
    <div className="rounded-md border bg-wash-subtle p-4">
      <h3 className="mb-2.5 text-xs text-muted-foreground">Models</h3>
      <ul className="flex flex-col gap-2.5">
        {variants.map((v) => {
          const pct = v.total > 0 ? Math.round((v.prepared / v.total) * 100) : 0;
          const state =
            v.prepared >= v.total && v.total > 0
              ? "done"
              : v.prepared > 0
                ? "generating"
                : "pending";
          return (
            <li className="flex flex-col gap-1" key={v.id}>
              <div className="flex items-center justify-between gap-2 text-xs">
                <span className="flex min-w-0 items-center gap-1.5">
                  <span className="truncate font-medium">{v.label}</span>
                  {v.errors > 0 && (
                    <Badge className="shrink-0 text-xs" variant="destructive">
                      {v.errors} failed
                    </Badge>
                  )}
                </span>
                <span className="flex shrink-0 items-center gap-1 tabular-nums text-muted-foreground">
                  {state === "generating" && <Spinner className="size-3" size="sm" />}
                  {state === "pending" && "queued"}
                  {state !== "pending" && `${v.prepared} / ${v.total}`}
                </span>
              </div>
              <div
                aria-label={`${v.label} generation progress`}
                aria-valuemax={v.total || 1}
                aria-valuemin={0}
                aria-valuenow={v.prepared}
                className="h-1 w-full overflow-hidden rounded-xs bg-muted"
                role="progressbar"
              >
                <div
                  className="h-full rounded-xs bg-primary transition-all duration-700 motion-reduce:transition-none"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function PartialEvaluatorStats({ stats }: { stats: EvalRunEvaluatorStat[] }) {
  if (stats.length === 0) return null;
  return (
    <div className="rounded-md border bg-wash-subtle p-4">
      <div className="mb-2.5 flex items-center gap-2">
        <h3 className="text-xs text-muted-foreground">Scores so far</h3>
        <Badge className="text-xs" variant="outline">
          Partial
        </Badge>
      </div>
      <ul className="flex flex-col gap-1.5">
        {stats.map((s) => (
          <li className="flex items-center justify-between gap-2 text-xs" key={s.name}>
            <span className="min-w-0 truncate" title={s.name}>
              {humanizeMetricName(s.name)}
            </span>
            <span className="flex shrink-0 items-center gap-2 tabular-nums text-muted-foreground">
              {s.passRate != null && <span>{Math.round(s.passRate * 100)}% pass</span>}
              <ScoreChip value={s.mean} />
              <span className="text-muted-foreground/60">n={s.scored}</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function ErrorsPanel({
  samples,
  generationErrorCount,
  evaluatorErrorCount,
}: {
  samples: EvalSampleList[];
  generationErrorCount: number;
  evaluatorErrorCount: number;
}) {
  const failed = samples.filter((s) => s.error);
  const totalErrors = generationErrorCount + evaluatorErrorCount;

  if (totalErrors === 0) {
    return <p className="px-1 text-xs text-muted-foreground/50">No errors so far</p>;
  }

  return (
    <div className="rounded-md border border-warning/30 bg-warning/5 p-4 text-xs">
      <p className="font-medium text-warning">
        {generationErrorCount > 0 &&
          `${generationErrorCount} generation error${generationErrorCount === 1 ? "" : "s"}`}
        {generationErrorCount > 0 && evaluatorErrorCount > 0 && " · "}
        {evaluatorErrorCount > 0 &&
          `${evaluatorErrorCount} scoring error${evaluatorErrorCount === 1 ? "" : "s"}`}
        {failed.length === 0 && (
          <span className="ml-1.5 font-normal text-muted-foreground">
            Marked "!" in the results table below.
          </span>
        )}
      </p>
      {failed.length > 0 && (
        <ul className="mt-2.5 flex max-h-48 flex-col gap-1.5 overflow-y-auto">
          {failed.map((s) => (
            <li className="rounded-sm border bg-background/60 px-2 py-1.5 text-xs" key={s.id}>
              <span className="font-medium">{s.variantLabel || "sample"}</span>
              <span className="ml-1.5 text-muted-foreground">{(s.error ?? "").slice(0, 200)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const FEED_SIZE = 12;

function LiveSampleFeed({
  samples,
  onOpen,
}: {
  samples: EvalSampleList[];
  onOpen: (id: string) => void;
}) {
  // Samples are all created up-front, so created_at can't tell us which
  // finished last — order by when each was first seen as prepared.
  const seenRef = useRef<Map<string, number>>(new Map());
  const counterRef = useRef(0);

  const { feed, preparedCount } = useMemo(() => {
    const seen = seenRef.current;
    for (const s of samples) {
      if (s.id && s.isPrepared && !seen.has(s.id)) seen.set(s.id, counterRef.current++);
    }
    const ordinals = new Map<string, number>();
    samples.forEach((s, i) => {
      if (s.id) ordinals.set(s.id, i + 1);
    });
    const prepared = samples.filter((s) => s.id && s.isPrepared);
    const items = [...prepared]
      .sort((a, b) => (seen.get(b.id ?? "") ?? 0) - (seen.get(a.id ?? "") ?? 0))
      .slice(0, FEED_SIZE)
      .map((s) => ({ ordinal: ordinals.get(s.id ?? "") ?? 0, sample: s }));
    return { feed: items, preparedCount: prepared.length };
  }, [samples]);

  return (
    <div className="flex h-full flex-col rounded-md border bg-wash-subtle p-4">
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <h3 className="text-xs text-muted-foreground">Latest responses</h3>
        <span className="text-xs tabular-nums text-muted-foreground/70">
          {preparedCount} of {samples.length} generated
        </span>
      </div>
      {feed.length === 0 ? (
        <LoadingState label="Waiting for the first responses…" />
      ) : (
        <ul className="flex flex-col gap-1">
          {feed.map(({ sample, ordinal }) => (
            <li key={sample.id}>
              <button
                aria-label={`Open sample ${ordinal} (${sample.variantLabel ?? "variant"})`}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-xs transition-colors hover:bg-wash-raised focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                onClick={() => sample.id && onOpen(sample.id)}
                type="button"
              >
                <span className="w-9 shrink-0 font-mono tabular-nums text-muted-foreground/60">
                  #{ordinal}
                </span>
                <span className="w-28 shrink-0 truncate font-medium" title={sample.variantLabel}>
                  {sample.variantLabel}
                </span>
                {sample.error ? (
                  <Badge className="shrink-0 text-xs" variant="destructive">
                    Failed
                  </Badge>
                ) : null}
                <span className="min-w-0 flex-1 truncate text-muted-foreground">
                  {sample.error ? sample.error : sample.outputPreview || "(empty output)"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function SampleQuickView({ sampleId, onClose }: { sampleId: string | null; onClose: () => void }) {
  const { data: sample, isLoading } = useEvalSampleQuery(sampleId ?? "");
  const [viewMode, setViewMode] = useState<ViewMode>("formatted");
  const isTraceScoringRun = useContext(TraceScoringRunContext);
  const traj = (sample?.trajectory ?? {}) as { final_output?: string };
  const rawExpected = sample?.expected;
  const expected =
    rawExpected == null
      ? null
      : typeof rawExpected === "string"
        ? rawExpected
        : JSON.stringify(rawExpected);

  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open={!!sampleId}
    >
      <DialogContent size="md">
        <DialogHeader end={<ViewModeToggle onChange={setViewMode} value={viewMode} />}>
          <DialogTitle className="text-sm">
            Sample <span className="font-mono text-muted-foreground">{sampleId?.slice(0, 8)}…</span>
          </DialogTitle>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          {isLoading || !sample ? (
            <LoadingState label="Loading…" />
          ) : (
            <div className="flex flex-col gap-3">
              {sample.error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                  {sample.error}
                </div>
              )}
              <div className="rounded-md border border-primary/25 bg-primary/5 p-3">
                <div className="mb-1.5 flex items-center gap-1.5">
                  <Icon.model className="size-3.5 text-primary" />
                  <span className="text-xs font-semibold text-foreground">Model output</span>
                  <span className="rounded-sm bg-primary/15 px-1 py-0.5 text-xs font-semibold text-primary">
                    this run
                  </span>
                </div>
                <ModelOutputContent
                  fallback={traj.final_output ?? null}
                  structured={sample?.structured as Record<string, unknown> | undefined}
                  viewMode={viewMode}
                />
              </div>
              {!isTraceScoringRun && expected != null && (
                <div className="rounded-md border bg-wash-subtle p-3">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <Icon.dataset className="size-3.5 text-muted-foreground" />
                    <span className="text-xs font-semibold text-muted-foreground">
                      Expected output
                    </span>
                    <span className="rounded-sm bg-muted px-1 py-0.5 text-xs font-semibold text-muted-foreground">
                      from dataset
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap break-words font-mono text-xs text-muted-foreground">
                    {renderPayload(expected, viewMode)}
                  </p>
                </div>
              )}
            </div>
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  );
}

function ScoreVerdictBadge({ state }: { state: ScoreState }) {
  if (state === "passed")
    return (
      <Badge className="pixel-label text-xs" variant="success">
        Pass
      </Badge>
    );
  if (state === "failed")
    return (
      <Badge className="pixel-label text-xs" variant="destructive">
        Fail
      </Badge>
    );
  if (state === "ungraded")
    return (
      <Badge className="pixel-label text-xs" variant="warning">
        Ungraded
      </Badge>
    );
  return null;
}

/** Borderless: parents separate rows with `divide-y`, so the row owns its own
 * horizontal padding and those hairlines run edge to edge. */
function ScoreRow({ score }: { score: ScoreLike }) {
  const [open, setOpen] = useState(false);
  const state = scoreState(score);
  const reasoning = score.reasoning?.trim();
  const label = state === "ungraded" ? "Why ungraded?" : "Why this score?";

  return (
    <div className="px-4 py-2.5 first:pt-0 last:pb-0">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium" title={score.name ?? undefined}>
            {score.name ? humanizeMetricName(score.name) : score.name}
          </span>
          <ScoreVerdictBadge state={state} />
        </div>
        <ScoreChip value={score.value} />
      </div>
      {reasoning ? (
        <div className="mt-2">
          <button
            aria-expanded={open}
            className={cn(
              "pixel-label flex items-center gap-1.5 text-left text-xs transition-colors",
              state === "ungraded"
                ? "text-warning hover:text-warning/80"
                : "text-muted-foreground hover:text-foreground"
            )}
            onClick={() => setOpen((v) => !v)}
            type="button"
          >
            {label}
            <Icon.chevronDown
              className={cn(
                "size-3 transition-transform duration-200 motion-reduce:transition-none",
                open && "rotate-180"
              )}
            />
          </button>
          {open && (
            <p
              className={cn(
                PROSE,
                "mt-1.5 whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground/90"
              )}
            >
              {reasoning}
            </p>
          )}
        </div>
      ) : state === "ungraded" ? (
        <p className={cn(PROSE, "mt-2 text-xs italic text-muted-foreground")}>
          Ungraded — the evaluator abstained (no reason recorded).
        </p>
      ) : null}
    </div>
  );
}

// Per-turn scores carry a target_ref of "turn:<n>"; everything else is sample-level.
function groupByTurn<T extends { targetRef?: string | null }>(
  scores: T[]
): { sampleScores: T[]; turnDepths: [number, T[]][] } {
  const sampleScores: T[] = [];
  const byDepth = new Map<number, T[]>();
  for (const s of scores) {
    const ref = s.targetRef ?? "";
    if (ref.startsWith("turn:")) {
      const depth = Number(ref.slice(5)) || 0;
      if (!byDepth.has(depth)) byDepth.set(depth, []);
      byDepth.get(depth)!.push(s);
    } else {
      sampleScores.push(s);
    }
  }
  return { sampleScores, turnDepths: [...byDepth.entries()].sort((a, b) => a[0] - b[0]) };
}

type PerTurnEntry = {
  depth: number;
  generated_final?: string;
  reference_final?: string;
  generated?: { tool?: string }[];
  reference?: { tool?: string }[];
};

// Teacher-forced replay concatenates independently-generated turns into one
// blob that reads like the model repeating itself, hence the per-turn split.
function ModelOutputContent({
  structured,
  fallback,
  viewMode,
}: {
  structured: Record<string, unknown> | null | undefined;
  fallback: string | null;
  viewMode: ViewMode;
}) {
  const perTurn = ((structured?.per_turn as PerTurnEntry[] | undefined) ?? []).filter(
    (t) => (t.generated_final?.trim()?.length ?? 0) > 0 || (t.generated?.length ?? 0) > 0
  );
  if (perTurn.length > 1) {
    return (
      <div className="flex flex-col gap-2">
        {perTurn.map((t) => {
          const text = t.generated_final?.trim();
          const tools = (t.generated ?? []).map((n) => n.tool).filter(Boolean);
          return (
            <div className="rounded-md border border-border bg-background p-2" key={t.depth}>
              <Badge className="mb-1 text-xs" variant="secondary">
                Turn {t.depth + 1}
              </Badge>
              <p className="whitespace-pre-wrap break-words font-mono text-xs">
                {text
                  ? renderPayload(text, viewMode)
                  : `→ called: ${tools.join(", ") || "(no output)"}`}
              </p>
            </div>
          );
        })}
      </div>
    );
  }
  return (
    <p className="whitespace-pre-wrap break-words font-mono text-xs">
      {fallback ? renderPayload(fallback, viewMode) : "—"}
    </p>
  );
}

const TURN_DIM_SHORT: Record<string, string> = {
  "args grounded": "Args",
  progress: "Progress",
  safety: "Safety",
  "tool choice": "Tools",
  "turn match": "Match",
};

function shortDim(name: string): string {
  const seg = (name.includes(":") ? (name.split(":").pop() as string) : name).trim();
  return TURN_DIM_SHORT[seg.toLowerCase()] ?? seg;
}

function turnChipClasses(pct: number | null): string {
  // A missing score reads as poor on this surface, not as "no data".
  if (pct == null) return TONE_CHIP.error;
  return scoreChipClass(pct);
}

function TurnSide({
  label,
  final,
  tools,
  viewMode,
  muted,
}: {
  label: string;
  final?: string;
  tools?: { tool?: string }[];
  viewMode: ViewMode;
  muted?: boolean;
}) {
  const toolNames = (tools ?? []).map((t) => t.tool).filter(Boolean) as string[];
  const text = final?.trim();
  return (
    <div
      className={cn(
        "rounded-md border p-2",
        muted ? "bg-wash-subtle" : "border-primary/25 bg-primary/5"
      )}
    >
      <span className="mb-1 block text-xs font-semibold text-muted-foreground">{label}</span>
      {text ? (
        <p className="whitespace-pre-wrap break-words font-mono text-xs">
          {renderPayload(text, viewMode)}
        </p>
      ) : toolNames.length > 0 ? (
        <p className="font-mono text-xs text-muted-foreground">→ {toolNames.join(", ")}</p>
      ) : (
        <p className="text-xs italic text-muted-foreground">(no output)</p>
      )}
    </div>
  );
}

function TurnCard({
  entry,
  scores,
  index,
  total,
  viewMode,
}: {
  entry: PerTurnEntry | undefined;
  scores: ScoreLike[];
  index: number;
  total: number;
  viewMode: ViewMode;
}) {
  const [open, setOpen] = useState(false);
  // Skip ungraded dims (e.g. tool/args chips when the golden turn used no tools).
  const chips = scores.filter((s) => scoreState(s) !== "ungraded");
  const reasons = scores
    .map((s) => ({ dim: shortDim(s.name ?? ""), text: s.reasoning?.trim() }))
    .filter((r): r is { dim: string; text: string } => !!r.text);

  return (
    <div className="rounded-md border bg-background p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="rounded-sm bg-muted px-1.5 py-0.5 text-xs font-semibold text-muted-foreground">
          Turn {index + 1} of {total}
        </span>
        <div className="flex flex-wrap gap-1">
          {chips.map((s) => {
            const pct = s.value == null ? null : scorePct(s.value);
            return (
              <span
                className={cn(
                  "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium",
                  turnChipClasses(pct)
                )}
                key={s.id}
              >
                {shortDim(s.name ?? "")}
                {pct != null && <span className="font-mono tabular-nums">{pct}%</span>}
              </span>
            );
          })}
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-2">
        <TurnSide
          final={entry?.generated_final}
          label="Model did"
          tools={entry?.generated}
          viewMode={viewMode}
        />
        <TurnSide
          final={entry?.reference_final}
          label="Golden did"
          muted
          tools={entry?.reference}
          viewMode={viewMode}
        />
      </div>
      {reasons.length > 0 && (
        <div className="mt-2">
          <button
            aria-expanded={open}
            className="pixel-label flex items-center gap-1.5 text-left text-xs text-muted-foreground transition-colors hover:text-foreground"
            onClick={() => setOpen((v) => !v)}
            type="button"
          >
            Why?
            <Icon.chevronDown
              className={cn(
                "size-3 transition-transform duration-200 motion-reduce:transition-none",
                open && "rotate-180"
              )}
            />
          </button>
          {open && (
            <div className="mt-1.5 flex flex-col gap-1.5">
              {reasons.map((r, i) => (
                <p
                  className="whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground/90"
                  key={`${r.dim}-${i}`}
                >
                  <span className="font-medium">{r.dim}:</span> {r.text}
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TurnTimeline({
  perTurn,
  turnDepths,
  viewMode,
}: {
  perTurn: PerTurnEntry[];
  turnDepths: [number, ScoreLike[]][];
  viewMode: ViewMode;
}) {
  const byDepth = new Map(perTurn.map((t) => [t.depth, t]));
  const scoresByDepth = new Map(turnDepths);
  const depths = [...new Set([...turnDepths.map(([d]) => d), ...perTurn.map((t) => t.depth)])].sort(
    (a, b) => a - b
  );

  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs font-semibold text-muted-foreground/60">Turn-by-turn decisions</span>
      {depths.map((d, i) => (
        <TurnCard
          entry={byDepth.get(d)}
          index={i}
          key={d}
          scores={scoresByDepth.get(d) ?? []}
          total={depths.length}
          viewMode={viewMode}
        />
      ))}
    </div>
  );
}

function VariantAccordionCard({
  sampleId,
  label,
  isOpen,
  onToggle,
  datasetScores,
  viewMode,
}: {
  sampleId: string;
  label: string;
  isOpen: boolean;
  onToggle: () => void;
  datasetScores: Map<string, number>;
  viewMode: ViewMode;
}) {
  const [hasOpened, setHasOpened] = useState(isOpen);

  useEffect(() => {
    if (isOpen) setHasOpened(true);
  }, [isOpen]);

  const { data: sample, isLoading } = useEvalSampleQuery(hasOpened ? sampleId : "");
  const isTraceScoringRun = useContext(TraceScoringRunContext);

  const handleToggle = () => onToggle();

  const traj = (sample?.trajectory ?? {}) as { final_output?: string };
  const scores = (sample?.scores ?? []).filter((s) => !s.name?.endsWith("__prediction"));

  const predScore = (sample?.scores ?? []).find((s) => s.name?.endsWith("__prediction"));
  const predSubScores = (
    predScore?.subScores as Array<{ prediction?: string; reference?: string }> | null | undefined
  )?.[0];
  const prediction: string | null = predSubScores?.prediction ?? traj.final_output ?? null;
  const rawExpected = predSubScores?.reference ?? sample?.expected;
  // Compact stringify, so "Raw" shows the exact payload and "Formatted" indents it.
  const reference: string | null =
    rawExpected == null
      ? null
      : typeof rawExpected === "string"
        ? rawExpected
        : JSON.stringify(rawExpected);

  // Replay samples render their turns in the timeline, so the whole-conversation
  // output/expected blocks are hidden for them.
  const perTurnEntries = (
    (sample?.structured as { per_turn?: PerTurnEntry[] } | undefined)?.per_turn ?? []
  ).filter(
    (t) =>
      (t.generated_final?.trim()?.length ?? 0) > 0 ||
      (t.generated?.length ?? 0) > 0 ||
      (t.reference_final?.trim()?.length ?? 0) > 0 ||
      (t.reference?.length ?? 0) > 0
  );
  const { sampleScores, turnDepths } = groupByTurn(scores);
  const isReplaySample = turnDepths.length > 0 || perTurnEntries.length > 0;

  return (
    <div
      className={cn(
        "flex flex-col overflow-hidden rounded-md border",
        isOpen ? "min-h-0 flex-1" : "shrink-0"
      )}
    >
      <button
        aria-expanded={isOpen}
        className="flex w-full shrink-0 items-center justify-between px-3 py-2.5 text-left transition-colors hover:bg-wash-subtle"
        onClick={handleToggle}
        type="button"
      >
        <ModelProviderChip model={label} />
        <Icon.chevronDown
          className={cn(
            "size-4 shrink-0 text-muted-foreground transition-transform duration-200 motion-reduce:transition-none",
            isOpen && "rotate-180"
          )}
        />
      </button>

      {isOpen && (
        <div className="min-h-40 flex-1 overflow-y-auto border-t">
          {isLoading ? (
            <div className="px-4 py-3">
              <LoadingState label="Loading…" />
            </div>
          ) : (
            <div className="divide-y divide-border/70">
              {!isReplaySample && prediction != null && (
                <div className="px-4 py-3">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <Icon.model className="size-3.5 text-primary" />
                    <span className="text-xs font-semibold text-foreground">Model output</span>
                    <span className="rounded-sm bg-primary/15 px-1 py-0.5 text-xs font-semibold text-primary">
                      this run
                    </span>
                  </div>
                  <ModelOutputContent
                    fallback={prediction}
                    structured={sample?.structured as Record<string, unknown> | undefined}
                    viewMode={viewMode}
                  />
                </div>
              )}
              {!isReplaySample && !isTraceScoringRun && reference != null && (
                <div className="bg-wash-subtle px-4 py-3">
                  <div className="mb-1.5 flex items-center gap-1.5">
                    <Icon.dataset className="size-3.5 text-muted-foreground" />
                    <span className="text-xs font-semibold text-muted-foreground">
                      Expected output
                    </span>
                    <span className="rounded-sm bg-muted px-1 py-0.5 text-xs font-semibold text-muted-foreground">
                      from dataset
                    </span>
                  </div>
                  <p className="break-words whitespace-pre-wrap font-mono text-xs text-muted-foreground">
                    {renderPayload(reference, viewMode)}
                  </p>
                </div>
              )}

              {(() => {
                const toRow = (s: (typeof scores)[number]) => ({
                  id: s.id,
                  name: s.name,
                  passed: s.passed,
                  reasoning: s.reasoning,
                  scope: s.scope,
                  value: s.value,
                });
                if (isReplaySample) {
                  return (
                    <>
                      {sampleScores.length > 0 && (
                        <div className="py-3">
                          <span className="mb-1.5 block px-4 text-xs font-semibold text-muted-foreground/60">
                            Conversation
                          </span>
                          <div className="divide-y divide-border/70">
                            {sampleScores.map((s) => (
                              <ScoreRow key={s.id} score={toRow(s)} />
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="px-4 py-3">
                        <TurnTimeline
                          perTurn={perTurnEntries}
                          turnDepths={turnDepths}
                          viewMode={viewMode}
                        />
                      </div>
                    </>
                  );
                }
                if (scores.length === 0) return null;
                return (
                  <div className="divide-y divide-border/70 py-3">
                    {scores.map((s) => (
                      <ScoreRow key={s.id} score={toRow(s)} />
                    ))}
                  </div>
                );
              })()}
              {datasetScores.size > 0 && (
                <div className="px-4 py-3">
                  <span className="mb-2 block text-xs font-semibold text-muted-foreground/60">
                    Dataset metrics
                  </span>
                  <div className="flex flex-col gap-2">
                    {Array.from(datasetScores.entries()).map(([name, value]) => {
                      const pct = scorePct(value);
                      return (
                        <div
                          className="rounded-md bg-wash-subtle p-3 opacity-80"
                          key={name}
                          title="Dataset-level aggregate score — same for all samples in this variant"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-1.5">
                              <span className="text-sm font-medium">
                                {humanizeMetricName(name)}
                              </span>
                              <span className="rounded-sm bg-muted px-1 py-0.5 text-xs font-semibold text-muted-foreground">
                                dataset
                              </span>
                            </div>
                            <ScoreChip value={value} />
                          </div>
                          <div className="mt-2 h-1 w-full overflow-hidden rounded-xs bg-muted">
                            <div
                              className={cn("h-full rounded-xs", scoreFillClass(pct))}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {scores.length === 0 && datasetScores.size === 0 && (
                <p className="px-4 py-6 text-center text-xs text-muted-foreground">
                  No scores for this sample.
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function SampleModal({
  row,
  sampleMap,
  variants,
  variantIds,
  datasetScoreIndex,
  onClose,
}: {
  row: DatapointRow | null;
  sampleMap: Map<string, Map<string, string>>;
  variants: Record<string, VariantSummary>;
  variantIds: string[];
  datasetScoreIndex: Map<string, Map<string, number>>;
  onClose: () => void;
}) {
  const firstSampleId =
    (row
      ? (sampleMap.get(row.key)?.get(variantIds[0] ?? "") ?? row.fallbackSampleId)
      : undefined) ?? "";

  const { data: firstSample, isLoading } = useEvalSampleQuery(firstSampleId);

  const [viewMode, setViewMode] = useState<ViewMode>("formatted");

  const [openCards, setOpenCards] = useState<Set<string>>(() => new Set([variantIds[0] ?? ""]));
  useEffect(() => {
    setOpenCards(new Set([variantIds[0] ?? ""]));
  }, [variantIds]);

  const toggleCard = (vid: string) =>
    setOpenCards((prev) => {
      const next = new Set(prev);
      if (next.has(vid)) next.delete(vid);
      else next.add(vid);
      return next;
    });

  useEffect(() => {
    if (!row) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [row, onClose]);

  const traj = (firstSample?.trajectory ?? {}) as {
    messages?: Array<{ role: string; content?: string }>;
  };

  const dpLabel = row?.rowIndex != null ? String(row.rowIndex) : row?.fallbackSampleId.slice(0, 8);

  const variantSamples = variantIds.map((vid) => ({
    label: variants[vid]?.label ?? vid,
    sampleId: (row ? sampleMap.get(row.key)?.get(vid) : undefined) ?? "",
    vid,
  }));

  return (
    <Dialog
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      open={!!row}
    >
      <DialogContent size="full">
        <DialogHeader>
          <DialogTitle>Datapoint {dpLabel}</DialogTitle>
          <DialogDescription className="sr-only">
            Input, expected output and per-model evaluator results for this datapoint.
          </DialogDescription>
        </DialogHeader>

        {isLoading || !firstSample ? (
          <DialogBody>
            <LoadingState label="Loading…" />
          </DialogBody>
        ) : (
          // Not a `DialogBody`: the two panes scroll independently.
          <div className="grid min-h-0 flex-1 grid-cols-5 divide-x overflow-hidden">
            <div className="col-span-2 flex min-h-0 flex-col overflow-hidden">
              <div className="flex h-10 shrink-0 items-center gap-1.5 border-b px-5">
                <Icon.dataset className="size-3.5 text-muted-foreground" />
                <p className="text-xs font-medium text-muted-foreground">Input · from dataset</p>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <div className="divide-y divide-border/70">
                  {(traj.messages ?? []).map((m, i) => (
                    <div className="px-5 py-4" key={i}>
                      <span
                        className={cn(
                          "mb-1.5 block text-xs font-semibold capitalize",
                          m.role === "assistant" ? "text-primary/70" : "text-muted-foreground"
                        )}
                      >
                        {m.role}
                      </span>
                      <span className="whitespace-pre-wrap break-words font-mono text-xs leading-relaxed">
                        {renderPayload(m.content ?? "", viewMode)}
                      </span>
                    </div>
                  ))}
                  {!traj.messages?.length && (
                    <p className="px-5 py-8 text-center text-sm text-muted-foreground">
                      No messages available.
                    </p>
                  )}
                </div>
              </div>
            </div>

            <div className="col-span-3 flex min-h-0 flex-col overflow-hidden">
              <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b px-5">
                <p className="text-xs font-medium text-muted-foreground">Models</p>
                <ViewModeToggle onChange={setViewMode} value={viewMode} />
              </div>
              <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-5 py-4">
                {variantSamples.map(({ vid, label, sampleId }) => (
                  <VariantAccordionCard
                    datasetScores={datasetScoreIndex.get(vid) ?? new Map()}
                    isOpen={openCards.has(vid)}
                    key={vid}
                    label={label}
                    onToggle={() => toggleCard(vid)}
                    sampleId={sampleId}
                    viewMode={viewMode}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
