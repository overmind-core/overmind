import { useMemo, useState } from "react";

import { isFinetunedServingId } from "@/components/finetuning/finetuned-serving-id";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { CreditsAmount } from "@/components/ui/credits";
import { DeltaChip } from "@/components/ui/delta-chip";
import { Icon } from "@/components/ui/icons";
import { SortableHeader, type SortState } from "@/components/ui/sortable-header";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { formatCost } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";
import type { EvalRunOperationalStat } from "@/openapi";

interface MetricCell {
  mean: number | null;
  n: number;
  pass_rate: number | null;
  delta?: number;
  regression?: boolean;
  ci_low?: number | null;
  ci_high?: number | null;
  // False when this variant's CI overlaps the baseline's: the delta is noise.
  separable?: boolean;
}

export interface VariantSummary {
  label: string;
  metrics: Record<string, MetricCell>;
}

export interface OverviewVariant {
  id: string;
  label: string;
  resolvedModel: string;
  isBaseline?: boolean;
  order?: number;
}

interface WinnerCalloutProps {
  variants: OverviewVariant[];
  summaryVariants: Record<string, VariantSummary>;
  metrics: string[];
  operational: EvalRunOperationalStat[];
  // Teacher-forced replay emits one column per per-turn judge dimension plus
  // exact-match fidelity noise; collapse them into one "Turn quality" score.
  replay?: boolean;
}

// Per-turn judge dimensions (see per_turn_judge.py) — averaged into one column.
const TURN_DIM_RE = /:\s*(progress|turn match|tool choice|args grounded|safety)\s*$/i;
// Exact-match fidelity metrics read like bugs (~0%) for teacher-forced replay.
const FIDELITY_RE = /subsequence|exact|tool[-\s]?args/i;
const EFFICIENCY_RE = /ratio/i;
const TURN_QUALITY_METRIC = "Turn quality";

function averageCells(cells: MetricCell[]): MetricCell {
  const means = cells.map((c) => c.mean).filter((v): v is number => v != null);
  const passes = cells.map((c) => c.pass_rate).filter((v): v is number => v != null);
  const deltas = cells.map((c) => c.delta).filter((v): v is number => v != null);
  const avg = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null);
  return {
    delta: deltas.length ? (avg(deltas) as number) : undefined,
    mean: avg(means),
    n: cells.reduce((a, c) => a + (c.n ?? 0), 0),
    pass_rate: avg(passes),
    // Muted only when every constituent dimension is inseparable from baseline.
    separable: cells.some((c) => c.separable !== false),
  };
}

function collapseReplay(
  metrics: string[],
  summaryVariants: Record<string, VariantSummary>
): { metrics: string[]; summaryVariants: Record<string, VariantSummary> } {
  const turnDims = metrics.filter((m) => TURN_DIM_RE.test(m));
  const efficiency = metrics.filter((m) => !TURN_DIM_RE.test(m) && EFFICIENCY_RE.test(m));
  const other = metrics.filter(
    (m) => !TURN_DIM_RE.test(m) && !EFFICIENCY_RE.test(m) && !FIDELITY_RE.test(m)
  );
  const displayMetrics = [
    ...(turnDims.length ? [TURN_QUALITY_METRIC] : []),
    ...efficiency,
    ...other,
  ];
  const kept = [...efficiency, ...other];
  const out: Record<string, VariantSummary> = {};
  for (const [id, v] of Object.entries(summaryVariants)) {
    const metricsOut: Record<string, MetricCell> = {};
    if (turnDims.length) {
      const cells = turnDims.map((m) => v.metrics[m]).filter(Boolean) as MetricCell[];
      if (cells.length) metricsOut[TURN_QUALITY_METRIC] = averageCells(cells);
    }
    for (const m of kept) if (v.metrics[m]) metricsOut[m] = v.metrics[m];
    out[id] = { label: v.label, metrics: metricsOut };
  }
  return { metrics: displayMetrics, summaryVariants: out };
}

function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function formatTokens(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return String(Math.round(value));
}

function isFinetunedVariant(v: { resolvedModel: string; label: string }): boolean {
  return isFinetunedServingId(v.resolvedModel || v.label);
}

function EvalCostCell({ usd }: { usd: number | null | undefined }) {
  if (usd == null) return <span className="text-muted-foreground/40">—</span>;
  return <CreditsAmount usd={usd} />;
}

// The provider's own $ spend, never converted to credits.
function GenCostCell({ usd }: { usd: number | null | undefined }) {
  if (usd == null) return <span className="text-muted-foreground/40">—</span>;
  return <span>{formatCost(usd)}</span>;
}

// Fine-tuned generation cost isn't tracked, so those rows report the judge cost
// alone rather than a total mixing a real figure with an unreliable one.
function displayedTotalCost(
  ops: EvalRunOperationalStat | undefined,
  finetuned: boolean
): number | null {
  if (!ops) return null;
  return finetuned ? ops.evalCost : ops.totalCost;
}

interface ModelAggregate {
  variantId: string;
  label: string;
  resolvedModel: string;
  isBaseline?: boolean;
  meanScore: number | null;
  passRate: number | null;
  scoredCriteria: number;
}

function aggregateModel(
  variant: OverviewVariant,
  summary: VariantSummary | undefined,
  metrics: string[]
): ModelAggregate {
  const means: number[] = [];
  const passes: number[] = [];
  for (const m of metrics) {
    const cell = summary?.metrics?.[m];
    if (cell?.mean != null) means.push(cell.mean);
    if (cell?.pass_rate != null) passes.push(cell.pass_rate);
  }
  return {
    isBaseline: variant.isBaseline,
    label: variant.label,
    meanScore: means.length ? means.reduce((a, b) => a + b, 0) / means.length : null,
    passRate: passes.length ? passes.reduce((a, b) => a + b, 0) / passes.length : null,
    resolvedModel: variant.resolvedModel || variant.label,
    scoredCriteria: means.length,
    variantId: variant.id,
  };
}

export function EvalWinnerCallout({
  variants,
  summaryVariants: rawSummaryVariants,
  metrics: rawMetrics,
  operational,
  replay = false,
}: WinnerCalloutProps) {
  const { metrics, summaryVariants } = useMemo(
    () =>
      replay
        ? collapseReplay(rawMetrics, rawSummaryVariants)
        : { metrics: rawMetrics, summaryVariants: rawSummaryVariants },
    [replay, rawMetrics, rawSummaryVariants]
  );

  const orderedVariants = useMemo(
    () => [...variants].sort((a, b) => (a.order ?? 0) - (b.order ?? 0)),
    [variants]
  );

  const opsByVariant = useMemo(() => {
    const map = new Map<string, EvalRunOperationalStat>();
    for (const o of operational) map.set(o.variantId, o);
    return map;
  }, [operational]);

  const aggregates = useMemo(
    () => orderedVariants.map((v) => aggregateModel(v, summaryVariants[v.id], metrics)),
    [orderedVariants, summaryVariants, metrics]
  );

  // Highest mean across all criteria, tie-broken by pass rate then lower cost.
  const winner = useMemo(() => {
    const scored = aggregates.filter((a) => a.meanScore != null);
    if (scored.length === 0) return null;
    return [...scored].sort((a, b) => {
      if ((b.meanScore ?? 0) !== (a.meanScore ?? 0)) return (b.meanScore ?? 0) - (a.meanScore ?? 0);
      if ((b.passRate ?? 0) !== (a.passRate ?? 0)) return (b.passRate ?? 0) - (a.passRate ?? 0);
      const aCost = opsByVariant.get(a.variantId)?.totalCost ?? Number.POSITIVE_INFINITY;
      const bCost = opsByVariant.get(b.variantId)?.totalCost ?? Number.POSITIVE_INFINITY;
      return aCost - bCost;
    })[0];
  }, [aggregates, opsByVariant]);

  const runnerUp = useMemo(() => {
    if (!winner) return null;
    const others = aggregates.filter(
      (a) => a.variantId !== winner.variantId && a.meanScore != null
    );
    if (others.length === 0) return null;
    return [...others].sort((a, b) => (b.meanScore ?? 0) - (a.meanScore ?? 0))[0];
  }, [aggregates, winner]);

  if (metrics.length === 0 || orderedVariants.length < 2 || !winner) return null;

  return (
    <WinnerCallout ops={opsByVariant.get(winner.variantId)} runnerUp={runnerUp} winner={winner} />
  );
}

function WinnerCallout({
  winner,
  runnerUp,
  ops,
}: {
  winner: ModelAggregate;
  runnerUp: ModelAggregate | null;
  ops: EvalRunOperationalStat | undefined;
}) {
  const lead =
    runnerUp?.meanScore != null && winner.meanScore != null
      ? Math.round((winner.meanScore - runnerUp.meanScore) * 100)
      : null;
  const winnerCost = displayedTotalCost(ops, isFinetunedVariant(winner));
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-success/40 bg-success/5 px-4 py-3">
      <div className="flex items-center gap-2">
        <Icon.trophy className="size-4 text-success" />
        <span className="text-xs text-success">Best overall</span>
      </div>
      <ModelProviderChip model={winner.resolvedModel} />
      <div className="flex items-center gap-1.5 text-sm">
        <span className="text-muted-foreground">Score</span>
        <span className="inline-flex items-center gap-1.5 font-mono font-semibold tabular-nums text-foreground">
          <span>{winner.meanScore != null ? `${scorePct(winner.meanScore)}%` : "—"}</span>
          {lead != null && lead > 0 && (
            <DeltaChip label={`Up ${lead}% over next best`} value={lead} />
          )}
        </span>
      </div>
      {ops && (
        <span className="ml-auto flex items-center gap-3 text-xs text-muted-foreground">
          {winnerCost != null && <CreditsAmount usd={winnerCost} />}
          {ops.genLatencyMs != null && (
            <span className="flex items-center gap-1">
              <Icon.job className="size-3" /> {formatLatency(ops.genLatencyMs)}
            </span>
          )}
        </span>
      )}
    </div>
  );
}

type OpsSortKey =
  | "model"
  | "samples"
  | "genCost"
  | "evalCost"
  | "totalCost"
  | "genLatency"
  | "evalLatency"
  | "tokens";

export function PerModelOps({
  variants,
  operational,
}: {
  variants: OverviewVariant[];
  operational: EvalRunOperationalStat[];
}) {
  const opsByVariant = useMemo(() => {
    const map = new Map<string, EvalRunOperationalStat>();
    for (const o of operational) map.set(o.variantId, o);
    return map;
  }, [operational]);

  const anyGenLatency = operational.some((o) => o.genLatencyMs != null);
  const anyTokens = operational.some((o) => o.totalTokens != null);

  const [sort, setSort] = useState<SortState<OpsSortKey>>({ dir: "desc", key: "totalCost" });
  const handleSort = (key: OpsSortKey) =>
    setSort((prev) =>
      prev.key === key
        ? { dir: prev.dir === "asc" ? "desc" : "asc", key }
        : { dir: key === "model" ? "asc" : "desc", key }
    );

  const sortedVariants = useMemo(() => {
    const numericValue = (v: OverviewVariant): number | null => {
      const o = opsByVariant.get(v.id);
      const finetuned = isFinetunedVariant(v);
      switch (sort.key) {
        case "samples":
          return o?.sampleCount ?? null;
        case "genCost":
          // Untracked for fine-tuned models: sink it rather than sort on it.
          return finetuned ? null : (o?.genCost ?? null);
        case "evalCost":
          return o?.evalCost ?? null;
        case "totalCost":
          return displayedTotalCost(o, finetuned);
        case "genLatency":
          return o?.genLatencyMs ?? null;
        case "evalLatency":
          return o?.evalLatencyMs ?? null;
        case "tokens":
          return o?.totalTokens ?? null;
        default:
          return null;
      }
    };
    return [...variants].sort((a, b) => {
      if (sort.key === "model") {
        const cmp = (a.resolvedModel || a.label).localeCompare(b.resolvedModel || b.label);
        return sort.dir === "asc" ? cmp : -cmp;
      }
      const av = numericValue(a);
      const bv = numericValue(b);
      // Missing values always sink to the bottom, regardless of direction.
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return sort.dir === "asc" ? av - bv : bv - av;
    });
  }, [variants, opsByVariant, sort]);

  // No internal header — the page's "Operations" heading titles this table.
  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-auto rounded-md border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <SortableHeader label="Model" onSort={handleSort} sort={sort} sortKey="model" />
              <SortableHeader
                centered
                label="Samples"
                onSort={handleSort}
                sort={sort}
                sortKey="samples"
              />
              <SortableHeader
                centered
                label="Gen cost"
                onSort={handleSort}
                sort={sort}
                sortKey="genCost"
              />
              <SortableHeader
                centered
                label="Eval credits"
                onSort={handleSort}
                sort={sort}
                sortKey="evalCost"
              />
              <SortableHeader
                centered
                label="Total credits"
                onSort={handleSort}
                sort={sort}
                sortKey="totalCost"
              />
              {anyGenLatency && (
                <SortableHeader
                  centered
                  label="Gen latency"
                  onSort={handleSort}
                  sort={sort}
                  sortKey="genLatency"
                />
              )}
              <SortableHeader
                centered
                label="Eval latency"
                onSort={handleSort}
                sort={sort}
                sortKey="evalLatency"
              />
              {anyTokens && (
                <SortableHeader
                  centered
                  label="Tokens"
                  onSort={handleSort}
                  sort={sort}
                  sortKey="tokens"
                />
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedVariants.map((v) => {
              const ops = opsByVariant.get(v.id);
              const finetuned = isFinetunedVariant(v);
              return (
                <TableRow className="hover:bg-wash-subtle" key={v.id}>
                  <TableCell>
                    <ModelProviderChip compact model={v.resolvedModel || v.label} />
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums text-muted-foreground">
                    {ops?.sampleCount ?? "—"}
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums">
                    {finetuned ? (
                      <span
                        className="text-muted-foreground/40"
                        title="Generation cost for your fine-tuned model isn't tracked yet"
                      >
                        -
                      </span>
                    ) : (
                      <GenCostCell usd={ops?.genCost} />
                    )}
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs tabular-nums">
                    <EvalCostCell usd={ops?.evalCost} />
                  </TableCell>
                  <TableCell className="text-center font-mono text-xs font-semibold tabular-nums">
                    <EvalCostCell usd={displayedTotalCost(ops, finetuned)} />
                  </TableCell>
                  {anyGenLatency && (
                    <TableCell className="text-center font-mono text-xs tabular-nums">
                      {formatLatency(ops?.genLatencyMs)}
                    </TableCell>
                  )}
                  <TableCell className="text-center font-mono text-xs tabular-nums text-muted-foreground">
                    {formatLatency(ops?.evalLatencyMs)}
                  </TableCell>
                  {anyTokens && (
                    <TableCell className="text-center font-mono text-xs tabular-nums text-muted-foreground">
                      {formatTokens(ops?.totalTokens)}
                    </TableCell>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {!anyGenLatency && (
        <p className={cn(PROSE, "text-xs text-muted-foreground/70")}>
          Generation latency &amp; token usage are recorded for runs executed after this release;
          older runs show eval-judge latency only.
        </p>
      )}
    </div>
  );
}
