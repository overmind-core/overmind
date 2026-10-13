import { ScoreChip } from "@/components/evaluations/run-comparison";
import { Badge } from "@/components/ui/badge";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";

/** One matrix row's result, as stored in `EvalScore.metrics` and
 *  `Span.eval_attributes.metrics`. */
export interface MetricResult {
  type?: string;
  expectation?: string;
  score?: number | null;
  verdict?: string;
  reason?: string;
  error?: string;
}

export const metricResults = (metrics: unknown): [string, MetricResult][] =>
  metrics != null && typeof metrics === "object" && !Array.isArray(metrics)
    ? Object.entries(metrics as Record<string, MetricResult>).sort(([a], [b]) => a.localeCompare(b))
    : [];

export function MetricList({ metrics, className }: { metrics: unknown; className?: string }) {
  const entries = metricResults(metrics);
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">No metrics</p>;
  }
  return (
    <ul className={cn("flex flex-col gap-2", className)}>
      {entries.map(([name, metric]) => (
        <li className="rounded-sm border border-border bg-wash-subtle px-3 py-2" key={name}>
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-sm font-medium" title={name}>
              {name}
            </span>
            {metric.verdict === "abstain" ? (
              <Badge size="chip" variant="neutral">
                Abstained
              </Badge>
            ) : metric.error ? (
              <Badge size="chip" variant="error">
                Error
              </Badge>
            ) : (
              <ScoreChip value={metric.score} />
            )}
          </div>
          {(metric.error || metric.reason) && (
            <p
              className={cn(
                PROSE,
                "mt-1 text-xs",
                metric.error ? "text-destructive" : "text-muted-foreground"
              )}
            >
              {metric.error || metric.reason}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}
