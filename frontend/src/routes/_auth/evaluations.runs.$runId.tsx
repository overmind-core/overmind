import { createFileRoute, Link } from "@tanstack/react-router";
import { toast } from "sonner";

import { UsedVersionChip } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import { GeneratedRunResults } from "@/components/evaluations/generated-run-results";
import { ProbabilityEvaluationPage } from "@/components/evaluations/probability-evaluation-page";
import { StatusBadge } from "@/components/evaluations/runs-table";
import { HeaderStat } from "@/components/finetuning/finetuning-chrome";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { DetailErrorState } from "@/components/route-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { CountChip } from "@/components/ui/count-chip";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { TooltipProvider } from "@/components/ui/tooltip";
import { usedVersionOf } from "@/hooks/use-datasets";
import { useElapsedSeconds } from "@/hooks/use-elapsed-seconds";
import {
  isActiveRunStatus,
  useEvalRunGroupQuery,
  useEvalRunQuery,
  useRelaunchEvalRunMutation,
} from "@/hooks/use-evaluations";
import { formatElapsed } from "@/lib/formatters";
import { evaluationRunSearchSchema } from "@/lib/schemas";
import { TITLE } from "@/lib/typography";
import type { EvalRun, EvalRunDetail } from "@/openapi";

export const Route = createFileRoute("/_auth/evaluations/runs/$runId")({
  component: EvaluationRunPage,
  validateSearch: evaluationRunSearchSchema,
});

const MODE_LABEL: Record<string, string> = {
  generate: "Generated",
  provided: "Provided",
  recorded: "Recorded",
};

const PHASE_LABEL: Record<string, string> = {
  generating: "Generating",
  preparing: "Preparing",
  scoring: "Scoring",
};

const targetLabel = (run: Pick<EvalRun, "label" | "mode">) =>
  run.label || MODE_LABEL[run.mode] || run.mode;

const groupTitle = (run: EvalRunDetail) => {
  const suffix = run.label ? ` · ${run.label}` : "";
  return suffix && run.name.endsWith(suffix) ? run.name.slice(0, -suffix.length) : run.name;
};

function EvaluationRunPage() {
  const { runId } = Route.useParams();
  const { format } = Route.useSearch();
  return format === "decision" ? <ProbabilityEvaluationPage id={runId} /> : <EvalRunDetailPage />;
}

function EvalRunDetailPage() {
  const { runId } = Route.useParams();
  const { projectId } = Route.useSearch();
  const { data: run, isLoading, error } = useEvalRunQuery(runId);
  const group = useEvalRunGroupQuery(projectId, run?.group || undefined);

  const header = <PageHeader title="Evaluations" />;

  if (error && !run) {
    return (
      <PageShell header={header} variant="full">
        <DetailErrorState
          action={
            <Button asChild size="sm" variant="secondary">
              {/* `search={true}` keeps the list's filters, which live only in this
                  URL. Not `(prev) => prev`: a cross-route `prev` is typed as every
                  route's search unioned, widening `run_status` past the enum. */}
              <Link search={true} to="/evaluations">
                Back to evaluations
              </Link>
            </Button>
          }
          error={error}
          fallback="Couldn't load this run."
        />
      </PageShell>
    );
  }

  if (isLoading || !run) {
    return (
      <PageShell header={header} variant="full">
        <Skeleton className="h-8 w-64" />
        <div className="grid grid-cols-2 gap-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton className="h-20" key={i} />
          ))}
        </div>
      </PageShell>
    );
  }

  const siblings = group.data?.results ?? [];
  const runs = siblings.length ? siblings.map((item) => (item.id === run.id ? run : item)) : [run];

  return (
    <TooltipProvider delayDuration={200}>
      <PageShell header={header} variant="full">
        {run.error ? (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {run.error}
          </div>
        ) : null}

        <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto">
          <h2 className={TITLE.card}>{groupTitle(run) || "Evaluation run"}</h2>
          <RunHeaderCard key={run.group || run.id} projectId={projectId} run={run} runs={runs} />

          {group.error && (
            <DetailErrorState error={group.error} fallback="Couldn't load the other runs." />
          )}
          <GeneratedRunResults runs={runs} />
        </div>
      </PageShell>
    </TooltipProvider>
  );
}

function RunElapsed({ run }: { run: EvalRun }) {
  const running = isActiveRunStatus(run.status);
  const live = useElapsedSeconds(running ? run.createdAt : undefined);
  const seconds = running
    ? live
    : Math.floor(((run.completedAt ?? run.updatedAt).getTime() - run.createdAt.getTime()) / 1000);
  if (seconds == null || seconds <= 0) return null;
  return (
    <span className="tabular-nums text-xs text-muted-foreground">{formatElapsed(seconds)}</span>
  );
}

function Divider() {
  return <span aria-hidden className="mx-5 w-px shrink-0 self-stretch bg-border/60" />;
}

function RelaunchButton({ runId, visible }: { runId: string; visible: boolean }) {
  const relaunch = useRelaunchEvalRunMutation();
  if (!visible) return null;
  return (
    <Button
      disabled={relaunch.isPending}
      onClick={() =>
        relaunch.mutate(runId, {
          onSuccess: () => toast.success("Re-running"),
        })
      }
      size="sm"
      variant="secondary"
    >
      <Icon.refresh />
      Re-run
    </Button>
  );
}

function RunHeaderCard({
  run,
  runs,
  projectId,
}: {
  run: EvalRunDetail;
  runs: EvalRun[];
  projectId: string | undefined;
}) {
  const [actionId, setActionId] = useState(run.id);
  const actionRun = runs.find((item) => item.id === actionId) ?? run;
  const active = isActiveRunStatus(actionRun.status);
  const progress = actionRun.progress;
  const createdAt = new Date(Math.min(...runs.map((item) => item.createdAt.getTime())));
  const completedAt = runs.every((item) => item.status === "completed" && item.completedAt)
    ? new Date(Math.max(...runs.map((item) => item.completedAt!.getTime())))
    : null;
  return (
    <Card aria-label="Run details" className="flex flex-col" role="region">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3 p-4">
        <div className="flex min-w-0 flex-col gap-2">
          <h3 className="inline-flex items-center gap-1.5 text-xs text-foreground">
            Models
            <CountChip count={runs.length} />
          </h3>
          <div className="flex flex-wrap items-center gap-2">
            {runs.map((item) => (
              <ModelProviderChip
                key={item.id}
                model={item.resolvedModel || item.modelName || targetLabel(item)}
                showModelId={false}
              >
                <span className="min-w-0 truncate">{targetLabel(item)}</span>
                {item.isBaseline && (
                  <Badge size="chip" variant="neutral">
                    Baseline
                  </Badge>
                )}
              </ModelProviderChip>
            ))}
          </div>
        </div>
        <dl className="flex shrink-0 flex-col items-end gap-1 text-xs">
          <div className="flex items-baseline gap-2">
            <dt className="text-muted-foreground">Created</dt>
            <dd className="font-medium">
              <DateTime value={createdAt} />
            </dd>
          </div>
          {completedAt && (
            <div className="flex items-baseline gap-2">
              <dt className="text-muted-foreground">Completed</dt>
              <dd className="font-medium">
                <DateTime value={completedAt} />
              </dd>
            </div>
          )}
        </dl>
      </div>

      <div className="flex flex-wrap items-stretch gap-y-3 border-t border-border/70 px-4 py-3.5">
        {run.capabilityId && (
          <>
            <HeaderStat label="Capability">
              <EntityRef id={run.capabilityId} kind="capability" name={run.capabilityName} />
            </HeaderStat>
            <Divider />
          </>
        )}
        {run.dataset && (
          <>
            <HeaderStat label="Dataset">
              <span className="inline-flex items-center gap-1.5">
                <EntityRef
                  id={run.dataset}
                  kind="dataset"
                  name={run.datasetName}
                  projectId={projectId}
                />
                <UsedVersionChip info={usedVersionOf(run.cellInfo)} />
              </span>
            </HeaderStat>
            <Divider />
          </>
        )}
        {run.maxItems > 0 && (
          <>
            <HeaderStat label="Max items">{run.maxItems}</HeaderStat>
            <Divider />
          </>
        )}
        <HeaderStat label="Matrix rows">
          {Array.isArray(run.matrix) ? run.matrix.length : 0}
        </HeaderStat>
        {run.judgeModel && (
          <>
            <Divider />
            <HeaderStat label="Judge">
              <span className="font-mono text-xs">{run.judgeModel}</span>
            </HeaderStat>
          </>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2.5 border-t border-border/70 px-4 py-3">
        {runs.length > 1 && (
          <Select onValueChange={setActionId} value={actionRun.id}>
            <SelectTrigger aria-label="Run status and actions" className="w-56 max-w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {runs.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {targetLabel(item)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <StatusBadge status={actionRun.status} />
        {active && progress && (
          <span className="font-mono text-xs tabular-nums text-muted-foreground">
            {PHASE_LABEL[progress.phase] ?? progress.phase} · {progress.done}/{progress.rows}
            {progress.errored > 0 && ` · ${progress.errored} errored`}
          </span>
        )}
        <RunElapsed run={actionRun} />
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <RelaunchButton runId={actionRun.id} visible={!active} />
          {actionRun.status === "completed" && (
            <Button asChild size="sm">
              <Link
                search={{
                  optimize: true,
                  projectId,
                  ...(actionRun.capabilityId ? { capabilityId: actionRun.capabilityId } : {}),
                  ...(actionRun.dataset ? { datasetId: actionRun.dataset } : {}),
                }}
                to="/optimiser"
              >
                <Icon.optimiser />
                Optimise
              </Link>
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

import { useState } from "react";
