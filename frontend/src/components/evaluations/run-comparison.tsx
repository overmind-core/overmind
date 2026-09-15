import { useEffect, useMemo, useState } from "react";

import { EntityRef } from "@/components/entity-ref";
import { Badge } from "@/components/ui/badge";
import { DateTime } from "@/components/ui/datetime";
import { DeltaChip, fromScore } from "@/components/ui/delta-chip";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useComparableEvalRunsQuery, useEvalRunComparisonQuery } from "@/hooks/use-evaluations";
import { type StatusTone, scoreChipClass, TONE_BADGE_VARIANT, TONE_CHIP } from "@/lib/colors";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";

interface SummaryMetricCell {
  mean: number | null;
  n: number;
  pass_rate: number | null;
}
interface SummaryVariant {
  label: string;
  metrics: Record<string, SummaryMetricCell>;
}
export interface ComparisonSummary {
  metrics?: string[];
  variants?: Record<string, SummaryVariant>;
  trust?: { degraded?: number; total?: number };
  error_counts?: { evaluator_errors?: number; errored?: number; total?: number };
  applicability?: { total?: number; by_evaluator?: Record<string, number> };
}

export interface EvalAggregate {
  mean: number | null;
  passRate: number | null;
  n: number;
  variantCount: number;
}

const mean = (xs: number[]): number | null =>
  xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

// Falls back to pass-rate for evaluators that only emit a boolean pass signal.
export const primaryValue = (agg: EvalAggregate | null): number | null =>
  agg == null ? null : (agg.mean ?? agg.passRate);

const isPresent = (agg: EvalAggregate | null): boolean => primaryValue(agg) != null;

/** Averages the per-variant means; a variant with no score is skipped, not zeroed. */
export function aggregateEvaluator(summary: ComparisonSummary, metricName: string): EvalAggregate {
  const means: number[] = [];
  const passes: number[] = [];
  let n = 0;
  for (const variant of Object.values(summary.variants ?? {})) {
    const cell = variant.metrics?.[metricName];
    if (!cell) continue;
    if (cell.mean != null) means.push(cell.mean);
    if (cell.pass_rate != null) passes.push(cell.pass_rate);
    n += cell.n ?? 0;
  }
  return { mean: mean(means), n, passRate: mean(passes), variantCount: means.length };
}

export function overallAggregate(summary: ComparisonSummary): EvalAggregate {
  const means: number[] = [];
  const passes: number[] = [];
  let n = 0;
  for (const name of summary.metrics ?? []) {
    const agg = aggregateEvaluator(summary, name);
    if (agg.mean != null) means.push(agg.mean);
    if (agg.passRate != null) passes.push(agg.passRate);
    n += agg.n;
  }
  return { mean: mean(means), n, passRate: mean(passes), variantCount: means.length };
}

export type DiffStatus = "improved" | "regressed" | "unchanged" | "added" | "removed";

export interface ComparisonRow {
  name: string;
  current: EvalAggregate | null;
  baseline: EvalAggregate | null;
  delta: number | null;
  status: DiffStatus;
}

// Half a percentage point; smaller absolute deltas read as noise.
const EPSILON = 0.005;

function classify(
  current: EvalAggregate | null,
  baseline: EvalAggregate | null
): { delta: number | null; status: DiffStatus } {
  const cur = primaryValue(current);
  const base = primaryValue(baseline);
  const curHas = cur != null;
  const baseHas = base != null;
  if (curHas && !baseHas) return { delta: null, status: "added" };
  // Present in the baseline but ungraded now: lost coverage, not a score drop.
  if (!curHas && baseHas) return { delta: null, status: "removed" };
  if (!curHas && !baseHas) return { delta: null, status: "unchanged" };
  const delta = (cur as number) - (base as number);
  if (delta > EPSILON) return { delta, status: "improved" };
  if (delta < -EPSILON) return { delta, status: "regressed" };
  return { delta, status: "unchanged" };
}

// Attention-ordered: regressions and lost coverage first, then gains, then noise.
const STATUS_ORDER: Record<DiffStatus, number> = {
  added: 2,
  improved: 3,
  regressed: 0,
  removed: 1,
  unchanged: 4,
};

/** Rows are keyed by evaluator NAME, so a re-authored evaluator still lines up. */
export function buildComparisonRows(
  current: ComparisonSummary,
  baseline: ComparisonSummary
): ComparisonRow[] {
  const names = new Set<string>([...(current.metrics ?? []), ...(baseline.metrics ?? [])]);
  const rows: ComparisonRow[] = [];
  for (const name of names) {
    const cur = aggregateEvaluator(current, name);
    const base = aggregateEvaluator(baseline, name);
    if (!isPresent(cur) && !isPresent(base)) continue;
    const { delta, status } = classify(cur, base);
    rows.push({
      baseline: isPresent(base) ? base : null,
      current: isPresent(cur) ? cur : null,
      delta,
      name,
      status,
    });
  }
  rows.sort((a, b) => {
    const so = STATUS_ORDER[a.status] - STATUS_ORDER[b.status];
    if (so !== 0) return so;
    return Math.abs(b.delta ?? 0) - Math.abs(a.delta ?? 0);
  });
  return rows;
}

export interface RunTrust {
  trusted: boolean;
  degraded: number;
  evaluatorErrors: number;
  errored: number;
  notApplicable: number;
}

// N/A is benign: it is surfaced but never a trust hit.
export function runTrust(summary: ComparisonSummary): RunTrust {
  const degraded = summary.trust?.degraded ?? 0;
  const evaluatorErrors = summary.error_counts?.evaluator_errors ?? 0;
  const errored = summary.error_counts?.errored ?? 0;
  const notApplicable = summary.applicability?.total ?? 0;
  return {
    degraded,
    errored,
    evaluatorErrors,
    notApplicable,
    trusted: degraded === 0 && evaluatorErrors === 0 && errored === 0,
  };
}

const chipClass = scoreChipClass;

function AggChip({ agg }: { agg: EvalAggregate | null }) {
  const value = primaryValue(agg);
  if (value == null) return <span className="text-muted-foreground/40">—</span>;
  const pct = scorePct(value);
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-sm border px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums",
        chipClass(pct)
      )}
    >
      {pct}%
    </span>
  );
}

/** Read by both the status pill and the delta cell, so a status cannot be red
 * in one column and grey in the next. */
const DIFF_TONE: Record<DiffStatus, StatusTone> = {
  added: "info",
  improved: "success",
  regressed: "error",
  removed: "warning",
  unchanged: "neutral",
};

const DIFF_LABEL: Record<DiffStatus, string> = {
  added: "Added",
  improved: "Improved",
  regressed: "Regressed",
  removed: "Degraded coverage",
  unchanged: "Unchanged",
};

function DeltaCell({ delta, status }: { delta: number | null; status: DiffStatus }) {
  if (status === "added")
    return (
      <Badge className="text-xs" variant={TONE_BADGE_VARIANT[DIFF_TONE.added]}>
        New
      </Badge>
    );
  if (status === "removed")
    return (
      <Badge className="text-xs" variant={TONE_BADGE_VARIANT[DIFF_TONE.removed]}>
        Now N/A
      </Badge>
    );
  if (delta == null || status === "unchanged")
    return <span className="font-mono text-xs tabular-nums text-muted-foreground">±0</span>;
  return <DeltaChip value={fromScore(delta)} />;
}

function StatusPill({ status }: { status: DiffStatus }) {
  return (
    <Badge className="pixel-label text-xs" variant={TONE_BADGE_VARIANT[DIFF_TONE[status]]}>
      {DIFF_LABEL[status]}
    </Badge>
  );
}

function TrustNote({ label, trust }: { label: string; trust: RunTrust }) {
  if (trust.trusted) return null;
  const parts: string[] = [];
  if (trust.degraded > 0) parts.push(`${trust.degraded} degraded`);
  if (trust.evaluatorErrors > 0) parts.push(`${trust.evaluatorErrors} evaluator error(s)`);
  if (trust.errored > 0) parts.push(`${trust.errored} failed sample(s)`);
  return (
    <span className="flex items-center gap-1.5">
      <Icon.warning className="size-3 shrink-0 text-warning" />
      <span className={PROSE}>
        <span className="font-medium">{label}</span> aggregates may be unreliable —{" "}
        {parts.join(", ")}.
      </span>
    </span>
  );
}

interface RunOption {
  id: string;
  name: string;
  createdAt: Date;
}

export function RunComparison({
  baseRunId,
  projectId,
  dataset,
  baseName,
  baseCreatedAt,
}: {
  baseRunId: string;
  projectId: string | undefined;
  dataset: string | null;
  baseName: string;
  baseCreatedAt?: Date;
}) {
  const { data: runList, isLoading: runsLoading } = useComparableEvalRunsQuery(projectId, dataset);

  // The dataset restriction is server-filtered; datasetless capability runs have no
  // such filter, so they are matched on capability name here.
  const { cohort, baseEntry } = useMemo(() => {
    const all = runList?.results ?? [];
    const entry = all.find((r) => r.id === baseRunId);
    const sameCohort = (r: (typeof all)[number]) => {
      if (dataset) return r.dataset === dataset;
      if (entry?.capabilityName) return r.capabilityName === entry.capabilityName;
      return true;
    };
    const opts: RunOption[] = all
      .filter((r) => r.id !== baseRunId && r.status === "completed" && sameCohort(r))
      .map((r) => ({ createdAt: r.createdAt, id: r.id, name: r.name }))
      .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
    return { baseEntry: entry, cohort: opts };
  }, [runList, baseRunId, dataset]);

  const defaultOtherId = useMemo(() => {
    if (cohort.length === 0) return null;
    const baseTime = baseCreatedAt?.getTime() ?? baseEntry?.createdAt.getTime() ?? Infinity;
    const previous = cohort.find((r) => r.createdAt.getTime() < baseTime);
    return (previous ?? cohort[0]).id;
  }, [cohort, baseCreatedAt, baseEntry]);

  const [otherRunId, setOtherRunId] = useState<string | null>(null);
  useEffect(() => {
    setOtherRunId((prev) => (prev && cohort.some((r) => r.id === prev) ? prev : defaultOtherId));
  }, [defaultOtherId, cohort]);

  const { data: baseCmp, isLoading: baseLoading } = useEvalRunComparisonQuery(
    baseRunId,
    "completed"
  );
  const { data: otherCmp, isLoading: otherLoading } = useEvalRunComparisonQuery(
    otherRunId ?? undefined,
    "completed"
  );

  const currentSummary = (baseCmp as { summary?: ComparisonSummary } | undefined)?.summary;
  const baselineSummary = (otherCmp as { summary?: ComparisonSummary } | undefined)?.summary;

  const rows = useMemo(
    () =>
      currentSummary && baselineSummary ? buildComparisonRows(currentSummary, baselineSummary) : [],
    [currentSummary, baselineSummary]
  );

  const overall = useMemo(() => {
    if (!currentSummary || !baselineSummary) return null;
    const cur = overallAggregate(currentSummary);
    const base = overallAggregate(baselineSummary);
    return { baseline: base, current: cur, ...classify(cur, base) };
  }, [currentSummary, baselineSummary]);

  const currentTrust = currentSummary ? runTrust(currentSummary) : null;
  const baselineTrust = baselineSummary ? runTrust(baselineSummary) : null;
  const naByEvaluator = useMemo(() => {
    const m = new Map<string, number>();
    for (const [name, count] of Object.entries(currentSummary?.applicability?.by_evaluator ?? {}))
      m.set(name, count);
    return m;
  }, [currentSummary]);

  if (runsLoading) {
    return <LoadingState label="Loading runs…" />;
  }

  if (cohort.length === 0) {
    return (
      <EmptyState
        className="rounded-md border border-dashed bg-wash-subtle"
        description={`Needs a second run on ${dataset ? "this dataset" : "this capability"}.`}
        icon={Icon.diff}
        size="section"
        title="No comparable run yet"
      />
    );
  }

  const selectedOption = cohort.find((r) => r.id === otherRunId);

  return (
    <section aria-label="Run-vs-run comparison" className="flex flex-col gap-4">
      {/* A label row over a content row, so long names truncate inside their
          own fractional column instead of pushing the layout around. */}
      <div className="grid grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-x-4 gap-y-1 rounded-md border bg-wash-subtle px-4 py-3">
        <span className="text-xs text-muted-foreground">This run</span>
        <span aria-hidden="true" />
        <label className="text-xs text-muted-foreground" htmlFor="run-comparison-picker">
          Compared to
        </label>

        <div className="flex h-8 min-w-0 items-center gap-2">
          <span className="min-w-0 truncate text-sm font-medium" title={baseName}>
            {baseName}
          </span>
          {baseCreatedAt && (
            <span className="shrink-0 text-xs text-muted-foreground">
              <DateTime value={baseCreatedAt} />
            </span>
          )}
        </div>
        <span aria-hidden="true" className="text-xs font-medium text-muted-foreground">
          vs
        </span>
        <div className="flex h-8 min-w-0 items-center gap-2">
          <Select onValueChange={(v) => setOtherRunId(v)} value={otherRunId ?? undefined}>
            <SelectTrigger
              className="w-72 min-w-0"
              id="run-comparison-picker"
              size="default"
              title={selectedOption?.name}
            >
              <SelectValue className="min-w-0" placeholder="Pick a run to compare" />
            </SelectTrigger>
            <SelectContent>
              {cohort.map((r) => (
                <SelectItem key={r.id} value={r.id}>
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="min-w-0 flex-1 truncate">{r.name}</span>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      <DateTime value={r.createdAt} />
                    </span>
                  </span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {selectedOption && (
            <EntityRef
              className="h-8"
              id={selectedOption.id}
              kind="evalRun"
              name={selectedOption.name}
              projectId={projectId}
            />
          )}
        </div>
      </div>

      {((currentTrust && !currentTrust.trusted) || (baselineTrust && !baselineTrust.trusted)) && (
        <div
          className={cn(
            "flex flex-col gap-1.5 rounded-md border px-4 py-3 text-xs",
            TONE_CHIP.warning
          )}
        >
          {currentTrust && <TrustNote label="This run's" trust={currentTrust} />}
          {baselineTrust && <TrustNote label="Compared run's" trust={baselineTrust} />}
          <span className={cn(PROSE, "text-warning/80")}>
            Degraded/errored samples are excluded from the means below — deltas on affected
            evaluators should be read with caution.
          </span>
        </div>
      )}

      {baseLoading || otherLoading ? (
        <LoadingState label="Loading comparison…" />
      ) : rows.length === 0 ? (
        <div className="rounded-md border border-dashed bg-wash-subtle px-4 py-8 text-center text-sm text-muted-foreground">
          No shared evaluators between these two runs to compare.
        </div>
      ) : (
        <div className="overflow-auto rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Evaluator</TableHead>
                <TableHead className="text-center">This run</TableHead>
                <TableHead className="text-center">Compared</TableHead>
                <TableHead className="text-center">Δ</TableHead>
                <TableHead className="text-center">Change</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {overall && (
                <TableRow className="border-b-2 bg-wash-subtle">
                  <TableCell className="text-sm font-semibold">Overall</TableCell>
                  <TableCell className="text-center">
                    <AggChip agg={overall.current} />
                  </TableCell>
                  <TableCell className="text-center">
                    <AggChip agg={overall.baseline} />
                  </TableCell>
                  <TableCell className="text-center">
                    <DeltaCell delta={overall.delta} status={overall.status} />
                  </TableCell>
                  <TableCell className="text-center">
                    <StatusPill status={overall.status} />
                  </TableCell>
                </TableRow>
              )}
              {rows.map((row) => {
                const naCount = naByEvaluator.get(row.name) ?? 0;
                return (
                  <TableRow key={row.name}>
                    <TableCell>
                      <div className="flex flex-col gap-0.5">
                        <span className="text-sm font-medium">{row.name}</span>
                        {naCount > 0 && (
                          <span className="text-xs text-info">
                            {naCount} sample{naCount === 1 ? "" : "s"} N/A this run
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-center">
                      <AggChip agg={row.current} />
                    </TableCell>
                    <TableCell className="text-center">
                      <AggChip agg={row.baseline} />
                    </TableCell>
                    <TableCell className="text-center">
                      <DeltaCell delta={row.delta} status={row.status} />
                    </TableCell>
                    <TableCell className="text-center">
                      <StatusPill status={row.status} />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}
