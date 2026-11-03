import { Icon } from "@/components/ui/icons";
import { lazyChart } from "@/components/ui/lazy-chart";
import { Skeleton } from "@/components/ui/skeleton";
import { type IssueScope, useObservabilityTrends } from "@/hooks/use-issues";
import { formatCost, formatNumber } from "@/lib/formatters";
import type { ObserveSearch } from "@/lib/schemas";
import { cn } from "@/lib/utils";
import type { TrendTotals } from "@/openapi";
import { formatRate, SectionHeading } from "./issue-parts";

const CHART_HEIGHT = 224;
const TrendChart = lazyChart(() => import("@/components/observe/trend-chart"), {
  minHeight: CHART_HEIGHT,
});

type Metric = ObserveSearch["metric"];

// `worse` says which way is bad, so a change reads as good or bad without a legend.
const METRICS: {
  key: Metric;
  label: string;
  color: string;
  rule: string;
  worse?: "up" | "down";
  format: (value: number) => string;
}[] = [
  {
    color: "var(--info)",
    format: (value) => formatNumber(value),
    key: "traces",
    label: "Traces",
    rule: "border-t-info",
  },
  {
    color: "var(--destructive)",
    format: (value) => formatRate(value),
    key: "failureRate",
    label: "Failure rate",
    rule: "border-t-destructive",
    worse: "up",
  },
  {
    color: "var(--success)",
    format: (value) => value.toFixed(2),
    key: "score",
    label: "Score",
    rule: "border-t-success",
    worse: "down",
  },
  {
    color: "var(--warning)",
    format: (value) => (value >= 1000 ? `$${formatNumber(Math.round(value))}` : formatCost(value)),
    key: "cost",
    label: "Cost",
    rule: "border-t-warning",
    worse: "up",
  },
];

function Change({
  comparable,
  current,
  previous,
  worse,
  format,
}: {
  comparable: boolean;
  current: number | null;
  previous: number | null;
  worse?: "up" | "down";
  format: (value: number) => string;
}) {
  if (!comparable || previous == null)
    return <span className="text-xs text-muted-foreground">No earlier data</span>;
  const direction =
    current == null || current === previous ? null : current > previous ? "up" : "down";
  const Arrow = direction === "up" ? Icon.arrowUp : Icon.arrowDown;
  return (
    <span className="flex items-center gap-1 text-xs tabular-nums text-muted-foreground">
      {direction && (
        <Arrow
          aria-hidden
          className={cn(
            "size-3",
            worse && (direction === worse ? "text-destructive" : "text-success")
          )}
        />
      )}
      from {format(previous)}
    </span>
  );
}

export function TrendPanel({
  scope,
  metric,
  onMetric,
}: {
  scope: IssueScope;
  metric: Metric;
  onMetric: (metric: Metric) => void;
}) {
  const { data } = useObservabilityTrends(scope);
  const selected = METRICS.find((item) => item.key === metric) ?? METRICS[1];
  const totals = (from: TrendTotals, key: Metric) => from[key] ?? null;

  return (
    <section aria-label="Trends">
      <SectionHeading>Trends</SectionHeading>
      {data ? (
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <div className="grid grid-cols-2 border-b border-border/70 lg:grid-cols-4">
            {METRICS.map((item) => {
              const current = totals(data.current, item.key);
              const active = item.key === metric;
              return (
                <button
                  aria-pressed={active}
                  className={cn(
                    "flex flex-col gap-1 border-t-2 border-t-transparent px-5 py-4 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring lg:not-last:border-r lg:not-last:border-r-border/70",
                    active ? cn("bg-wash-raised", item.rule) : "hover:bg-wash-subtle"
                  )}
                  key={item.key}
                  onClick={() => onMetric(item.key)}
                  type="button"
                >
                  <span
                    className={cn("text-xs", active ? "text-foreground" : "text-muted-foreground")}
                  >
                    {item.label}
                  </span>
                  <span className="text-2xl tabular-nums text-foreground">
                    {current == null ? "—" : item.format(current)}
                  </span>
                  <Change
                    comparable={data.comparable}
                    current={current}
                    format={item.format}
                    previous={totals(data.previous, item.key)}
                    worse={item.worse}
                  />
                </button>
              );
            })}
          </div>
          <div className="px-4 pt-3 pb-4">
            <TrendChart
              bucket={data.bucket}
              color={selected.color}
              data={data.points.map((point) => ({
                at: point.at.getTime(),
                value: point[metric] ?? null,
              }))}
              format={selected.format}
              height={CHART_HEIGHT}
              label={selected.label}
              markers={data.markers}
            />
          </div>
        </div>
      ) : (
        <Skeleton className="h-80 w-full rounded-md" />
      )}
    </section>
  );
}
