import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Breakdown } from "~/components/breakdown";
import { DailyChart } from "~/components/daily-chart";
import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type Overview, overwatchGet, type ProductUsage } from "~/lib/api";
import { fillDays, formatInt, formatUsd, sum } from "~/lib/format";

import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export const Route = createFileRoute("/_app/")({ component: OverviewPage });

function OverviewPage() {
  const { days } = Route.useSearch();
  const query = useQuery({
    queryFn: () => overwatchGet<Overview>("overview", { days }),
    queryKey: ["overview", days],
  });
  const usageQuery = useQuery({
    queryFn: () => overwatchGet<ProductUsage>("product-usage", { days }),
    queryKey: ["product-usage", days],
  });

  return (
    <>
      <PageHeader
        actions={<Freshness data={query.data} />}
        className="flex-col sm:flex-row"
        description={`Product activity over the last ${days} days. Guest-only projects excluded. Project activity does not identify the individual who performed it.`}
        title="User insights"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading overview" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <OverviewBody
          data={query.data}
          days={days}
          usage={usageQuery.data}
          usageFailed={usageQuery.isError}
        />
      )}
    </>
  );
}

function OverviewBody({
  data,
  days,
  usage,
  usageFailed,
}: {
  data: Overview;
  days: number;
  usage?: ProductUsage;
  usageFailed: boolean;
}) {
  const signups = fillDays(data.signups, days, data.computed_at);
  const projects = fillDays(data.projects_created, days, data.computed_at);
  const spans = fillDays(data.spans, days, data.computed_at);
  const traces = fillDays(data.traces, days, data.computed_at);
  const traffic = spans.map((point, index) => ({
    day: point.day,
    spans: point.value,
    traces: traces[index].value,
  }));
  const growth = signups.map((point, index) => ({
    day: point.day,
    projects: projects[index].value,
    signups: point.value,
  }));
  const posthogDays = fillDays(
    (usage?.daily ?? []).map(({ day, cli_completions: value }) => ({ day, value })),
    days,
    usage?.computed_at ?? data.computed_at
  );
  const posthogByDay = new Map((usage?.daily ?? []).map((point) => [point.day, point]));
  const productEvents = posthogDays.map(({ day }) => {
    const point = posthogByDay.get(day);
    return {
      cli_completions: point?.cli_completions ?? 0,
      day,
      mcp_tool_calls: point?.mcp_tool_calls ?? 0,
      training_checks_finished: point?.training_checks_finished ?? 0,
      training_results_available: point?.training_results_available ?? 0,
    };
  });
  const productActionTotal = Object.values(usage?.totals ?? {}).reduce(
    (total, count) => total + count,
    0
  );
  const dailyTopups = fillDays(data.topups_usd, days, data.computed_at);
  const dailySpend = new Map(
    fillDays(data.spend_usd, days, data.computed_at).map(({ day, value }) => [day, value])
  );
  const financial = dailyTopups.map(({ day, value }) => ({
    day,
    spend: dailySpend.get(day) ?? 0,
    topups: value,
  }));
  const totalSpend = Object.values(data.spend_by_service).reduce(
    (total, amount) => total + Number(amount),
    0
  );

  return (
    <div className="space-y-6">
      <section aria-label="Product activity" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Users" value={formatInt(data.users_total)} />
        <Stat label="Sign-ups" value={formatInt(sum(data.signups))} />
        <Stat
          detail="Members of projects that received traces in the last 7 days"
          label="Members of trace-active projects"
          value={formatInt(data.trace_active_users_7d)}
        />
        <Stat
          detail={`Members of projects that received traces in the last ${days} UTC days`}
          label={`Members of active projects, ${days} days`}
          value={formatInt(data.trace_active_users_window)}
        />
        <Stat label="Live deployments" value={formatInt(data.live_deployments)} />
        {usage?.available ? (
          <Stat
            detail={`Last ${days} days · CLI and MCP`}
            label="Active CLI/MCP identities"
            value={formatInt(usage.active_users ?? 0)}
          />
        ) : null}
        {usage?.available ? (
          <Stat
            detail={`${formatInt(usage.mcp_tool_errors ?? 0)} failed MCP calls`}
            label="MCP tool error rate"
            value={`${((usage.mcp_tool_error_rate ?? 0) * 100).toFixed(1)}%`}
          />
        ) : null}
      </section>

      <section aria-label="Spend and payments" className="space-y-4">
        <h2 className="text-sm font-medium text-muted-foreground">Spend and payments</h2>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat
            detail="Active subscriptions"
            label="Paying users"
            value={formatInt(data.paying_users)}
          />
          <Stat
            detail="Cancel at period end"
            label="Cancelling"
            value={formatInt(data.cancelling_users)}
          />
          <Stat label="Free users" value={formatInt(data.free_users)} />
          <Stat detail={`Last ${days} days`} label="Product spend" value={formatUsd(totalSpend)} />
          <Stat
            detail={`Last ${days} days`}
            label="Stripe top-ups"
            value={formatUsd(sum(data.topups_usd))}
          />
          <Stat
            detail="Current balance below $5 after spend"
            label="Low balance users"
            value={formatInt(data.low_balance_users)}
          />
        </div>
      </section>

      <section aria-label="Trends" className="grid gap-4 lg:grid-cols-2">
        <DailyChart
          data={growth}
          format={formatInt}
          series={[
            { color: "var(--chart-1)", key: "signups", label: "Sign-ups" },
            { color: "var(--chart-2)", key: "projects", label: "Projects" },
          ]}
          title="Sign-ups and projects"
          total={`${formatInt(sum(signups))} sign-ups`}
        />
        {usage?.available ? (
          <DailyChart
            data={productEvents}
            format={formatInt}
            series={[
              { color: "var(--chart-1)", key: "cli_completions", label: "CLI completions" },
              { color: "var(--chart-3)", key: "mcp_tool_calls", label: "MCP tool calls" },
              {
                color: "var(--chart-4)",
                key: "training_results_available",
                label: "Training results",
              },
              {
                color: "var(--chart-5)",
                key: "training_checks_finished",
                label: "Training checks finished",
              },
            ]}
            stacked
            title="Product actions"
            total={`${formatInt(productActionTotal)} actions`}
          />
        ) : (
          <SectionCard title="PostHog product events">
            <p className="text-sm text-muted-foreground">
              {usage?.reason ??
                (usageFailed
                  ? "PostHog product event data could not be loaded."
                  : "Loading product event data…")}
            </p>
          </SectionCard>
        )}
        <DailyChart
          data={traffic}
          format={formatInt}
          series={[
            { color: "var(--chart-4)", key: "spans", label: "Spans" },
            { color: "var(--chart-5)", key: "traces", label: "Traces" },
          ]}
          title="Ingested spans and traces"
          total={`${formatInt(sum(spans))} spans`}
        />
        <DailyChart
          data={fillDays(data.inference_calls, days, data.computed_at)}
          format={formatInt}
          series={[{ color: "var(--chart-2)", key: "value", label: "Calls" }]}
          title="Inference calls"
          total={formatInt(sum(data.inference_calls))}
        />
        <DailyChart
          data={financial}
          format={formatUsd}
          series={[
            { color: "var(--chart-2)", key: "topups", label: "Stripe top-ups" },
            { color: "var(--chart-4)", key: "spend", label: "Product spend" },
          ]}
          title="Spend and payments"
          total={`${formatUsd(sum(data.topups_usd))} topped up`}
        />
      </section>

      <section aria-label="Product breakdowns" className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Breakdown format={formatInt} title="Training jobs" values={data.training_jobs} />
        <Breakdown format={formatUsd} title="Spend by service" values={data.spend_by_service} />
        <Breakdown format={formatInt} title="Subscription status" values={data.subscriptions} />
        <Breakdown format={formatInt} title="Sign-in method" values={data.sign_on_methods} />
        {usage?.available ? (
          <SectionCard contentClassName="p-0" title="Most used CLI commands">
            {usage.top_cli_commands?.length ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Command</TableHead>
                    <TableHead className="text-right">Runs</TableHead>
                    <TableHead className="text-right">Identities</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.top_cli_commands.map((command) => (
                    <TableRow key={command.name}>
                      <TableCell className="font-mono">{command.name}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatInt(command.completions)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatInt(command.users)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="p-4 text-xs text-muted-foreground">No CLI command events</p>
            )}
          </SectionCard>
        ) : null}
        {usage?.available ? (
          <SectionCard contentClassName="p-0" title="Most used MCP tools">
            {usage.top_mcp_tools?.length ? (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Tool</TableHead>
                    <TableHead className="text-right">Calls</TableHead>
                    <TableHead className="text-right">Identities</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.top_mcp_tools.map((tool) => (
                    <TableRow key={tool.name}>
                      <TableCell className="font-mono">{tool.name}</TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatInt(tool.calls)}
                      </TableCell>
                      <TableCell className="text-right font-mono tabular-nums">
                        {formatInt(tool.users)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            ) : (
              <p className="p-4 text-xs text-muted-foreground">No MCP tool calls</p>
            )}
          </SectionCard>
        ) : null}
      </section>

      <SectionCard contentClassName="p-0" title="Top base models">
        {data.top_base_models.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Base model</TableHead>
                <TableHead className="text-right">Training jobs</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.top_base_models.map((row) => (
                <TableRow key={row.base_model}>
                  <TableCell className="font-mono">{row.base_model}</TableCell>
                  <TableCell className="text-right font-mono tabular-nums">
                    {formatInt(row.jobs)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="p-4 text-xs text-muted-foreground">No training jobs</p>
        )}
      </SectionCard>
    </div>
  );
}
