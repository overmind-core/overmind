import { useEffect, useRef, useState } from "react";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";

import apiClient from "@/client";
import { WorkshopStatusIcon } from "@/components/datasets/workshop-status";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
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
import { useProjectsList } from "@/hooks/use-projects";
import { cn } from "@/lib/utils";
import type { Dataset, PaginatedDatasetList } from "@/openapi";

const workspaceRowClass =
  "grid h-8 grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-x-2 rounded-md border border-transparent px-2 text-sm";

function workspaceSummary(workspace: Dataset) {
  const sources: { filename: string }[] = workspace.sourceSpec?.sources ?? [];
  const types = [
    ...new Set(
      sources.map((source) =>
        source.filename.replace(/\.gz$/i, "").split(".").at(-1)?.toUpperCase()
      )
    ),
  ].filter(Boolean);
  return [
    types.length ? types.join(", ") : workspace.sourceKind === "traces" ? "Traces" : null,
    sources.length > 1 ? `${sources.length} files` : null,
    `${workspace.rows.toLocaleString()} ${workspace.rows === 1 ? "row" : "rows"}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function WorkspaceProject({
  project,
  active,
  enabled,
  search,
  onNavigate,
  onDelete,
}: {
  project: { id: string; name: string };
  active: boolean;
  enabled: boolean;
  search: string;
  onNavigate?: () => void;
  onDelete: (dataset: Dataset) => void;
}) {
  const [open, setOpen] = useState(active);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    if (active) setOpen(true);
  }, [active]);
  const isOpen = open || !!search;
  const FolderIcon = isOpen ? Icon.folderOpen : Icon.folder;
  const query = useInfiniteQuery({
    enabled: enabled && isOpen,
    getNextPageParam: (last: PaginatedDatasetList, pages) =>
      last.next ? pages.length + 1 : undefined,
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      apiClient.datasets.datasetsList({
        ordering: "-updated_at",
        page: pageParam,
        pageSize: 30,
        project: project.id,
        search: search || undefined,
      }),
    queryKey: ["datasets", "list", project.id, "workspaces", search],
    refetchInterval: (query) =>
      query.state.data?.pages.some((page) => page.results.some(isBusy)) ? 3000 : 15000,
  });
  const workspaces = query.data?.pages.flatMap((page) => page.results ?? []) ?? [];
  const count = query.data?.pages[0]?.count;
  const visible = expanded || search ? workspaces : workspaces.slice(0, 5);
  return (
    <Collapsible onOpenChange={setOpen} open={isOpen}>
      <CollapsibleTrigger
        className={cn(
          workspaceRowClass,
          "group w-full text-left text-muted-foreground outline-none hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-ring/60"
        )}
      >
        <FolderIcon aria-hidden="true" className="size-4 justify-self-center" />
        <span className="flex min-w-0 items-center gap-2">
          <span className={cn("min-w-0 flex-1 truncate", active && "text-foreground")}>
            {project.name}
          </span>
          {count != null && (
            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{count}</span>
          )}
        </span>
        <span className="grid size-6 place-items-center">
          <Icon.chevronRight className="size-3 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none" />
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ul aria-label={`${project.name} workspaces`}>
          {visible.map((workspace) => (
            <li
              className={cn(
                workspaceRowClass,
                "group min-w-0 hover:bg-accent/60 focus-within:bg-accent/60"
              )}
              key={workspace.id}
            >
              <Tooltip>
                <TooltipTrigger asChild>
                  <Link
                    aria-label={`${datasetDisplayName(workspace)} · ${workspaceSummary(workspace)}`}
                    className="col-span-2 grid min-w-0 grid-cols-subgrid items-center self-stretch rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
                    onClick={onNavigate}
                    params={{ datasetId: workspace.id }}
                    search={{ projectId: project.id }}
                    to="/datasets/$datasetId"
                  >
                    <span className="col-start-2 truncate">{datasetDisplayName(workspace)}</span>
                  </Link>
                </TooltipTrigger>
                <TooltipContent className="max-w-72 space-y-1" side="right" sideOffset={12}>
                  <p className="break-words">{datasetDisplayName(workspace)}</p>
                  <p>{workspaceSummary(workspace)}</p>
                </TooltipContent>
              </Tooltip>
              <div className="grid grid-cols-[1.5rem_1.5rem] place-items-center gap-1 md:grid-cols-[1.5rem] md:gap-0">
                <WorkshopStatusIcon
                  className="pointer-events-none col-start-1 row-start-1 size-3 md:group-focus-within:opacity-0 md:group-hover:opacity-0"
                  state={
                    isBusy(workspace)
                      ? "working"
                      : workspace.state === "error"
                        ? "error"
                        : workspace.activeVersion
                          ? "complete"
                          : "idle"
                  }
                />
                <Button
                  aria-label={`Delete ${datasetDisplayName(workspace)}`}
                  className="col-start-2 row-start-1 text-muted-foreground group-focus-within:opacity-100 group-hover:opacity-100 md:col-start-1 md:opacity-0"
                  onClick={() => onDelete(workspace)}
                  size="icon-xs"
                  variant="ghost"
                >
                  <Icon.delete />
                </Button>
              </div>
            </li>
          ))}
        </ul>
        {query.isPending && (
          <div className="px-9 py-3">
            <Spinner className="size-3 text-muted-foreground" />
          </div>
        )}
        {query.isError && (
          <div className="space-y-2 px-9 py-2 text-xs text-destructive">
            <p>Couldn't load workspaces.</p>
            <Button onClick={() => void query.refetch()} size="xs" variant="secondary">
              Retry
            </Button>
          </div>
        )}
        {query.isSuccess && !workspaces.length && (
          <p className="px-9 py-2 text-xs text-muted-foreground">
            {search ? "No matching workspaces" : "No workspaces"}
          </p>
        )}
        {(visible.length < workspaces.length || query.hasNextPage) && (
          <Button
            className="mt-1 ml-9 justify-start bg-transparent px-0 text-muted-foreground hover:bg-transparent hover:text-foreground has-[>svg]:px-0"
            disabled={query.isFetchingNextPage}
            onClick={() => {
              if (visible.length < workspaces.length) setExpanded(true);
              else void query.fetchNextPage();
            }}
            size="xs"
            variant="secondary"
          >
            {query.isFetchingNextPage && <Spinner className="size-3" />}Show more
            <Icon.chevronDown className="size-3" />
          </Button>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}

function WorkspaceNavigation({
  enabled = true,
  onNavigate,
}: {
  enabled?: boolean;
  onNavigate?: () => void;
}) {
  const projectId = useRouterState({
    select: (state) => (state.location.search as { projectId?: string }).projectId,
  });
  const projects = useProjectsList();
  const mobile = useIsMobile();
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (enabled && mobile) searchRef.current?.focus();
  }, [enabled, mobile]);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search, 250);
  const [deleting, setDeleting] = useState<Dataset | null>(null);
  const remove = useDeleteDatasetMutation();
  const guard = useGuestGate();
  const groups = [...(projects.data?.projects ?? [])].sort(
    (a, b) => Number(b.id === projectId) - Number(a.id === projectId)
  );
  return (
    <>
      <div className="flex h-14 shrink-0 items-center gap-2 px-1">
        <SearchInput
          className="min-w-0 flex-1"
          label="Search workspaces"
          onChange={(event) => setSearch(event.target.value)}
          onClear={() => setSearch("")}
          placeholder="Search workspaces"
          ref={searchRef}
          value={search}
        />
        <div className="md:hidden">
          <WorkshopSwitcher iconOnly />
        </div>
      </div>
      <nav
        aria-label="Workshop workspaces"
        className="min-h-0 flex-1 space-y-3 overflow-y-auto px-1 pt-3 pb-5"
      >
        {groups.map((project) => (
          <WorkspaceProject
            active={project.id === projectId}
            enabled={enabled}
            key={project.id}
            onDelete={guard(setDeleting)}
            onNavigate={onNavigate}
            project={project}
            search={debouncedSearch}
          />
        ))}
        {projects.isPending && <Spinner className="mx-auto size-4" />}
        {projects.isError && (
          <div className="space-y-2 px-2 text-xs text-destructive">
            <p>Couldn't load projects.</p>
            <Button onClick={() => void projects.refetch()} size="xs" variant="secondary">
              Retry
            </Button>
          </div>
        )}
      </nav>
      <ConfirmDialog
        confirmLabel="Delete workspace"
        description="This deletes the workspace and its dataset cells."
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
        title={`Delete ${deleting ? datasetDisplayName(deleting) : "workspace"}?`}
      />
    </>
  );
}

export function WorkshopSidebar({ open }: { open: boolean }) {
  const { close } = useWorkshopSidebar();
  return (
    <aside
      aria-hidden={!open}
      aria-label="Workshop navigation"
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
        <WorkspaceNavigation enabled={open} onNavigate={close} />
      </div>
    </aside>
  );
}

export function WorkshopSwitcher({ iconOnly = false }: { iconOnly?: boolean }) {
  const mobile = useIsMobile();
  const { open, toggle } = useWorkshopSidebar();
  const FolderIcon = open ? Icon.folderOpen : Icon.folder;
  const label = open ? "Collapse workspaces" : "Expand workspaces";
  if (!mobile && !iconOnly) return null;
  const button = (
    <Button
      aria-controls="workshop-navigation"
      aria-expanded={open}
      aria-label={label}
      className={iconOnly ? undefined : "self-start"}
      onClick={(event) => toggle(event.currentTarget)}
      size={iconOnly ? "icon-sm" : "sm"}
      variant={iconOnly ? "ghost" : "secondary"}
    >
      <FolderIcon aria-hidden="true" />
      {!iconOnly && "Workspaces"}
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
