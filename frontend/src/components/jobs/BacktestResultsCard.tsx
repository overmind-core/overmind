import { Link } from "@tanstack/react-router";

import { BacktestRecommendations } from "@/components/jobs/JobCard";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

/** Format a number as percentage (0–1 → "0.0%") */
function formatPercent(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${(v * 100).toFixed(1)}%`;
}

/** Format latency in ms */
function formatLatency(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} ms`;
}

/** Format cost */
function formatCost(v: number | null | undefined): string {
  if (v == null) return "—";
  return `$${v.toFixed(4)}`;
}

/** Strip reasoning suffix for display (e.g. gpt-5.2:reasoning-medium → gpt-5.2) */
function displayModelName(key: string): string {
  return key.includes(":reasoning") ? key.split(":reasoning")[0] : key;
}

interface BacktestResult {
  backtest_run_id?: string;
  prompt_id?: string;
  current_model?: string;
  models_tested?: number;
  spans_tested?: number;
  spans_succeeded?: number;
  spans_failed?: number;
  model_results?: Record<
    string,
    { aggregate_metrics?: Record<string, number> }
  >;
  recommendations?: Record<string, unknown>;
  suggestion_id?: string;
}

interface BacktestResultsCardProps {
  result: BacktestResult;
  promptSlug?: string | null;
}

export function BacktestResultsCard({ result, promptSlug }: BacktestResultsCardProps) {
  const recommendations = result.recommendations;
  const hasRecommendations =
    recommendations &&
    typeof recommendations === "object" &&
    "summary" in recommendations;

  const modelResults = result.model_results;
  const modelEntries = modelResults
    ? Object.entries(modelResults).filter(
        ([, data]) => data?.aggregate_metrics && typeof data.aggregate_metrics === "object"
      )
    : [];

  const currentModel = result.current_model;

  return (
    <div className="space-y-6">
      {/* Summary stats */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatBlock
          label="Spans tested"
          value={result.spans_tested}
          subValue={
            result.spans_succeeded != null && result.spans_failed != null
              ? `${result.spans_succeeded} succeeded, ${result.spans_failed} failed`
              : undefined
          }
        />
        <StatBlock label="Models tested" value={result.models_tested} />
        <StatBlock
          label="Current model"
          value={currentModel ? displayModelName(currentModel) : "—"}
        />
        {result.suggestion_id && promptSlug && (
          <div className="col-span-2 sm:col-span-1 flex flex-col gap-1">
            <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
              Suggestion
            </span>
            <Link
              to="/agents/$slug"
              params={{ slug: promptSlug }}
              search={{ tab: "suggestions" }}
              className="text-sm font-medium text-primary underline-offset-4 hover:underline"
            >
              View suggestion →
            </Link>
          </div>
        )}
      </div>

      {/* Per-model comparison table */}
      {modelEntries.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Model comparison</h3>
          <div className="overflow-x-auto rounded-md border border-border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="font-medium">Model</TableHead>
                  <TableHead className="text-right">Avg score</TableHead>
                  <TableHead className="text-right">Success rate</TableHead>
                  <TableHead className="text-right">Avg latency</TableHead>
                  <TableHead className="text-right">Avg cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {modelEntries.map(([modelKey, { aggregate_metrics }]) => {
                  const agg = aggregate_metrics ?? {};
                  const isCurrent =
                    currentModel &&
                    displayModelName(modelKey) === displayModelName(currentModel);
                  return (
                    <TableRow
                      key={modelKey}
                      className={cn(isCurrent && "bg-muted/50")}
                    >
                      <TableCell className="font-medium">
                        <span className="flex items-center gap-2">
                          {displayModelName(modelKey)}
                          {isCurrent && (
                            <Badge variant="secondary" className="text-[0.65rem]">
                              Current
                            </Badge>
                          )}
                        </span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPercent(agg.avg_eval_score)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatPercent(agg.success_rate)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatLatency(agg.avg_latency_ms)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCost(agg.avg_cost_per_request)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      )}

      {/* Recommendations */}
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

function StatBlock({
  label,
  value,
  subValue,
}: {
  label: string;
  value?: string | number | null;
  subValue?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className="text-sm font-semibold text-foreground">
        {value != null ? String(value) : "—"}
      </p>
      {subValue && (
        <p className="text-xs text-muted-foreground">{subValue}</p>
      )}
    </div>
  );
}
