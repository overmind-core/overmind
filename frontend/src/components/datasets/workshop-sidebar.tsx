import { useEffect, useRef, useState } from "react";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";

import apiClient from "@/client";
import { WorkshopStatusIcon } from "@/components/datasets/workshop-status";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useWorkshopSidebar } from "@/contexts/workshop-sidebar-context";
import { datasetDisplayName, isBusy, useDeleteDatasetMutation } from "@/hooks/use-datasets";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";
import type { Dataset, PaginatedDatasetList } from "@/openapi";

function datasetSummary(dataset: Dataset) {
  const sources: { filename: string }[] = dataset.sourceSpec?.sources ?? [];
  const types = [
    ...new Set(
      sources.map((source) =>
        source.filename.replace(/\.gz$/i, "").split(".").at(-1)?.toUpperCase()
      )
    ),
  ].filter(Boolean);
  return [
    types.length ? types.join(", ") : dataset.sourceKind === "traces" ? "Traces" : null,
    sources.length > 1 ? `${sources.length} files` : null,
    `${dataset.rows.toLocaleString()} ${dataset.rows === 1 ? "row" : "rows"}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function DatasetNavigation({ projectId, enabled }: { projectId?: string; enabled: boolean }) {
  const { close } = useWorkshopSidebar();
  const pathname = useRouterState({ select: (state) => state.location.pathname });
  const mobile = useIsMobile();
  const searchRef = useRef<HTMLInputElement>(null);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [deleting, setDeleting] = useState<Dataset | null>(null);
  const remove = useDeleteDatasetMutation();
  const guard = useGuestGate();
  useEffect(() => {
    if (enabled && mobile) searchRef.current?.focus();
  }, [enabled, mobile]);

  const query = useInfiniteQuery({
    enabled: enabled && !!projectId,
    getNextPageParam: (last: PaginatedDatasetList, pages) =>
      last.next ? pages.length + 1 : undefined,
    initialPageParam: 1,
    queryFn: ({ pageParam, signal }) =>
      apiClient.datasets.datasetsList(
        {
          page: pageParam,
          pageSize: 30,
          project: projectId!,
          search: debouncedSearch || undefined,
        },
        { signal }
      ),
    queryKey: ["datasets", "list", projectId, "navigation", debouncedSearch],
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.results.some(isBusy)) ? 3000 : 15000,
  });
  const datasets = [
    ...new Map(
      (query.data?.pages.flatMap((page) => page.results) ?? []).map((dataset) => [
        dataset.id,
        dataset,
      ])
    ).values(),
  ];
  return (
    <>
      <div className="flex h-14 shrink-0 items-center gap-2 px-1">
        <SearchInput
          className="min-w-0 flex-1"
          label="Search datasets in this project"
          onChange={(event) => setSearch(event.target.value)}
          onClear={() => setSearch("")}
          placeholder="Search datasets"
          ref={searchRef}
          value={search}
        />
        <div className="md:hidden">
          <WorkshopSwitcher iconOnly />
        </div>
      </div>
      <nav aria-label="Project datasets" className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
        <ul className="m-0 list-none p-0">
          {datasets.map((dataset) => {
            const active = pathname.replace(/\/$/, "").endsWith(`/datasets/${dataset.id}`);
            return (
              <li
                className={cn(
                  "group flex h-9 min-w-0 items-center gap-1 rounded-sm px-1 md:h-7",
                  active
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:bg-accent/60 focus-within:bg-accent/60"
                )}
                key={dataset.id}
              >
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Link
                      aria-current={active ? "page" : undefined}
                      aria-label={`${datasetDisplayName(dataset)} · ${datasetSummary(dataset)}`}
                      className="flex min-w-0 flex-1 items-center self-stretch rounded-sm px-1 text-sm outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
                      onClick={close}
                      params={{ datasetId: dataset.id }}
                      search={{ projectId }}
                      to="/datasets/$datasetId"
                    >
                      <span className="truncate">{datasetDisplayName(dataset)}</span>
                    </Link>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-72 space-y-1" side="right" sideOffset={12}>
                    <p className="break-words">{datasetDisplayName(dataset)}</p>
                    <p>{datasetSummary(dataset)}</p>
                  </TooltipContent>
                </Tooltip>
                <div className="grid shrink-0 grid-cols-[1.5rem_1.5rem] place-items-center md:grid-cols-[1.5rem]">
                  <WorkshopStatusIcon
                    className="pointer-events-none col-start-1 row-start-1 size-3 md:group-focus-within:opacity-0 md:group-hover:opacity-0"
                    state={
                      isBusy(dataset)
                        ? "working"
                        : dataset.state === "error"
                          ? "error"
                          : dataset.activeVersion
                            ? "complete"
                            : "idle"
                    }
                  />
                  <Button
                    aria-label={`Delete ${datasetDisplayName(dataset)}`}
                    className="col-start-2 row-start-1 text-muted-foreground group-focus-within:opacity-100 group-hover:opacity-100 md:col-start-1 md:opacity-0"
                    onClick={guard(() => setDeleting(dataset))}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <Icon.delete />
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
        {enabled && query.isPending && (
          <div className="flex justify-center py-3">
            <Spinner className="size-4" />
          </div>
        )}
        {query.isError && (
          <div className="space-y-2 px-2 py-2 text-xs text-destructive">
            <p>Couldn't load datasets.</p>
            <Button onClick={() => void query.refetch()} size="xs" variant="secondary">
              Retry
            </Button>
          </div>
        )}
        {query.isSuccess && !datasets.length && (
          <p className="px-2 py-2 text-xs text-muted-foreground">
            {search ? "No matching datasets" : "No datasets"}
          </p>
        )}
        {query.hasNextPage && (
          <Button
            className="mt-2 w-full"
            disabled={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
            size="xs"
            variant="secondary"
          >
            {query.isFetchingNextPage && <Spinner className="size-3" />}Load more datasets
          </Button>
        )}
      </nav>
      <ConfirmDialog
        confirmLabel="Delete dataset"
        description="This deletes the dataset and its cells."
        destructive
        error={remove.error}
        isPending={remove.isPending}
        keepOpenOnError
        onConfirm={async () => {
          if (deleting) await remove.mutateAsync(deleting.id);
        }}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        open={!!deleting}
        title={`Delete ${deleting ? datasetDisplayName(deleting) : "dataset"}?`}
      />
    </>
  );
}

export function WorkshopSidebar({ open }: { open: boolean }) {
  const { close } = useWorkshopSidebar();
  const projectId = useRouterState({
    select: (state) => (state.location.search as { projectId?: string }).projectId,
  });
  return (
    <aside
      aria-hidden={!open}
      aria-label="Dataset navigation"
      className="min-h-0 min-w-0 overflow-hidden"
      id="workshop-navigation"
      inert={!open}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.stopPropagation();
          close();
        }
      }}
    >
      <div className="flex h-full w-full flex-col border-r border-sidebar-border px-2 md:w-62">
        <DatasetNavigation enabled={open} key={projectId} projectId={projectId} />
      </div>
    </aside>
  );
}

export function WorkshopSwitcher({ iconOnly = false }: { iconOnly?: boolean }) {
  const { open, toggle } = useWorkshopSidebar();
  const FolderIcon = open ? Icon.folderOpen : Icon.folder;
  const label = open ? "Collapse datasets" : "Expand datasets";
  const button = (
    <Button
      aria-controls="workshop-navigation"
      aria-expanded={open}
      aria-label={label}
      onClick={(event) => toggle(event.currentTarget)}
      size={iconOnly ? "icon-sm" : "sm"}
      variant={iconOnly ? "ghost" : "secondary"}
    >
      <FolderIcon aria-hidden="true" />
      {!iconOnly && "Datasets"}
    </Button>
  );
  if (!iconOnly) return button;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{button}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  );
}
