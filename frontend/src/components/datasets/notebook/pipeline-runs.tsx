import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { activeCellOf, useDatasetQuery } from "@/hooks/use-datasets";
import type { PipelineRun, Workbench } from "@/openapi";

export function usePipelineRuns(datasetId: string) {
  return useInfiniteQuery<Workbench, Error, InfiniteData<Workbench>, string[], number>({
    getNextPageParam: (page) =>
      page.runPage.nextCursor ? Number(page.runPage.nextCursor) : undefined,
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      apiClient.datasets.datasetsWorkbenchRetrieve({ id: datasetId, runOffset: pageParam }),
    queryKey: ["datasets", datasetId, "workbench"],
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) =>
        page.runs.some((run) => ["queued", "running"].includes(run.state))
      )
        ? 3000
        : false,
  });
}

export function DatasetProcessDetails({ datasetId }: { datasetId: string }) {
  const query = usePipelineRuns(datasetId);
  const { data: dataset } = useDatasetQuery(datasetId);
  const active = activeCellOf(dataset);
  const runs = query.data?.pages.flatMap((page) => page.runs) ?? [];
  const latest = runs[0];
  const run =
    latest && (latest.state !== "completed" || !active)
      ? latest
      : runs.find((item) => item.outputCell === active?.id);
  if (!active && !run) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button aria-label="Preparation details" size="xs" variant="outline">
          Process <Icon.chevronDown className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[min(24rem,calc(100vw-2rem))] p-3 font-sans">
        <p className="text-sm">
          {active ? `${active.title} · ${active.rows.toLocaleString()} rows` : run?.pipelineName}
        </p>
        {run && <RunFacts run={run} />}
        {query.isError && (
          <Button onClick={() => void query.refetch()} size="xs" variant="secondary">
            Retry run details
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}

function RunFacts({ run }: { run: PipelineRun }) {
  const steps = Array.isArray(run.result.steps) ? run.result.steps : [];
  return (
    <div className="mt-2 space-y-2 text-xs">
      {run.error && (
        <p className="text-destructive" role="alert">
          {run.error}
        </p>
      )}
      {run.runner?.status === "unavailable" && ["queued", "running"].includes(run.state) && (
        <p role="status">Runner observations unavailable.</p>
      )}
      <details open={["queued", "running", "failed"].includes(run.state)}>
        <summary className="cursor-pointer text-muted-foreground">Execution details</summary>
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
          <dt>Transformation</dt>
          <dd>
            {run.pipelineName ?? "External import"}
            {run.revision ? ` · revision ${run.revision}` : ""}
          </dd>
          <dt>Revision ID</dt>
          <dd className="break-all">{run.pipeline ?? "—"}</dd>
          <dt>Source dataset</dt>
          <dd className="break-all">{run.sourceDataset}</dd>
          <dt>Source</dt>
          <dd className="break-all">{run.sourceFingerprint}</dd>
          <dt>Execution</dt>
          <dd>{run.execution.replaceAll("_", " ")}</dd>
          <dt>Progress</dt>
          <dd>{String(run.result.stage ?? run.state).replaceAll("_", " ")}</dd>
          <dt>Parameters</dt>
          <dd className="break-all">{JSON.stringify(run.parameters)}</dd>
        </dl>
        {steps.length > 0 && (
          <ol className="mt-2 space-y-1">
            {steps.map((step, index) => (
              <li key={String(step.index ?? index)}>
                {index + 1}. {String(step.name)} · {String(step.state)} ·{" "}
                {String(step.input_rows ?? "—")} → {String(step.output_rows ?? "—")} rows
                {step.batches && (
                  <>
                    {" "}
                    · {String(step.batches.completed)}/{String(step.batches.total)} batches
                  </>
                )}
                {step.consumer_check?.passed && (
                  <> · {String(step.consumer_check.consumer)} validated</>
                )}
              </li>
            ))}
          </ol>
        )}
        {run.mode === "preview" && (
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all">
            {JSON.stringify(
              steps.map((step) => ({ sample: step.sample, step: step.name })),
              null,
              2
            )}
          </pre>
        )}
      </details>
    </div>
  );
}
