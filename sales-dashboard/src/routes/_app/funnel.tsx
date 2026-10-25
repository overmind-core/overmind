import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type Activation, overwatchGet } from "~/lib/api";
import { formatInt, humanize } from "~/lib/format";

import { PageHeader } from "@/components/ui/page-header";
import { SectionCard } from "@/components/ui/section-card";
import { LoadingState } from "@/components/ui/spinner";

export const Route = createFileRoute("/_app/funnel")({ component: ActivationPage });

const percent = (part: number, whole: number) =>
  whole ? `${Math.round((part / whole) * 100)}%` : "–";

function ActivationPage() {
  const { days } = Route.useSearch();
  const query = useQuery({
    queryFn: () => overwatchGet<Activation>("activation", { days }),
    queryKey: ["activation", days],
  });

  return (
    <>
      <PageHeader
        actions={<Freshness data={query.data} />}
        description={`Users who signed up in the last ${days} days, by product step reached. Project activity is attributed to members who belonged to the project at the time; the data does not identify which member performed it.`}
        title="Activation"
      />
      {query.isPending ? (
        <LoadingState fullPage label="Loading activation" />
      ) : query.isError ? (
        <Failure error={query.error} onRetry={query.refetch} />
      ) : (
        <ActivationBody data={query.data} />
      )}
    </>
  );
}

function ActivationBody({ data }: { data: Activation }) {
  const top = data.steps[0]?.users ?? 0;
  const median = data.median_hours_to_first_span;
  const last = data.steps.at(-1)?.users ?? 0;

  return (
    <div className="space-y-6">
      <section aria-label="Summary" className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <Stat label="New users" value={formatInt(top)} />
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
                    style={{ width: top ? `${(step.users / top) * 100}%` : 0 }}
                  />
                </div>
                <span className="text-right font-mono text-sm tabular-nums">
                  {formatInt(step.users)}
                  {index ? (
                    <span className="ml-2 text-xs text-muted-foreground">
                      {percent(step.users, top)}
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
