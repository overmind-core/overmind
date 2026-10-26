import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Breakdown } from "~/components/breakdown";
import { DailyChart } from "~/components/daily-chart";
import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type Overview, salesGet } from "~/lib/api";
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
    queryFn: () => salesGet<Overview>("overview", { days }),
    queryKey: ["overview", days],
  });

  return (
    <>
      <PageHeader
        actions={<Freshness data={query.data} />}
        description={`Last ${days} days. Guests excluded.`}
        title="Overview"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading overview" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <OverviewBody data={query.data} days={days} />
      )}
    </>
  );
}

function OverviewBody({ data, days }: { data: Overview; days: number }) {
  const signups = fillDays(data.signups, days);
  const projects = fillDays(data.projects_created, days);
  const spans = fillDays(data.spans, days);
  const traces = fillDays(data.traces, days);
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
  const spend = sum(Object.values(data.spend_by_service).map((value) => ({ value })));

  return (
    <div className="space-y-6">
      <section
        aria-label="Accounts"
        className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-5"
      >
        <Stat label="Accounts" value={formatInt(data.accounts_total)} />
        <Stat label="Sign-ups" value={formatInt(sum(data.signups))} />
        <Stat label="Active, 7 days" value={formatInt(data.active_accounts_7d)} />
        <Stat label={`Active, ${days} days`} value={formatInt(data.active_accounts_window)} />
        <Stat label="Live deployments" value={formatInt(data.live_deployments)} />
        <Stat label="Paying" tone="success" value={formatInt(data.paying_accounts)} />
        <Stat label="Free with a project" value={formatInt(data.free_accounts)} />
        <Stat
          label="Cancelling"
          tone={data.cancelling_accounts ? "warning" : undefined}
          value={formatInt(data.cancelling_accounts)}
        />
        <Stat
          detail="Under $5 after spending"
          label="Low balance"
          tone={data.low_balance_accounts ? "warning" : undefined}
          value={formatInt(data.low_balance_accounts)}
        />
        <Stat label="Platform spend" value={formatUsd(spend)} />
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
        <DailyChart
          data={fillDays(data.topups_usd, days)}
          format={formatUsd}
          series={[{ color: "var(--chart-3)", key: "value", label: "Top-ups" }]}
          title="Stripe top-ups"
          total={formatUsd(sum(data.topups_usd))}
        />
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
          data={fillDays(data.inference_calls, days)}
          format={formatInt}
          series={[{ color: "var(--chart-2)", key: "value", label: "Calls" }]}
          title="Inference calls"
          total={formatInt(sum(data.inference_calls))}
        />
      </section>

      <section aria-label="Breakdowns" className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Breakdown format={formatUsd} title="Spend by service" values={data.spend_by_service} />
        <Breakdown format={formatInt} title="Subscriptions" values={data.subscriptions} />
        <Breakdown format={formatInt} title="Training jobs" values={data.training_jobs} />
        <Breakdown format={formatInt} title="Sign-on method" values={data.sign_on_methods} />
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
