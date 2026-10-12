import { useEffect, useState } from "react";

import { type InfiniteData, useInfiniteQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";

import apiClient from "@/client";
import { sourceFiles } from "@/components/datasets/notebook/source";
import { WorkshopStatusIcon, workshopStateOf } from "@/components/datasets/workshop-status";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useWorkshopSidebar } from "@/contexts/workshop-sidebar-context";
import { datasetDisplayName, isBusy, useDeleteDatasetMutation } from "@/hooks/use-datasets";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { cn } from "@/lib/utils";
import type { Dataset, PaginatedDatasetList } from "@/openapi";

const PAGE = 30;

/** File types, file count and rows, or where the rows came from. */
export function datasetSummary(dataset: Dataset): string {
  const files = sourceFiles(dataset.sourceSpec);
  const rows = `${dataset.rows.toLocaleString()} ${dataset.rows === 1 ? "row" : "rows"}`;
  if (!files.length) {
    const origin =
      dataset.sourceKind === "file" ? "" : dataset.sourceKind === "traces" ? "Traces" : "LLM calls";
    return [origin, rows].filter(Boolean).join(" · ");
  }
  const types = [
    ...new Set(
      files.map(
        (file) =>
          file.filename.toLowerCase().replace(/\.gz$/, "").split(".").at(-1)?.toUpperCase() ??
          "FILE"
      )
    ),
  ].join(", ");
  const count = files.length === 1 ? "1 file" : `${files.length} files`;
  return `${types} · ${count} · ${rows}`;
}

function DatasetRow({
  dataset,
  projectId,
  current,
}: {
  dataset: Dataset;
  projectId: string;
  current: boolean;
}) {
  const remove = useDeleteDatasetMutation();
  const name = datasetDisplayName(dataset);
  return (
    <li className="group/row relative">
      <Tooltip>
        <TooltipTrigger asChild>
          <Link
            aria-current={current ? "page" : undefined}
            className={cn(
              "flex h-8 items-center gap-2 rounded-sm px-2 text-sm hover:bg-wash-raised",
              current ? "bg-wash-raised text-foreground" : "text-muted-foreground"
            )}
            params={{ datasetId: dataset.id }}
            search={{ projectId }}
            to="/datasets/$datasetId"
          >
            <span className="min-w-0 flex-1 truncate">{name}</span>
            <WorkshopStatusIcon
              className="group-focus-within/row:invisible group-hover/row:invisible"
              state={workshopStateOf(dataset)}
            />
          </Link>
        </TooltipTrigger>
        <TooltipContent side="right">
          <p>{name}</p>
          <p className="text-muted-foreground">{datasetSummary(dataset)}</p>
        </TooltipContent>
      </Tooltip>
      <ConfirmDialog
        confirmLabel="Delete"
        description="This deletes the dataset and every version of it."
        destructive
        error={remove.error}
        isPending={remove.isPending}
        keepOpenOnError
        onConfirm={() => remove.mutateAsync(dataset.id)}
        title={`Delete ${name}?`}
        trigger={
          <Button
            aria-label={`Delete ${name}`}
            className="absolute top-1 right-1 opacity-0 group-focus-within/row:opacity-100 group-hover/row:opacity-100"
            size="icon-xs"
            variant="ghost"
          >
            <Icon.delete />
          </Button>
        }
      />
    </li>
  );
}

function DatasetNavigation({ projectId }: { projectId: string }) {
  const params = useParams({ strict: false }) as { datasetId?: string };
  const [search, setSearch] = useState("");
  const query = useDebouncedValue(search.trim(), 250);
  const list = useInfiniteQuery<
    PaginatedDatasetList,
    Error,
    InfiniteData<PaginatedDatasetList>,
    readonly unknown[],
    number
  >({
    getNextPageParam: (last, pages) => (last.next ? pages.length + 1 : undefined),
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) =>
      apiClient.datasets.datasetsList(
        { page: pageParam, pageSize: PAGE, project: projectId, search: query || undefined },
        { signal }
      ),
    queryKey: ["datasets", "list", projectId, "navigation", query],
    refetchInterval: (state) =>
      state.state.data?.pages.some((page) =>
        page.results.some((d) => isBusy(d) || (d.running ?? 0) > 0)
      )
        ? 3000
        : 15000,
  });
  const datasets = list.data?.pages.flatMap((page) => page.results) ?? [];
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2 p-2">
      <SearchInput
        label="Search datasets in this project"
        onChange={(event) => setSearch(event.target.value)}
        onClear={() => setSearch("")}
        placeholder="Search datasets"
        size="sm"
        value={search}
      />
      <nav aria-label="Project datasets" className="min-h-0 flex-1 overflow-y-auto">
        {list.isError ? (
          <div className="flex flex-col items-start gap-2 p-2 text-xs text-muted-foreground">
            Couldn't load datasets.
            <Button onClick={() => void list.refetch()} size="xs" variant="secondary">
              Retry
            </Button>
          </div>
        ) : list.isPending ? null : datasets.length === 0 ? (
          <p className="p-2 text-xs text-muted-foreground">
            {query ? "No matching datasets" : "No datasets"}
          </p>
        ) : (
          <ul className="flex flex-col gap-0.5">
            {datasets.map((dataset) => (
              <DatasetRow
                current={dataset.id === params.datasetId}
                dataset={dataset}
                key={dataset.id}
                projectId={projectId}
              />
            ))}
          </ul>
        )}
        {list.hasNextPage && (
          <Button
            className="mt-1 w-full"
            disabled={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
            size="xs"
            variant="secondary"
          >
            Load more datasets
          </Button>
        )}
      </nav>
    </div>
  );
}

/** The project's datasets beside the canvas. Escape closes it. */
export function WorkshopSidebar({ projectId }: { projectId: string | undefined }) {
  const { open, close } = useWorkshopSidebar();
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);
  return (
    <aside
      aria-hidden={!open}
      aria-label="Dataset navigation"
      className="flex min-h-0 min-w-0 flex-col overflow-hidden border-r border-sidebar-border"
      id="workshop-navigation"
      inert={!open}
    >
      {projectId && <DatasetNavigation projectId={projectId} />}
    </aside>
  );
}

export function WorkshopSwitcher() {
  const { open, toggle } = useWorkshopSidebar();
  const label = open ? "Collapse datasets" : "Expand datasets";
  return (
    <Button
      aria-controls="workshop-navigation"
      aria-expanded={open}
      aria-label={label}
      onClick={(event) => toggle(event.currentTarget)}
      size="icon-sm"
      title={label}
      variant="ghost"
    >
      <Icon.folder />
    </Button>
  );
}
