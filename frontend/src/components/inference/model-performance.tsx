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

import { ModelUsage } from "@/components/inference/model-usage";
import { DetailErrorState } from "@/components/route-error";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DateTime } from "@/components/ui/datetime";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  type InferencePeriod,
  type InferenceTrafficSource,
  useModelActivityQuery,
  useModelMetricsQuery,
} from "@/hooks/use-inference";
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

export function ModelPerformance({ modelId }: { modelId: string }) {
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
          <Card className="overflow-hidden">
            <div className="grid grid-cols-2 divide-x divide-border/70 lg:grid-cols-4">
              <Metric
                detail={
                  metrics?.lastRequestAt
                    ? `Last request ${metrics.lastRequestAt.toLocaleDateString()}`
                    : ""
                }
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
            </div>
          </Card>
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
            <ActivitySkeleton />
          ) : !hasTraffic && !error ? (
            <div className="px-1">
              <p className="text-sm text-muted-foreground">
                No {source === "application" ? "application " : ""}requests{" "}
                {period === "all" ? "recorded" : `in the ${selected.label.toLowerCase()}`}
              </p>
            </div>
          ) : activityQuery.isPending ? (
            <ActivitySkeleton />
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
            <Card className="p-3">
              <Tabs defaultValue="traffic">
                <TabsList
                  aria-label="Performance chart"
                  className="max-w-full justify-start overflow-x-auto"
                >
                  <TabsTrigger value="traffic">Requests</TabsTrigger>
                  <TabsTrigger value="latency">Response time</TabsTrigger>
                  <TabsTrigger value="speed">Generation speed</TabsTrigger>
                </TabsList>
                <TabsContent value="traffic">
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
                </TabsContent>
                <TabsContent value="latency">
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
                </TabsContent>
                <TabsContent value="speed">
                  <PerformanceChart
                    description="Median tokens per second · warm requests"
                    format={(value) => (value == null ? "—" : `${value.toFixed(1)} tok/s`)}
                    granularity={selected.granularity}
                    points={points}
                    series={[
                      { color: "var(--chart-2)", key: "avgTokensPerSecond", label: "tok/s" },
                    ]}
                    title="Generation speed"
                  />
                </TabsContent>
              </Tabs>
            </Card>
          ) : null}
        </>
      )}
      {metrics ? <ModelUsage metrics={metrics} /> : null}
    </section>
  );
}

function ActivitySkeleton() {
  return <Skeleton aria-label="Loading inference activity" className="h-52 w-full" />;
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
    <div className="min-w-0 space-y-1 px-3 py-3">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      {loading ? (
        <Skeleton className="h-7 w-20" />
      ) : (
        <p className="font-mono text-sm tabular-nums">{value}</p>
      )}
      {detail ? <p className="text-xs text-muted-foreground">{detail}</p> : null}
    </div>
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
    <div className="space-y-3 pt-2">
      <p className="text-xs text-muted-foreground">{description}</p>
      <div aria-label={`${title} chart`} className="h-52 min-w-0" role="img">
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
                tickFormatter={(value) => format(Number(value))}
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
    </div>
  );
}
