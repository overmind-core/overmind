import { useState } from "react";

import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";
import { useNavigate, useRouterState } from "@tanstack/react-router";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import {
  activeCellOf,
  cellsOf,
  isBusy,
  useDatasetQuery,
  usePatchDatasetMutation,
} from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { cn } from "@/lib/utils";
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

export function DatasetVersionChip({
  datasetId,
  projectId,
}: {
  datasetId: string;
  projectId?: string;
}) {
  const [open, setOpen] = useState(false);
  const query = usePipelineRuns(datasetId);
  const { data: dataset } = useDatasetQuery(datasetId);
  const patch = usePatchDatasetMutation(datasetId);
  const guard = useGuestGate();
  const navigate = useNavigate();
  const iteration = useRouterState({
    select: (state) => (state.location.search as { iteration?: string }).iteration,
  });
  const cells = cellsOf(dataset);
  const active = activeCellOf(dataset);
  const selected = cells.find((cell) => cell.id === iteration) ?? active;
  const runs = query.data?.pages.flatMap((page) => page.runs) ?? [];
  const run = runs.find(
    (item) =>
      item.outputCell === selected?.id ||
      (Array.isArray(item.result.steps) &&
        item.result.steps.some((step) => step.output_cell === selected?.id))
  );
  const show = (id?: string) => {
    void navigate({
      params: { datasetId },
      replace: true,
      search: { iteration: id, projectId },
      to: "/datasets/$datasetId",
    });
    setOpen(false);
  };
  if (!dataset || !selected) return null;
  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button
          aria-label={`Dataset versions, ${selected.version}`}
          className="shrink-0 gap-1 font-mono"
          size="xs"
          variant="outline"
        >
          {selected.version}
          <Icon.chevronDown className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Dataset versions"
        className="w-[min(24rem,calc(100vw-2rem))] p-2 font-sans"
      >
        <div className="mb-1 flex items-center justify-between gap-2">
          <span className="text-sm">Versions</span>
          <Button
            onClick={() => show(iteration === "all" ? undefined : "all")}
            size="xs"
            variant="secondary"
          >
            {iteration === "all" ? "Current output" : "All iterations"}
          </Button>
        </div>
        <div className="max-h-60 overflow-y-auto">
          {[...cells]
            .reverse()
            .filter((cell) => cell.state === "ok")
            .map((cell) => (
              <button
                aria-pressed={selected.id === cell.id}
                className={cn(
                  "flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-xs hover:bg-accent focus-visible:outline focus-visible:outline-ring",
                  selected.id === cell.id && "bg-accent"
                )}
                key={cell.id}
                onClick={() => show(cell.id)}
                type="button"
              >
                <span className="shrink-0 font-mono">{cell.version}</span>
                <span className="min-w-0 flex-1 truncate">{cell.title}</span>
                {cell.id === active?.id && <span className="text-muted-foreground">Active</span>}
              </button>
            ))}
        </div>
        {selected.id !== active?.id && (
          <div className="mt-2 border-t border-border/70 pt-2">
            <Button
              disabled={patch.isPending || isBusy(dataset)}
              onClick={guard(() =>
                patch.mutate({ active: selected.id }, { onSuccess: () => show() })
              )}
              size="sm"
              variant="secondary"
            >
              {patch.isPending && <Spinner size="sm" />}Restore {selected.version}
            </Button>
          </div>
        )}
        {run && <RunFacts run={run} />}
        {query.isError && (
          <Button onClick={() => void query.refetch()} size="xs" variant="secondary">
            Retry run details
          </Button>
        )}
        {query.hasNextPage && (
          <Button
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
            size="xs"
            variant="secondary"
          >
            Older run details
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
      <details>
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
