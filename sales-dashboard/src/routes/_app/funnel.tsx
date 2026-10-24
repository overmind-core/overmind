import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type Funnel, salesGet } from "~/lib/api";
import { formatInt, humanize } from "~/lib/format";

import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState } from "@/components/ui/spinner";

export const Route = createFileRoute("/_app/funnel")({ component: FunnelPage });

const percent = (part: number, whole: number) =>
  whole ? `${Math.round((part / whole) * 100)}%` : "–";

function FunnelPage() {
  const { days } = Route.useSearch();
  const query = useQuery({
    queryFn: () => salesGet<Funnel>("funnel", { days }),
    queryKey: ["funnel", days],
  });

  return (
    <>
      <PageHeader
        actions={<Freshness data={query.data} />}
        description={`Accounts that signed up in the last ${days} days, by step reached. Steps can be reached out of order.`}
        title="Activation funnel"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading funnel" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <FunnelBody data={query.data} />
      )}
    </>
  );
}

function FunnelBody({ data }: { data: Funnel }) {
  const top = data.steps[0]?.accounts ?? 0;
  const median = data.median_hours_to_first_span;
  const last = data.steps.at(-1)?.accounts ?? 0;

  return (
    <div className="space-y-6">
      <section aria-label="Summary" className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="Signed up" value={formatInt(top)} />
        <Stat
          detail="Median"
          label="Sign-up to first span"
          value={
            median == null
              ? "–"
              : median < 48
                ? `${median.toFixed(1)}h`
                : `${(median / 24).toFixed(1)}d`
          }
        />
        <Stat detail="Signed up to alias call" label="Conversion" value={percent(last, top)} />
      </section>

      <SectionCard title="Steps">
        <ol className="space-y-3">
          {data.steps.map((step, index) => {
            return (
              <li className="grid grid-cols-[10rem_1fr_9rem] items-center gap-3" key={step.step}>
                <span className="text-sm">{humanize(step.step)}</span>
                <div className="h-6 rounded-xs bg-wash-raised">
                  <div
                    className="h-full rounded-xs bg-chart-1"
                    style={{ width: top ? `${(step.accounts / top) * 100}%` : 0 }}
                  />
                </div>
                <span className="text-right font-mono text-sm tabular-nums">
                  {formatInt(step.accounts)}
                  {index ? (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {percent(step.accounts, top)}
                    </span>
                  ) : null}
                </span>
              </li>
            );
          })}
        </ol>
      </SectionCard>
    </div>
  );
}
