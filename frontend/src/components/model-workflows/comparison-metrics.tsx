import { useMemo } from "react";

import type { ColumnDef } from "@tanstack/react-table";

import {
  type ConfidenceEvidence,
  ConfidenceMetric,
  summarizeConfidence,
} from "@/components/evaluations/confidence-metric";
import { MetricBars } from "@/components/evaluations/metric-bars";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { DataTable } from "@/components/ui/data-table";
import { Icon } from "@/components/ui/icons";
import { SortableHeader } from "@/components/ui/sortable-header";
import { Table, TableBody, TableCell, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useTableSort } from "@/hooks/use-table-sort";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import { TITLE } from "@/lib/typography";
export type Metric = {
  mean?: number;
  candidate_minus_baseline?: number;
  decisions: number;
  groups: number;
  interval_95?: number[] | null;
};
type Coverage = Omit<ConfidenceEvidence, "metrics"> & {
  expected: number;
  scored: number;
  missing_predictions: number;
  invalid_predictions: number;
  incompatible_inputs: number;
  metrics: Record<string, Metric>;
};
export type Comparison = {
  baseline: { benchmarks: Record<string, Coverage>; model_identity?: string };
  candidate: { benchmarks: Record<string, Coverage>; model_identity?: string };
  benchmarks: Record<string, { metrics: Record<string, Metric> }>;
};
export type Comparisons = Record<string, Partial<Record<"raw" | "calibrated", Comparison>>>;
const labels: Record<string, string> = {
  accuracy: "Accuracy",
  brier: "Brier",
  calibration_error: "Calibration error",
  cross_entropy: "Cross entropy",
  expected_accuracy: "Expected accuracy",
  expected_score_mae: "Score MAE",
  expected_score_squared_error: "Score squared error",
  mean_absolute_error: "Mean absolute error",
  mode_agreement: "Mode agreement",
  ordinal_mae: "Ordinal MAE",
  rps: "Ranked probability score",
  top1_reference_mass: "Top choice reference mass",
};
const descriptions: Record<string, string> = {
  accuracy: "How often the model chose the correct answer. Higher is better.",
  brier: "Probability error across all answer options. Lower is better.",
  cross_entropy: "Penalises low confidence in the reference answers. Lower is better.",
  expected_score_mae: "Average distance between predicted and reference scores. Lower is better.",
  expected_score_squared_error:
    "Score error with a larger penalty for bigger misses. Lower is better.",
  mode_agreement: "How often the model chose a most-supported reference answer. Higher is better.",
  rps: "Probability error that accounts for the order of score levels. Lower is better.",
  top1_reference_mass: "Reference support for the model's chosen answer. Higher is better.",
};
const percentages = new Set([
  "accuracy",
  "expected_accuracy",
  "mode_agreement",
  "top1_reference_mass",
]);
const number = (value: number | undefined) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
const format = (value: number | undefined, metric: string) =>
  typeof value === "number" && Number.isFinite(value) && metric === "calibration_error"
    ? `${(value * 100).toFixed(1)} pp`
    : typeof value === "number" && Number.isFinite(value) && percentages.has(metric)
      ? `${(value * 100).toFixed(1)}%`
      : number(value);
const label = (metric: string) => labels[metric] ?? metric.replaceAll("_", " ");

export {
  format as formatNativeMetric,
  label as nativeMetricLabel,
  percentages as nativePercentageMetrics,
};

type ModelRow = {
  key: string;
  name: string;
  card: Coverage;
  adjusted?: Coverage;
  delta?: Record<string, Metric>;
  colorIndex: number;
};
type ComparisonRow = ModelRow & { benchmark: string; metrics: string[] };

function measuredMetrics(rows: ModelRow[]) {
  const names = new Set(rows.flatMap((row) => Object.keys(row.card.metrics)));
  // Accuracy only exists on hard labels. Equal denominators establish that
  // these two distribution metrics cover exactly the same hard-label rows.
  for (const alias of ["mode_agreement", "top1_reference_mass"]) {
    if (
      rows.every(
        ({ card }) =>
          !card.metrics[alias] ||
          (card.metrics.accuracy?.decisions > 0 &&
            card.metrics.accuracy.decisions === card.metrics[alias].decisions)
      )
    )
      names.delete(alias);
  }
  const priority = ["accuracy", "cross_entropy", "brier", "expected_score_mae"];
  return [
    ...priority.filter((metric) => names.has(metric)),
    ...[...names].filter((metric) => !priority.includes(metric)).sort(),
  ];
}

function MetricValue({ row, metric }: { row: ModelRow; metric: string }) {
  const score = row.card.metrics[metric];
  const adjusted = row.adjusted?.metrics[metric];
  const delta = row.delta?.[metric];
  if (score?.mean == null) return <span className="text-muted-foreground">—</span>;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          className="cursor-help rounded-xs font-mono text-xs tabular-nums focus-visible:outline focus-visible:outline-ring"
          type="button"
        >
          {format(score.mean, metric)}
        </button>
      </TooltipTrigger>
      <TooltipContent className="border border-border bg-popover text-popover-foreground [&_svg]:hidden">
        <div className="space-y-1">
          {metric === "calibration_error" && (
            <p>Average confidence gap within the recorded confidence bands. Lower is better.</p>
          )}
          <p>
            {score.decisions} decisions · {score.groups} groups
          </p>
          {score.interval_95 && (
            <p>
              95% interval: {score.interval_95.map((value) => format(value, metric)).join(" to ")}
            </p>
          )}
          {delta?.candidate_minus_baseline != null && (
            <p>
              Difference from baseline:{" "}
              {percentages.has(metric)
                ? `${(delta.candidate_minus_baseline * 100).toFixed(1)} pp`
                : number(delta.candidate_minus_baseline)}
            </p>
          )}
          {delta?.interval_95 && (
            <p>
              Difference · 95% interval:{" "}
              {delta.interval_95
                .map((value) =>
                  percentages.has(metric) ? `${(value * 100).toFixed(1)} pp` : number(value)
                )
                .join(" to ")}
            </p>
          )}
          {adjusted?.mean != null && (
            <p>
              After calibration: {format(adjusted.mean, metric)} · {adjusted.decisions} decisions
            </p>
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function CoverageValue({ row }: { row: ModelRow }) {
  const { scored, expected, missing_predictions, invalid_predictions, incompatible_inputs } =
    row.card;
  const count = (
    <span className="font-mono text-xs tabular-nums">
      {scored} / {expected}
    </span>
  );
  if (!(missing_predictions || invalid_predictions || incompatible_inputs)) return count;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={`${row.name}: ${scored} / ${expected} scored`}
          className="inline-flex cursor-help items-center gap-2 rounded-xs focus-visible:outline focus-visible:outline-ring"
          type="button"
        >
          {count}
          <Icon.warning className="size-3.5 text-warning" />
        </button>
      </TooltipTrigger>
      <TooltipContent>
        {missing_predictions} missing · {invalid_predictions} invalid · {incompatible_inputs}{" "}
        incompatible
      </TooltipContent>
    </Tooltip>
  );
}

function withConfidenceMetric(card: Coverage): Coverage {
  const summary = summarizeConfidence(card);
  if (!summary) return card;
  return {
    ...card,
    metrics: {
      ...card.metrics,
      calibration_error: {
        decisions: summary.count,
        groups: card.metrics.top1_reference_mass.groups,
        mean: summary.error,
      },
    },
  };
}

function comparisonGroups(
  comparisons: Comparisons,
  names: Record<string, string>,
  baselineKey: string,
  colorIndices: Record<string, number>
) {
  const benchmarks = new Map<string, Map<string, ModelRow>>();
  Object.entries(comparisons).forEach(([key, modes]) => {
    const comparison = modes.raw;
    if (!comparison?.baseline || !comparison?.candidate) return;
    for (const [modelKey, name, coverage, adjusted] of [
      [
        baselineKey,
        names[baselineKey] ?? "Baseline",
        comparison.baseline,
        modes.calibrated?.baseline,
      ],
      [key, names[key] ?? key, comparison.candidate, modes.calibrated?.candidate],
    ] as const) {
      for (const [benchmark, card] of Object.entries(coverage.benchmarks)) {
        const adjustedCard = adjusted?.benchmarks[benchmark];
        if (!benchmarks.has(benchmark)) benchmarks.set(benchmark, new Map());
        benchmarks.get(benchmark)!.set(modelKey, {
          adjusted: adjustedCard ? withConfidenceMetric(adjustedCard) : undefined,
          card: withConfidenceMetric(card),
          colorIndex: colorIndices[modelKey],
          delta: modelKey === baselineKey ? undefined : comparison.benchmarks[benchmark]?.metrics,
          key: modelKey,
          name,
        });
      }
    }
  });
  return [...benchmarks].map(([name, models]) => {
    const rows = [...models.values()];
    return { metrics: measuredMetrics(rows), name, rows };
  });
}

function ConfidenceGroup({ name, rows }: { name: string; rows: ModelRow[] }) {
  return (
    <ConfidenceMetric
      benchmark={name}
      rows={rows.map((row) => ({
        ...row,
        adjusted: row.adjusted ? summarizeConfidence(row.adjusted) : null,
        original: summarizeConfidence(row.card),
      }))}
    />
  );
}

type ComparisonProps = {
  colorIndices: Record<string, number>;
  comparisons: Comparisons;
  names: Record<string, string>;
  baselineKey?: string;
};

export function ComparisonMetrics({
  colorIndices,
  comparisons,
  names,
  baselineKey = "base",
}: ComparisonProps) {
  const { sort, setSort, sortRows } = useTableSort<string>();
  const groups = useMemo(
    () => comparisonGroups(comparisons, names, baselineKey, colorIndices),
    [comparisons, names, baselineKey, colorIndices]
  );
  const metrics = useMemo(() => [...new Set(groups.flatMap((group) => group.metrics))], [groups]);
  const rows = groups.flatMap((group) =>
    sortRows(
      group.rows.map((row) => ({ ...row, benchmark: group.name, metrics: group.metrics })),
      (row, key) =>
        key === "model"
          ? row.name
          : key === "scored"
            ? row.card.scored
            : row.metrics.includes(key.slice(7))
              ? row.card.metrics[key.slice(7)]?.mean
              : undefined
    )
  );
  const columns = useMemo<ColumnDef<ComparisonRow>[]>(
    () => [
      {
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-2">
            <ModelProviderChip compact model={row.original.name} />
            {row.original.key === baselineKey && (
              <Badge className="shrink-0" size="chip" variant="neutral">
                Baseline
              </Badge>
            )}
          </div>
        ),
        header: "Model",
        id: "model",
        meta: { label: "Model", orderingField: "model" },
        size: 320,
      },
      ...metrics.map(
        (metric): ColumnDef<ComparisonRow> => ({
          cell: ({ row }) =>
            row.original.metrics.includes(metric) ? (
              <MetricValue metric={metric} row={row.original} />
            ) : (
              <span className="text-xs text-muted-foreground">—</span>
            ),
          header: () => <span title={descriptions[metric]}>{label(metric)}</span>,
          id: `metric:${metric}`,
          meta: { label: label(metric), orderingField: `metric:${metric}` },
          size: metric === "calibration_error" ? 160 : 144,
        })
      ),
      {
        cell: ({ row }) => <CoverageValue row={row.original} />,
        header: "Scored",
        id: "scored",
        meta: { label: "Scored", orderingField: "scored" },
        size: 128,
      },
    ],
    [metrics, baselineKey]
  );
  if (!groups.length) return null;
  return (
    <TooltipProvider>
      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <section aria-label={group.name} className="space-y-3" key={group.name}>
            {groups.length > 1 && <h3 className="text-sm font-medium">{group.name}</h3>}
            <div className="grid items-start gap-4 xl:grid-cols-2">
              {group.metrics
                .filter((metric) => metric !== "calibration_error")
                .map((metric) => {
                  const rows = group.rows.flatMap((row) => {
                    const value = row.card.metrics[metric]?.mean;
                    return typeof value === "number" && Number.isFinite(value)
                      ? [{ colorIndex: row.colorIndex, key: row.key, name: row.name, value }]
                      : [];
                  });
                  return rows.length ? (
                    <MetricBars
                      benchmark={group.name}
                      description={descriptions[metric]}
                      key={JSON.stringify([group.name, metric])}
                      percentage={percentages.has(metric)}
                      rows={rows}
                      title={label(metric)}
                    />
                  ) : null;
                })}
              <ConfidenceGroup {...group} />
            </div>
          </section>
        ))}
        <section aria-label="Model comparison" className="min-w-0">
          <DataTable
            className="min-w-0"
            columns={columns}
            data={{ count: rows.length, results: rows }}
            getGroupKey={groups.length > 1 ? (row) => row.benchmark : undefined}
            getRowId={(row) => JSON.stringify([row.benchmark, row.key])}
            hidePagination
            onOrderingChange={(ordering) => {
              const next = parseDrfOrdering(ordering);
              setSort(next ? { dir: next.dir, key: next.field } : null);
            }}
            onPageChange={() => {}}
            onPageSizeChange={() => {}}
            ordering={sort ? `${sort.dir === "desc" ? "-" : ""}${sort.key}` : undefined}
            page={1}
            pageSize={rows.length}
            renderGroupHeader={(name) => name}
            storageKey="native-model-comparison"
            toolbar={<h3 className={TITLE.card}>Model comparison</h3>}
          />
        </section>
      </div>
    </TooltipProvider>
  );
}

export function PairedMetrics({
  benchmarks,
}: {
  benchmarks: Record<
    string,
    {
      expected: number;
      paired_decisions: number;
      baseline_only: number;
      candidate_only: number;
      metrics: Record<string, Metric>;
    }
  >;
}) {
  const { sort, onSort, sortRows } = useTableSort<
    "name" | "difference" | "interval" | "paired" | "unpaired"
  >();
  const rows = Object.entries(benchmarks).flatMap(([benchmark, card]) =>
    Object.entries(card.metrics).map(([metric, score]) => ({ benchmark, card, metric, score }))
  );
  const sortedRows = sortRows(rows, ({ benchmark, card, metric, score }, key) => {
    if (key === "name") return `${benchmark} · ${label(metric)}`;
    if (key === "difference") return score.candidate_minus_baseline;
    if (key === "interval") return score.interval_95?.[0];
    if (key === "paired") return card.paired_decisions;
    return card.baseline_only + card.candidate_only;
  });
  return (
    <Card className="overflow-x-auto">
      <h3 className="border-b border-border px-5 py-4 text-sm font-medium">
        Paired metrics · model minus baseline
      </h3>
      <Table>
        <TableHeader>
          <TableRow>
            <SortableHeader label="Benchmark / metric" onSort={onSort} sort={sort} sortKey="name" />
            <SortableHeader label="Difference" onSort={onSort} sort={sort} sortKey="difference" />
            <SortableHeader label="95% interval" onSort={onSort} sort={sort} sortKey="interval" />
            <SortableHeader
              label="Paired / expected"
              onSort={onSort}
              sort={sort}
              sortKey="paired"
            />
            <SortableHeader label="Unpaired" onSort={onSort} sort={sort} sortKey="unpaired" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sortedRows.map(({ benchmark, card, metric, score }) => (
            <TableRow key={`${benchmark}:${metric}`}>
              <TableCell>
                {benchmark} · {labels[metric] ?? metric.replaceAll("_", " ")}
              </TableCell>
              <TableCell>{number(score.candidate_minus_baseline)}</TableCell>
              <TableCell>{score.interval_95?.map(number).join(" to ") ?? "—"}</TableCell>
              <TableCell>
                {card.paired_decisions} / {card.expected}
              </TableCell>
              <TableCell>{card.baseline_only + card.candidate_only}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}
