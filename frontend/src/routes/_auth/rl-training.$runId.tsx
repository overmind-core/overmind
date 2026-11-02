import { createFileRoute, redirect } from "@tanstack/react-router";

import { DetailErrorState } from "@/components/route-error";
import { RLStatusBadge } from "@/components/rl/format";
import { RunDetail } from "@/components/rl/run-detail";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import { LoadingState } from "@/components/ui/spinner";
import { isTerminalRLStatus, useRLRunQuery, useStopRLRun } from "@/hooks/use-rl";
import { featureFlags } from "@/lib/feature-flags";
import { projectIdSearchSchema } from "@/lib/schemas";

export const Route = createFileRoute("/_auth/rl-training/$runId")({
  beforeLoad: () => {
    if (!featureFlags.finetuning) throw redirect({ replace: true, to: "/" });
  },
  component: RLRunPage,
  validateSearch: projectIdSearchSchema,
});

function RLRunPage() {
  const { runId } = Route.useParams();
  const { data: run, error } = useRLRunQuery(runId);
  const stop = useStopRLRun(runId);

  const header = (
    <PageHeader
      actions={
        run && !isTerminalRLStatus(run.status) ? (
          <Button disabled={stop.isPending} onClick={() => stop.mutate()} variant="secondary">
            <Icon.stop /> Stop
          </Button>
        ) : null
      }
      description={
        run
          ? `${run.baseModel} · ${run.datasetName} · ${run.trainRows} training prompts`
          : undefined
      }
      icon={<Icon.target aria-hidden className="size-6 shrink-0" />}
      meta={run ? <RLStatusBadge status={run.status} /> : null}
      title={run?.name ?? "RL run"}
    />
  );

  return (
    <PageShell header={header} variant="scroll">
      {run ? (
        <RunDetail run={run} />
      ) : error ? (
        <DetailErrorState error={error} fallback="This RL run could not load" />
      ) : (
        <LoadingState label="Loading the RL run" />
      )}
    </PageShell>
  );
}
