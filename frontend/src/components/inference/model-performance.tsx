import { type ReactNode, useState } from "react";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { DetailErrorState } from "@/components/route-error";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import { SectionCard } from "@/components/ui/section-card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  type InferencePeriod,
  type InferenceTrafficSource,
  useModelActivityQuery,
  useModelMetricsQuery,
} from "@/hooks/use-inference";
import { formatCost } from "@/lib/formatters";
import { TITLE } from "@/lib/typography";
import type { InferenceActivityPoint } from "@/openapi";

const PERIODS: { value: InferencePeriod; label: string; granularity: "minute" | "hour" | "day" }[] =
  [
    { granularity: "minute", label: "Last hour", value: "1h" },
    { granularity: "hour", label: "Last 24 hours", value: "24h" },
    { granularity: "hour", label: "Last 7 days", value: "7d" },
    { granularity: "day", label: "Last 30 days", value: "30d" },
    { granularity: "day", label: "All time", value: "all" },
  ];

const integer = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString();
const latency = (ms: number | null | undefined) =>
  ms == null ? "—" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;

export function ModelPerformance({
  modelId,
  onConnect,
}: {
  modelId: string;
  onConnect: () => void;
}) {
  const [period, setPeriod] = useState<InferencePeriod>("24h");
  const [source, setSource] = useState<InferenceTrafficSource>("application");
  const selected = PERIODS.find((item) => item.value === period)!;
  const metricsQuery = useModelMetricsQuery(modelId, period, source);
  const activityQuery = useModelActivityQuery(modelId, selected.granularity, period, source);
  const metrics = metricsQuery.data;
  const points = activityQuery.data?.points ?? [];
  const loading = metricsQuery.isPending;
  const error = metricsQuery.error ?? activityQuery.error;
  const refresh = () => {
    void metricsQuery.refetch();
    void activityQuery.refetch();
  };
  const successRate = metrics?.requestCount
    ? ((metrics.requestCount - metrics.failedRequestCount) / metrics.requestCount) * 100
    : null;
  const hasTraffic = !!metrics?.requestCount || points.some((point) => point.requestCount > 0);

  return (
    <section aria-label="Inference performance" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <h2 className={TITLE.section}>Inference performance</h2>
          <p className="text-xs text-muted-foreground">
            {error ? "Refresh interrupted" : "Refreshes every 15 seconds"}
            {metricsQuery.dataUpdatedAt ? (
              <>
                {" "}
                · Updated <DateTime value={new Date(metricsQuery.dataUpdatedAt)} />
              </>
            ) : null}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select
            onValueChange={(value) => setSource(value as InferenceTrafficSource)}
            value={source}
          >
            <SelectTrigger aria-label="Traffic source" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="application">Application traffic</SelectItem>
              <SelectItem value="all">All traffic</SelectItem>
            </SelectContent>
          </Select>
          <Select onValueChange={(value) => setPeriod(value as InferencePeriod)} value={period}>
            <SelectTrigger aria-label="Reporting period" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PERIODS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            aria-label="Refresh performance"
            disabled={metricsQuery.isFetching || activityQuery.isFetching}
            onClick={refresh}
            size="icon-sm"
            variant="secondary"
          >
            <Icon.refresh />
          </Button>
        </div>
      </div>

      {error && metrics ? (
        <Alert variant="warning">
          <Icon.warning />
          <div className="flex flex-1 flex-wrap items-center justify-between gap-2">
            <span>
              Performance data could not refresh. The last available measurements are shown.
            </span>
            <Button onClick={refresh} size="sm" variant="secondary">
              Retry
            </Button>
          </div>
        </Alert>
      ) : null}

      {metricsQuery.error && !metrics ? (
        <DetailErrorState
          action={
            <Button onClick={refresh} size="sm" variant="secondary">
              Retry
            </Button>
          }
          error={metricsQuery.error}
          fallback="Couldn't load inference performance."
        />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5 [&>:last-child]:col-span-2 lg:[&>:last-child]:col-span-1">
            <Metric
              detail={`${integer(metrics?.totalTokens)} tokens processed`}
              label="Requests"
              loading={loading}
              value={integer(metrics?.requestCount)}
            />
            <Metric
              detail={`${integer(metrics?.failedRequestCount)} failed requests`}
              label="Success rate"
              loading={loading}
              value={successRate == null ? "—" : `${successRate.toFixed(1)}%`}
            />
            <Metric
              detail={`p95 ${latency(metrics?.endToEndP95Ms)} · end to end`}
              label="Response time · p50"
              loading={loading}
              value={latency(metrics?.endToEndP50Ms)}
            />
            <Metric
              detail="Median · warm requests"
              label="Generation speed"
              loading={loading}
              value={
                metrics?.avgTokensPerSecond == null
                  ? "—"
                  : `${metrics.avgTokensPerSecond.toFixed(1)} tok/s`
              }
            />
            <Metric
              detail={
                metrics && metrics.costRecordedRequestCount < metrics.requestCount
                  ? `Estimated · ${integer(metrics.costRecordedRequestCount)} of ${integer(metrics.requestCount)} requests`
                  : "Estimated GPU spend"
              }
              label="Serving cost"
              loading={loading}
              value={metrics?.cost === 0 ? "$0.00" : formatCost(metrics?.cost)}
            />
          </div>

          {metrics?.latestFailure ? (
            <Alert variant="destructive">
              <Icon.failed />
              <div className="min-w-0 space-y-1">
                <p>
                  Latest failure: {metrics.latestFailure.errorCode} ·{" "}
                  <DateTime value={metrics.latestFailure.createdAt} />
                </p>
                <p className="break-all font-mono text-xs select-all">
                  Request {metrics.latestFailure.id}
                </p>
              </div>
            </Alert>
          ) : null}

          {loading ? (
            <Skeleton className="h-56 w-full rounded-md" />
          ) : !hasTraffic && !error ? (
            <Card>
              <EmptyState
                action={
                  <Button onClick={onConnect} size="sm" variant="secondary">
                    <Icon.terminal />
                    View API snippet
                  </Button>
                }
                className="py-8"
                description={
                  source === "application"
                    ? "Requests made with an API key to the live alias or this model's pinned ID appear here."
                    : "Completed inference requests, including internal evaluations, appear here."
                }
                icon={Icon.inference}
                size="section"
                title={`No ${source === "application" ? "application " : ""}requests ${period === "all" ? "recorded" : `in the ${selected.label.toLowerCase()}`}`}
              />
            </Card>
          ) : activityQuery.isPending ? (
            <Skeleton className="h-64 w-full rounded-md" />
          ) : activityQuery.error && !activityQuery.data ? (
            <DetailErrorState
              action={
                <Button onClick={refresh} size="sm" variant="secondary">
                  Retry charts
                </Button>
              }
              error={activityQuery.error}
              fallback="Couldn't load inference activity."
            />
          ) : hasTraffic ? (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <PerformanceChart
                description={`Requests per ${selected.granularity}`}
                format={integer}
                granularity={selected.granularity}
                integerTicks
                points={points}
                series={[
                  { color: "var(--chart-1)", key: "requestCount", label: "Requests" },
                  { color: "var(--destructive)", key: "failedRequestCount", label: "Failed" },
                ]}
                title="Request traffic"
              />
              <PerformanceChart
                description="End-to-end milliseconds · includes cold starts"
                format={latency}
                granularity={selected.granularity}
                points={points}
                series={[
                  { color: "var(--chart-1)", key: "endToEndP50Ms", label: "p50" },
                  { color: "var(--chart-4)", key: "endToEndP95Ms", label: "p95" },
                ]}
                title="Response time"
              />
              <PerformanceChart
                description="Median tokens per second · warm requests"
                format={(value) => (value == null ? "—" : `${value.toFixed(1)} tok/s`)}
                granularity={selected.granularity}
                points={points}
                series={[{ color: "var(--chart-2)", key: "avgTokensPerSecond", label: "tok/s" }]}
                title="Generation speed"
              />
            </div>
          ) : null}

          {metrics && hasTraffic ? (
            <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
              <span>
                Input tokens{" "}
                <span className="text-foreground">{integer(metrics.promptTokens)}</span>
              </span>
              <span>
                Output tokens{" "}
                <span className="text-foreground">{integer(metrics.completionTokens)}</span>
              </span>
              <span title="Coldness is estimated from recent inference activity.">
                Cold requests (estimated){" "}
                <span className="text-foreground">{integer(metrics.coldRequestCount)}</span>
              </span>
              <span>
                Warm response p50{" "}
                <span className="text-foreground">{latency(metrics.avgLatencyMs)}</span>
              </span>
              {metrics.lastRequestAt ? (
                <span className="ml-auto">
                  Last request <DateTime value={metrics.lastRequestAt} />
                </span>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function Metric({
  label,
  value,
  detail,
  loading,
}: {
  label: string;
  value: ReactNode;
  detail: string;
  loading: boolean;
}) {
  return (
    <Card className="min-w-0 space-y-3 p-4">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {loading ? (
        <Skeleton className="h-7 w-20" />
      ) : (
        <p className="font-mono text-xl leading-none tabular-nums">{value}</p>
      )}
      <p className="text-xs text-muted-foreground">{detail}</p>
    </Card>
  );
}

type Series = {
  key:
    | "requestCount"
    | "failedRequestCount"
    | "endToEndP50Ms"
    | "endToEndP95Ms"
    | "avgTokensPerSecond";
  label: string;
  color: string;
};

function PerformanceChart({
  title,
  description,
  points,
  granularity,
  series,
  format,
  integerTicks = false,
}: {
  title: string;
  description: string;
  points: InferenceActivityPoint[];
  granularity: "minute" | "hour" | "day";
  series: Series[];
  format: (value: number | null | undefined) => string;
  integerTicks?: boolean;
}) {
  const hasMeasurements = points.some((point) => series.some((item) => point[item.key] != null));
  const timeLabel = (value: number) =>
    new Date(value).toLocaleString(
      undefined,
      granularity === "minute"
        ? { hour: "2-digit", minute: "2-digit" }
        : granularity === "hour"
          ? { day: "numeric", hour: "2-digit", month: "short" }
          : { day: "numeric", month: "short" }
    );
  return (
    <SectionCard contentClassName="space-y-3" title={title}>
      <p className="text-xs text-muted-foreground">{description}</p>
      <div aria-label={`${title} chart`} className="h-44 min-w-0" role="img">
        {hasMeasurements ? (
          <ResponsiveContainer height="100%" width="100%">
            <LineChart
              data={points.map((point) => ({ ...point, time: point.bucket.getTime() }))}
              margin={{ bottom: 0, left: -12, right: 8, top: 8 }}
            >
              <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                axisLine={false}
                dataKey="time"
                domain={["dataMin", "dataMax"]}
                minTickGap={40}
                tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                tickFormatter={timeLabel}
                tickLine={false}
                type="number"
              />
              <YAxis
                allowDecimals={!integerTicks}
                axisLine={false}
                domain={[0, "auto"]}
                tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                tickFormatter={(value) =>
                  Number(value).toLocaleString(undefined, {
                    maximumFractionDigits: 1,
                    notation: "compact",
                  })
                }
                tickLine={false}
                width={48}
              />
              <Tooltip
                content={({ active, payload, label }) => (
                  <GraphTooltip
                    active={active}
                    label={label != null ? new Date(Number(label)).toLocaleString() : undefined}
                    payload={payload}
                    valueFormatter={format}
                  />
                )}
              />
              {series.map((item) => (
                <Line
                  connectNulls={false}
                  dataKey={item.key}
                  dot={{ fill: item.color, r: 2, strokeWidth: 0 }}
                  isAnimationActive={false}
                  key={item.key}
                  name={item.label}
                  stroke={item.color}
                  strokeWidth={1.5}
                  type="linear"
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="flex h-full items-center justify-center text-xs text-muted-foreground">
            No measurements for this period
          </div>
        )}
      </div>
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        {series.map((item) => (
          <span className="inline-flex items-center gap-1.5" key={item.key}>
            <span className="size-1.5 rounded-xs" style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
    </SectionCard>
  );
}
