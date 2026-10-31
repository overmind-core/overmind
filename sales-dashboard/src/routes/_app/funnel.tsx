import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";

import { Failure } from "~/components/failure";
import { Freshness } from "~/components/freshness";
import { Stat } from "~/components/stat";
import { type Activation, overwatchGet } from "~/lib/api";
import { formatInt, milestoneLabel } from "~/lib/format";

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
        className="flex-col sm:flex-row"
        description={`Users who signed up in the last ${days} UTC days. Counts show independent milestones in retained records. Project milestones are attributed to memberships.`}
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
          label="Sign-up to first retained trace"
          value={
            median == null
              ? "–"
              : median < 48
                ? `${median.toFixed(1)}h`
                : `${(median / 24).toFixed(1)}d`
          }
        />
        <Stat
          detail="New users who reached a model call"
          label="Model call adoption"
          value={percent(last, top)}
        />
      </section>

      <SectionCard title="Milestone adoption">
        <p className="mb-4 text-sm text-muted-foreground">
          Milestones can be reached in any order. Use PostHog to measure ordered conversion.
        </p>
        <ol className="space-y-3">
          {data.steps.map((step, index) => {
            return (
              <li
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 sm:grid-cols-[10rem_minmax(0,1fr)_9rem]"
                key={step.step}
              >
                <span className="text-sm">{milestoneLabel(step.step)}</span>
                <div className="order-3 col-span-2 h-6 rounded-xs bg-wash-raised sm:order-none sm:col-span-1">
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
