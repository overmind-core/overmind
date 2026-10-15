import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { MetricBars } from "@/components/evaluations/metric-bars";
import { HeaderStat } from "@/components/finetuning/finetuning-chrome";
import { MetricSeriesChart } from "@/components/finetuning/loss-chart";
import { MonitorChartCard } from "@/components/finetuning/monitor-chart-card";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { seriesColor } from "@/lib/colors";
import { humanizeKey } from "@/lib/label-case";
import type { DailyOverview, ObservabilityOverview as OverviewData } from "@/openapi";

type Days = 7 | 30 | 90;
type TraceFilters = { capability?: string; model?: string };

const count = new Intl.NumberFormat("en-GB");
const money = (value: number) => `$${value.toFixed(value < 0.01 ? 4 : 2)}`;
const duration = (value: number) =>
  value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${Math.round(value)}ms`;
const rate = (part: number, whole: number) =>
  whole ? `${((part / whole) * 100).toFixed(1)}%` : "—";

function DailyTrend({
  daily,
  measure,
  format,
  name,
}: {
  daily: DailyOverview[];
  measure: (day: DailyOverview) => number | null;
  format: (value: number) => string;
  name: string;
}) {
  const points = daily.flatMap((day, index) => {
    const value = measure(day);
    return value == null ? [] : [{ step: index + 1, value }];
  });
  return points.length ? (
    <MetricSeriesChart
      color={seriesColor(0)}
      data={points}
      height={180}
      name={name}
      valueFormatter={format}
      xLabel="day"
    />
  ) : null;
}

function OverviewContent({
  data,
  onOpenTrace,
  onOpenTraces,
  onOpenSignal,
}: {
  data: OverviewData;
  onOpenTrace: (id: string) => void;
  onOpenTraces: (filters?: TraceFilters) => void;
  onOpenSignal: (signal?: string) => void;
}) {
  if (!data.runs && !data.issues.length) {
    return (
      <EmptyState
        action={
          <Button onClick={() => onOpenTraces()} size="sm" variant="secondary">
            Explore traces
          </Button>
        }
        description="Metrics appear when this project receives traces."
        icon={Icon.observability}
        title="No traces in this period"
      />
    );
  }
  const failedChecks = data.issues.filter((issue) => issue.kind === "check" && issue.occurrences);
  const signals = data.issues.filter((issue) => issue.kind === "signal");
  return (
    <div className="flex flex-col gap-5 pb-6">
      {data.truncated && (
        <Alert variant="warning">
          Showing the latest {count.format(data.sampledSpans)} of {count.format(data.spanCount)}{" "}
          spans. Run and measurement figures describe this sample; choose a shorter period for a
          complete view.
        </Alert>
      )}
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 border-b border-border/70 pb-4">
        <HeaderStat label="Runs" primary>
          {count.format(data.runs)}
        </HeaderStat>
        <HeaderStat label="Sessions">{count.format(data.sessions)}</HeaderStat>
        <HeaderStat label="Scored checks">{count.format(data.checks)}</HeaderStat>
        <HeaderStat label="Spans received">{count.format(data.spanCount)}</HeaderStat>
        <Button className="ml-auto" onClick={() => onOpenTraces()} size="sm" variant="secondary">
          Explore traces <Icon.forward />
        </Button>
      </div>

      <section aria-label="Behaviour metrics" className="flex flex-col gap-2">
        <h3 className="text-xs text-muted-foreground">Metrics</h3>
        <div className="grid gap-x-6 gap-y-5 md:grid-cols-2">
          <MonitorChartCard
            emptyHint="No scored checks in this period"
            help="The share of scored checks at or above their pass threshold. Missing and errored checks do not become failures."
            subtitle="checks passing · daily UTC"
            title="Quality"
            values={[
              {
                label: "Period",
                value:
                  data.checkPassRate == null ? "—" : `${(data.checkPassRate * 100).toFixed(1)}%`,
              },
              { label: "Checks", value: count.format(data.checks) },
              { label: "Failed", value: count.format(data.failedChecks) },
            ]}
          >
            <DailyTrend
              daily={data.daily}
              format={(value) => `${value.toFixed(1)}%`}
              measure={(day) => (day.checks ? (day.passedChecks / day.checks) * 100 : null)}
              name="Check pass rate"
            />
          </MonitorChartCard>
          <MonitorChartCard
            emptyHint="No runs in this period"
            help="A run counts once when any of its spans has an error status."
            subtitle="runs with errors · daily UTC"
            title="Reliability"
            values={[
              { label: "Period", value: rate(data.errorRuns, data.runs) },
              { label: "Error runs", value: count.format(data.errorRuns) },
              { label: "Runs", value: count.format(data.runs) },
            ]}
          >
            <DailyTrend
              daily={data.daily}
              format={(value) => `${value.toFixed(1)}%`}
              measure={(day) => (day.runs ? (day.errorRuns / day.runs) * 100 : null)}
              name="Runs with errors"
            />
          </MonitorChartCard>
          <MonitorChartCard
            emptyHint="No root durations recorded"
            help="Duration is measured on recorded root spans. A missing root duration is excluded."
            subtitle="p95 run duration · daily UTC"
            title="Speed"
            values={[
              {
                label: "Period p95",
                value: data.p95DurationMs == null ? "—" : duration(data.p95DurationMs),
              },
              { label: "Measured", value: count.format(data.measuredDurationRuns) },
              { label: "Runs", value: count.format(data.runs) },
            ]}
          >
            <DailyTrend
              daily={data.daily}
              format={duration}
              measure={(day) => day.p95DurationMs}
              name="p95 duration"
            />
          </MonitorChartCard>
          <MonitorChartCard
            emptyHint="No cost data recorded"
            help="Recorded USD usage from model calls. Runs with missing cost are excluded, and this is not a billing total."
            subtitle="average recorded per run · daily UTC"
            title="Cost"
            values={[
              {
                label: "Period mean",
                value: data.averageCostUsd == null ? "—" : money(data.averageCostUsd),
              },
              { label: "Measured", value: count.format(data.measuredCostRuns) },
              { label: "Runs", value: count.format(data.runs) },
            ]}
          >
            <DailyTrend
              daily={data.daily}
              format={money}
              measure={(day) => day.averageCostUsd}
              name="Average recorded cost"
            />
          </MonitorChartCard>
        </div>
      </section>

      <section aria-label="Recurring behaviour" className="flex flex-col gap-2">
        <h3 className="text-xs text-muted-foreground">Recurring behaviour</h3>
        <div className="grid items-start gap-4 xl:grid-cols-2">
          <MetricBars
            benchmark="Recorded failures"
            description="Failed checks · select a bar to inspect a run"
            emptyHint="No failed checks recorded in this period."
            onSelect={(key) => {
              const trace = failedChecks.find((issue) => issue.name === key)?.examples[0];
              if (trace) onOpenTrace(trace);
            }}
            rows={failedChecks.map((issue, index) => ({
              colorIndex: index,
              key: issue.name,
              name: `${issue.name} · ${count.format(issue.affectedRuns ?? 0)} runs`,
              value: issue.occurrences,
            }))}
            title="Failed checks"
            valueFormatter={(value) => count.format(value)}
          />
          <MetricBars
            benchmark="Signal events"
            description="Recorded signal events · select a bar to investigate"
            emptyHint="No signal events recorded in this period."
            onSelect={onOpenSignal}
            rows={signals.map((issue, index) => ({
              colorIndex: index,
              key: issue.name,
              name: humanizeKey(issue.name),
              value: issue.occurrences,
            }))}
            title="Signals"
            valueFormatter={(value) => count.format(value)}
          />
        </div>
      </section>

      <section aria-label="Breakdown" className="flex flex-col gap-2">
        <h3 className="text-xs text-muted-foreground">Breakdown</h3>
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Source</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Activity</TableHead>
                <TableHead>Problems</TableHead>
                <TableHead>Recorded cost</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.capabilities.map((item) => (
                <TableRow key={`capability:${item.id}`}>
                  <TableCell>
                    <button
                      className="text-left hover:underline"
                      onClick={() => onOpenTraces({ capability: item.id })}
                      type="button"
                    >
                      {item.name}
                    </button>
                  </TableCell>
                  <TableCell>Capability</TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.runs)} runs
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.errorRuns)} error runs · {count.format(item.failedChecks)}{" "}
                    failed checks
                  </TableCell>
                  <TableCell>—</TableCell>
                </TableRow>
              ))}
              {data.models.map((item) => (
                <TableRow key={`model:${item.name}`}>
                  <TableCell>
                    <button
                      className="text-left hover:underline"
                      onClick={() => onOpenTraces({ model: item.name })}
                      type="button"
                    >
                      {item.name}
                    </button>
                  </TableCell>
                  <TableCell>Model</TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.calls)} calls
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.errorCalls)} errors
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {item.recordedCostUsd == null ? "—" : money(item.recordedCostUsd)}
                  </TableCell>
                </TableRow>
              ))}
              {data.tools.map((item) => (
                <TableRow key={`tool:${item.name}`}>
                  <TableCell>
                    <button
                      className="text-left hover:underline"
                      onClick={() => onOpenTrace(item.exampleTraceId)}
                      type="button"
                    >
                      {item.name}
                    </button>
                  </TableCell>
                  <TableCell>Tool</TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.calls)} calls
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {count.format(item.errorCalls)} errors
                  </TableCell>
                  <TableCell>—</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <p className="text-xs text-muted-foreground">
          Coverage: {count.format(data.scoredSpans)} / {count.format(data.sampledSpans)} spans have
          numeric scores; {count.format(data.measuredCostRuns)} / {count.format(data.runs)} runs
          have cost data. Missing measurements are not zero.
        </p>
      </section>
    </div>
  );
}

export function ObservabilityOverview({
  projectId,
  days,
  onDaysChange,
  onOpenTrace,
  onOpenTraces,
  onOpenSignal,
}: {
  projectId: string;
  days: Days;
  onDaysChange: (days: Days) => void;
  onOpenTrace: (id: string) => void;
  onOpenTraces: (filters?: TraceFilters) => void;
  onOpenSignal: (signal?: string) => void;
}) {
  const query = useQuery({
    queryFn: () =>
      apiClient.observability.observabilityOverviewRetrieve({ days, project: projectId }),
    queryKey: ["observability-overview", projectId, days],
    refetchInterval: 30_000,
  });
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-7xl space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            Project behaviour · UTC
            {query.data?.latestReceivedAt &&
              ` · last received ${query.data.latestReceivedAt.toLocaleString("en-GB")}`}
          </p>
          <div className="flex items-center gap-2">
            <Select
              onValueChange={(value) => onDaysChange(Number(value) as Days)}
              value={String(days)}
            >
              <SelectTrigger aria-label="Dashboard period" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="7">Last 7 days</SelectItem>
                <SelectItem value="30">Last 30 days</SelectItem>
                <SelectItem value="90">Last 90 days</SelectItem>
              </SelectContent>
            </Select>
            <Button
              aria-label="Refresh dashboard"
              disabled={query.isFetching}
              onClick={() => query.refetch()}
              size="icon-sm"
              variant="ghost"
            >
              <Icon.refresh />
            </Button>
          </div>
        </div>
        {query.isLoading ? (
          <LoadingState label="Loading behaviour overview" />
        ) : query.isError ? (
          <EmptyState
            action={
              <Button onClick={() => query.refetch()} size="sm" variant="secondary">
                Retry
              </Button>
            }
            description="Try again to load the recorded metrics."
            size="section"
            title="Couldn't load the overview"
          />
        ) : query.data ? (
          <OverviewContent
            data={query.data}
            onOpenSignal={onOpenSignal}
            onOpenTrace={onOpenTrace}
            onOpenTraces={onOpenTraces}
          />
        ) : null}
      </div>
    </div>
  );
}
