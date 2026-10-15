import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { MetricSeriesChart } from "@/components/finetuning/loss-chart";
import { MonitorChartCard } from "@/components/finetuning/monitor-chart-card";
import { Button } from "@/components/ui/button";
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
  return points.length > 1 ? (
    <MetricSeriesChart
      color={seriesColor(0)}
      data={points}
      height={180}
      name={name}
      valueFormatter={format}
      xLabel="day"
    />
  ) : (
    <p className="px-3 py-4 text-sm text-muted-foreground">
      {points.length
        ? "One UTC day measured; a trend needs another day."
        : "No measurements in this period."}
    </p>
  );
}

function ReportRow({
  name,
  detail,
  value,
  onSelect,
}: {
  name: string;
  detail: string;
  value: string;
  onSelect?: () => void;
}) {
  const content = (
    <>
      <span className="min-w-0">
        <span className="block truncate text-sm text-foreground">{name}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </span>
      <span className="shrink-0 font-mono text-sm tabular-nums">{value}</span>
    </>
  );
  const className =
    "flex w-full items-start justify-between gap-3 border-b border-border/70 px-3 py-2.5 text-left last:border-b-0";
  return onSelect ? (
    <button
      className={`${className} hover:bg-wash-raised focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring`}
      onClick={onSelect}
      type="button"
    >
      {content}
    </button>
  ) : (
    <div className={className}>{content}</div>
  );
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
        description="Reporting appears when this project receives traces."
        icon={Icon.observability}
        title="No traces in this period"
      />
    );
  }

  const failedChecks = data.issues.filter((issue) => issue.kind === "check" && issue.occurrences);
  const signals = data.issues.filter((issue) => issue.kind === "signal");
  const capabilities = [...data.capabilities]
    .filter((item) => item.checks)
    .sort((a, b) => b.failedChecks / b.checks - a.failedChecks / a.checks)
    .slice(0, 5);
  const execution = [
    ...data.models.map((item) => ({
      ...item,
      kind: "Model",
      onSelect: () => onOpenTraces({ model: item.name }),
    })),
    ...data.tools.map((item) => ({
      ...item,
      kind: "Tool",
      onSelect: undefined,
    })),
  ]
    .filter((item) => item.calls && item.errorCalls)
    .sort((a, b) => b.errorCalls / b.calls - a.errorCalls / a.calls)
    .slice(0, 5);

  return (
    <div className="grid gap-5 pb-6 md:grid-cols-2 md:gap-6">
      <div className="flex min-w-0 flex-col gap-5">
        <MonitorChartCard
          emptyHint="No failed checks recorded"
          help="Checks with the most affected runs. Open an example and verify the check is judging the intended step before using it as an experiment target."
          subtitle="ranked by affected runs"
          title="Experiment candidates"
          values={[
            { label: "Candidates shown", value: count.format(failedChecks.length) },
            { label: "Failed verdicts", value: count.format(data.failedChecks) },
            { label: "Observed runs", value: count.format(data.runs) },
          ]}
        >
          {failedChecks.length ? (
            <div>
              {failedChecks.map((issue) => (
                <ReportRow
                  detail={`${count.format(issue.occurrences)} failed verdicts · ${count.format(issue.affectedRuns ?? 0)} affected runs`}
                  key={issue.name}
                  name={issue.name}
                  onSelect={issue.examples[0] ? () => onOpenTrace(issue.examples[0]) : undefined}
                  value={rate(issue.affectedRuns ?? 0, data.runs)}
                />
              ))}
            </div>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">No failed checks recorded.</p>
          )}
        </MonitorChartCard>

        <MonitorChartCard
          emptyHint="No scored checks in this period"
          help="Daily share of recorded check verdicts below the pass threshold. This is not verified task success; judge scope and rubric changes can affect the trend."
          subtitle="failed check verdicts · daily UTC"
          title="Behaviour trend"
          values={[
            { label: "Failed", value: rate(data.failedChecks, data.checks) },
            { label: "Scored checks", value: count.format(data.checks) },
            {
              label: "Days scored",
              value: count.format(data.daily.filter((day) => day.checks).length),
            },
          ]}
        >
          <DailyTrend
            daily={data.daily}
            format={(value) => `${value.toFixed(1)}%`}
            measure={(day) =>
              day.checks ? ((day.checks - day.passedChecks) / day.checks) * 100 : null
            }
            name="Failed check share"
          />
        </MonitorChartCard>

        <MonitorChartCard
          emptyHint="No root durations recorded"
          help="Daily p95 duration from recorded root spans. This can reveal a speed regression even when check verdicts stay stable."
          subtitle="p95 run duration · daily UTC"
          title="Latency"
          values={[
            {
              label: "Period p95",
              value: data.p95DurationMs == null ? "—" : duration(data.p95DurationMs),
            },
            { label: "Measured runs", value: count.format(data.measuredDurationRuns) },
            { label: "Observed runs", value: count.format(data.runs) },
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
          emptyHint="No recorded model cost"
          help="Daily average recorded model cost per run. Missing cost stays unmeasured and this is not a billing total."
          subtitle="average recorded cost · daily UTC"
          title="Cost"
          values={[
            {
              label: "Period mean",
              value: data.averageCostUsd == null ? "—" : money(data.averageCostUsd),
            },
            { label: "Measured runs", value: count.format(data.measuredCostRuns) },
            { label: "Observed runs", value: count.format(data.runs) },
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
      <div className="flex min-w-0 flex-col gap-5">
        <MonitorChartCard
          emptyHint="No capability checks recorded"
          help="Failed check verdicts divided by scored checks for each capability. This identifies where to investigate; different capabilities may use different checks."
          subtitle="failed check share by capability"
          title="Capability gaps"
          values={[
            { label: "Capabilities shown", value: count.format(capabilities.length) },
            { label: "Error runs", value: count.format(data.errorRuns) },
            { label: "Runs", value: count.format(data.runs) },
          ]}
        >
          {capabilities.length ? (
            <div>
              {capabilities.map((item) => (
                <ReportRow
                  detail={`${count.format(item.failedChecks)} / ${count.format(item.checks)} checks · ${count.format(item.runs)} runs`}
                  key={item.id}
                  name={item.name}
                  onSelect={() => onOpenTraces({ capability: item.id })}
                  value={rate(item.failedChecks, item.checks)}
                />
              ))}
            </div>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              No capability checks recorded.
            </p>
          )}
        </MonitorChartCard>

        <MonitorChartCard
          emptyHint="No model or tool errors recorded"
          help="Recorded error-status calls by model or tool. An error status does not establish whether the agent recovered or whether the task failed."
          subtitle="error-status calls by component"
          title="Execution friction"
          values={[
            {
              label: "Shown model calls",
              value: count.format(data.models.reduce((sum, item) => sum + item.calls, 0)),
            },
            {
              label: "Shown tool calls",
              value: count.format(data.tools.reduce((sum, item) => sum + item.calls, 0)),
            },
            { label: "Components shown", value: count.format(execution.length) },
          ]}
        >
          {execution.length ? (
            <div>
              {execution.map((item) => (
                <ReportRow
                  detail={`${item.kind} · ${count.format(item.errorCalls)} / ${count.format(item.calls)} calls`}
                  key={`${item.kind}:${item.name}`}
                  name={item.name}
                  onSelect={item.onSelect}
                  value={rate(item.errorCalls, item.calls)}
                />
              ))}
            </div>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">
              No model or tool errors recorded.
            </p>
          )}
        </MonitorChartCard>

        <MonitorChartCard
          emptyHint="No signal events recorded"
          help="Recurring detections to review alongside the trace. A signal is a lead, not an independently validated failure."
          subtitle="detections · inspect trace evidence"
          title="Signal patterns"
          values={[
            { label: "Signal types", value: count.format(signals.length) },
            {
              label: "Events shown",
              value: count.format(signals.reduce((sum, item) => sum + item.occurrences, 0)),
            },
            { label: "Sessions", value: count.format(data.sessions) },
          ]}
        >
          {signals.length ? (
            <div>
              {signals.map((issue) => (
                <ReportRow
                  detail="Open related signals"
                  key={issue.name}
                  name={humanizeKey(issue.name)}
                  onSelect={() => onOpenSignal(issue.name)}
                  value={count.format(issue.occurrences)}
                />
              ))}
            </div>
          ) : (
            <p className="px-3 py-4 text-sm text-muted-foreground">No signal events recorded.</p>
          )}
        </MonitorChartCard>

        <MonitorChartCard
          emptyHint="No evidence recorded"
          help="Shows how complete this report is. Check verdicts are not labelled task outcomes, and this trace report cannot calculate paired model lift."
          subtitle="limits of the current evidence"
          title="Evidence readiness"
          values={[
            {
              label: "Span sample",
              value: `${count.format(data.sampledSpans)} / ${count.format(data.spanCount)}`,
            },
            { label: "Cost coverage", value: rate(data.measuredCostRuns, data.runs) },
            { label: "Task outcome", value: "Unmeasured" },
          ]}
        >
          <div>
            <ReportRow
              detail={
                data.truncated
                  ? "Shorten the period for complete span sampling"
                  : "All received spans in this period included"
              }
              name="Span window"
              value={data.truncated ? "Sampled" : "Complete sample"}
            />
            <ReportRow
              detail="Recorded check verdicts may need scope and rubric review"
              name="Evaluator trust"
              value="Review"
            />
            <ReportRow
              detail="Requires comparable labelled examples and a baseline"
              name="Paired improvement"
              value="Unmeasured"
            />
          </div>
        </MonitorChartCard>
      </div>
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
        <div className="flex justify-end gap-2">
          <Select
            onValueChange={(value) => onDaysChange(Number(value) as Days)}
            value={String(days)}
          >
            <SelectTrigger aria-label="Reporting period" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Last 7 days</SelectItem>
              <SelectItem value="30">Last 30 days</SelectItem>
              <SelectItem value="90">Last 90 days</SelectItem>
            </SelectContent>
          </Select>
          <Button
            aria-label="Refresh reporting"
            disabled={query.isFetching}
            onClick={() => query.refetch()}
            size="icon-sm"
            variant="ghost"
          >
            <Icon.refresh />
          </Button>
        </div>
        {query.isLoading ? (
          <LoadingState label="Loading behaviour reporting" />
        ) : query.isError ? (
          <EmptyState
            action={
              <Button onClick={() => query.refetch()} size="sm" variant="secondary">
                Retry
              </Button>
            }
            description="Try again to load the recorded metrics."
            size="section"
            title="Couldn't load reporting"
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
