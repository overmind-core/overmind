import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";

import { Breakdown } from "~/components/breakdown";
import { DailyChart } from "~/components/daily-chart";
import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type AccountActivity, salesGet, type TrainingOutcome } from "~/lib/api";
import { fillDays, formatDate, formatInt, humanize, sum } from "~/lib/format";

import { Badge } from "@/components/ui/badge";
import { Icon } from "@/components/ui/icons";
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

const OUTCOMES: { key: TrainingOutcome; label: string; color: string }[] = [
  { color: "var(--success)", key: "completed", label: "Completed" },
  { color: "var(--destructive)", key: "failed", label: "Failed" },
  { color: "var(--muted-foreground)", key: "cancelled", label: "Cancelled" },
  { color: "var(--chart-1)", key: "in_progress", label: "In progress" },
];

const STATUS_VARIANT: Record<string, "success" | "error" | "neutral"> = {
  cancelled: "neutral",
  failed: "error",
  succeeded: "success",
};

export const Route = createFileRoute("/_app/accounts_/$accountId")({ component: AccountPage });

function AccountPage() {
  const { days } = Route.useSearch();
  const { accountId } = Route.useParams();
  const query = useQuery({
    queryFn: () => salesGet<AccountActivity>(`accounts/${Number(accountId)}`, { days }),
    queryKey: ["account", accountId, days],
  });

  return (
    <>
      <Link
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        search={{ days, ordering: "-date_joined", page: 1, page_size: 25 }}
        to="/accounts"
      >
        <Icon.back className="size-3.5" />
        Accounts
      </Link>
      <PageHeader
        actions={<Freshness data={query.data} />}
        description={`Totals are lifetime. Charts cover the last ${days} days.`}
        title={query.data?.account.email ?? "Account"}
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading account" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <AccountBody data={query.data} days={days} />
      )}
    </>
  );
}

function AccountBody({ data, days }: { data: AccountActivity; days: number }) {
  const { training, traces, inference, datasets, optimizer, evaluations } = data;
  const trainingByDay = new Map(training.daily.map((point) => [point.day.slice(0, 10), point]));
  const trainingDaily = fillDays([], days).map(({ day }) => {
    const values = trainingByDay.get(day)?.values ?? {};
    return Object.fromEntries([
      ["day", day],
      ...OUTCOMES.map((outcome) => [outcome.key, values[outcome.key] ?? 0]),
    ]);
  });
  const trainingInWindow = training.daily
    .flatMap((point) => Object.values(point.values))
    .reduce((total, n) => total + n, 0);
  const tracesDaily = fillDays(traces.daily, days);
  const inferenceDaily = fillDays(inference.daily, days);

  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat
          detail={`${formatInt(training.totals.completed)} completed · ${formatInt(training.totals.failed)} failed`}
          label="Training jobs"
          value={formatInt(training.totals.started)}
        />
        <Stat label="Traces" value={formatInt(traces.total)} />
        <Stat
          detail={inference.calls ? `${formatInt(inference.calls)} calls` : "No inference"}
          label="Models inferred"
          tone={inference.models ? "success" : undefined}
          value={formatInt(inference.models)}
        />
        <Stat label="Datasets" value={formatInt(datasets.total)} />
        <Stat label="Optimiser runs" value={formatInt(optimizer.total)} />
        <Stat label="Evaluations" value={formatInt(evaluations.total)} />
        <Stat label="Projects" value={formatInt(data.account.projects)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <DailyChart
          data={trainingDaily}
          format={formatInt}
          series={OUTCOMES}
          stacked
          title="Training jobs started"
          total={formatInt(trainingInWindow)}
        />
        <DailyChart
          data={tracesDaily}
          format={formatInt}
          series={[{ color: "var(--chart-5)", key: "value", label: "Traces" }]}
          title="Traces ingested"
          total={formatInt(sum(tracesDaily))}
        />
        <DailyChart
          data={inferenceDaily}
          format={formatInt}
          series={[{ color: "var(--chart-2)", key: "value", label: "Calls" }]}
          title="Inference calls"
          total={formatInt(sum(inferenceDaily))}
        />
        <Breakdown
          format={formatInt}
          title="Inference calls by model"
          values={inference.by_model}
        />
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Breakdown format={formatInt} title="Datasets by source" values={datasets.by_source} />
        <Breakdown format={formatInt} title="Datasets by intent" values={datasets.by_intent} />
        <Breakdown
          format={formatInt}
          title="Optimiser runs by status"
          values={optimizer.by_status}
        />
        <Breakdown
          format={formatInt}
          title="Evaluations by status"
          values={evaluations.by_status}
        />
      </div>
      <SectionCard contentClassName="p-0" title="Recent training jobs">
        {training.recent.length ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Base model</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Finished</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {training.recent.map((job) => (
                <TableRow key={job.id}>
                  <TableCell className="font-mono">{job.base_model}</TableCell>
                  <TableCell>
                    <Badge size="chip" variant={STATUS_VARIANT[job.status] ?? "info"}>
                      {humanize(job.status)}
                    </Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap">{formatDate(job.created_at)}</TableCell>
                  <TableCell className="whitespace-nowrap">
                    {job.completed_at ? formatDate(job.completed_at) : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="px-4 py-3 text-xs text-muted-foreground">No training jobs</p>
        )}
      </SectionCard>
    </>
  );
}
