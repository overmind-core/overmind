import { type ReactNode, useEffect, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";
import { toast } from "sonner";

import { EntityRef } from "@/components/entity-ref";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import {
  useDeleteEvalRunMutation,
  useEvalRunDatasetsQuery,
  useEvalRunsQuery,
  useProjectCapabilitiesQuery,
} from "@/hooks/use-evaluations";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { StatusBadge as JobStatusBadge } from "@/lib/job-status";
import { sentenceCase } from "@/lib/label-case";
import { notify } from "@/lib/notify";
import type { EvaluationsSearch } from "@/lib/schemas";
import { clearUnseenCompletion, useUnseenCompletionKeys } from "@/lib/unseen-job-completions";
import { cn } from "@/lib/utils";
import type { EvalRunList, EvalRunProgress } from "@/openapi";
import { EvalRunsListStatusEnum } from "@/openapi";

const plural = (count: number, noun: string): string => `${count} ${noun}${count === 1 ? "" : "s"}`;

const STATUS_FILTER_LABELS: Record<string, string> = {
  cancelled: "Cancelled",
  completed: "Completed",
  failed: "Failed",
  pending: "Pending",
  running: "Running",
};

export function StatusBadge({ status, progress }: { status: string; progress?: number | null }) {
  return <JobStatusBadge fallback="pending" progress={progress} status={status} />;
}

function runProgressPercent(progress?: EvalRunProgress | null): number | null {
  if (!progress) return null;
  const isGenerating = progress.phase === "generating" || progress.phase === "pending";
  const done = isGenerating ? progress.prepared : progress.scored;
  const total = isGenerating ? progress.total : progress.scoreTotal;
  return progress.phase === "aggregating" ? 100 : total > 0 ? (done / total) * 100 : null;
}

type RunsFilters = Pick<
  EvaluationsSearch,
  "run_capability" | "run_dataset" | "run_search" | "run_status"
>;
interface RunsTableProps {
  projectId: string;
  /** Toolbar CTA rendered before the search input. */
  action?: ReactNode;
  page: number;
  pageSize: number;
  filters: RunsFilters;
  onSearchChange: (updates: Partial<RunsFilters> & { page?: number; page_size?: number }) => void;
}

export function RunsTable({
  projectId,
  action,
  page,
  pageSize,
  filters,
  onSearchChange,
}: RunsTableProps) {
  const navigate = useNavigate();
  const unseenKeys = useUnseenCompletionKeys();
  const deleteRun = useDeleteEvalRunMutation(projectId);
  // Rows hidden while their undoable delete window is open.
  const [pendingDelete, setPendingDelete] = useState<Set<string>>(() => new Set());
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [bulkDeletePending, setBulkDeletePending] = useState(false);
  const {
    run_capability: capabilityFilter,
    run_dataset: datasetFilter,
    run_search: searchParam,
    run_status: statusFilter,
  } = filters;
  // Local so typing doesn't write a URL entry per keystroke.
  const [search, setSearch] = useState(searchParam);
  const debouncedSearch = useDebouncedValue(search, 400);
  // DRF `?ordering=` string (e.g. "-created_at"). Empty = server default.
  const [ordering, setOrdering] = usePersistedState<string>(
    "evaluations:runOrdering",
    "-created_at"
  );

  const { data: capabilitiesData } = useProjectCapabilitiesQuery(projectId);
  // Server returns the runs' distinct datasets, name-ordered — no client sort.
  const { data: datasetOptions = [] } = useEvalRunDatasetsQuery(projectId);

  const { data, isLoading, error } = useEvalRunsQuery({
    capability: capabilityFilter !== "all" ? capabilityFilter : undefined,
    dataset: datasetFilter !== "all" ? datasetFilter : undefined,
    ordering,
    page,
    pageSize,
    projectId,
    search: searchParam,
    status: statusFilter !== "all" ? (statusFilter as EvalRunsListStatusEnum) : undefined,
  });

  const pageData = useMemo(() => {
    if (!data) return data;
    if (pendingDelete.size === 0) return data;
    const results = data.results.filter((r) => !r.id || !pendingDelete.has(r.id));
    return { ...data, results };
  }, [data, pendingDelete]);

  const resetToFirstPage = () => {
    if (page !== 1) onSearchChange({ page: 1 });
  };

  // The guard matters: the debounce seeds with the incoming value, so an
  // unguarded effect would reset a deep-linked `?page=` on mount.
  useEffect(() => {
    if (debouncedSearch !== searchParam) onSearchChange({ page: 1, run_search: debouncedSearch });
  }, [debouncedSearch, searchParam, onSearchChange]);

  // A URL filter the facet doesn't carry (stale link, deleted capability/dataset)
  // would hide the select or blank its trigger, leaving the filtered table
  // unexplained and Clear unreachable.
  const capabilityOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const a of capabilitiesData?.results ?? []) if (a.id && a.name) byId.set(a.id, a.name);
    if (capabilityFilter !== "all" && !byId.has(capabilityFilter))
      byId.set(capabilityFilter, "Unknown capability");
    return [...byId]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [capabilitiesData, capabilityFilter]);

  const datasetSelectOptions = useMemo(() => {
    const rows = datasetOptions.map((d) => ({
      id: d.id,
      name: d.name?.trim() || `Dataset ${d.id.slice(0, 8)}`,
    }));
    if (datasetFilter !== "all" && !rows.some((d) => d.id === datasetFilter))
      rows.push({ id: datasetFilter, name: "Unknown dataset" });
    return rows;
  }, [datasetOptions, datasetFilter]);

  const hasActiveFilters =
    statusFilter !== "all" ||
    capabilityFilter !== "all" ||
    datasetFilter !== "all" ||
    search.trim() !== "";

  const clearFilters = () => {
    setSearch("");
    onSearchChange({
      page: 1,
      run_capability: "all",
      run_dataset: "all",
      run_search: "",
      run_status: "all",
    });
  };

  const pageRows = pageData?.results ?? [];
  const allSelected = pageRows.length > 0 && pageRows.every((r) => selectedIds.has(r.id!));
  const someSelected = pageRows.some((r) => selectedIds.has(r.id!));

  const toggleSelect = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleSelectAll = () =>
    setSelectedIds(allSelected ? new Set() : new Set(pageRows.map((r) => r.id!)));

  const clearSelection = () => {
    setSelectedIds(new Set());
  };

  const handleBulkDelete = async () => {
    setBulkDeletePending(true);
    try {
      await Promise.all(
        [...selectedIds].map(
          (id) =>
            new Promise<void>((resolve, reject) =>
              deleteRun.mutate(id, { onError: reject, onSuccess: () => resolve() })
            )
        )
      );
      toast.success(`${plural(selectedIds.size, "run")} deleted`);
      clearSelection();
    } catch (e) {
      notify.error(e, "Couldn't delete some of the selected runs");
    } finally {
      setBulkDeletePending(false);
    }
  };

  const removePending = (id: string) =>
    setPendingDelete((p) => {
      const next = new Set(p);
      next.delete(id);
      return next;
    });
  const handleDelete = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
    notify.undoable({
      commit: () =>
        deleteRun.mutate(id, {
          onError: (e) => {
            notify.error(e, "Couldn't delete run");
            removePending(id);
          },
        }),
      message: "Run deleted",
      optimistic: () => setPendingDelete((p) => new Set(p).add(id)),
      rollback: () => removePending(id),
    });
  };

  const columns: ColumnDef<EvalRunList>[] = [
    {
      cell: ({ row }) => (
        <div className="pl-1">
          <Checkbox
            aria-label={`Select run ${row.original.name}`}
            checked={selectedIds.has(row.original.id!)}
            onCheckedChange={() => toggleSelect(row.original.id!)}
            // The row navigates from its own `onClick`, so the guard has to be on
            // the click: stopping `pointerdown` leaves the later click untouched.
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      ),
      enableResizing: false,
      header: () => (
        <div className="flex h-8 items-center pl-1">
          <Checkbox
            aria-label="Select all runs"
            checked={allSelected ? true : someSelected ? "indeterminate" : false}
            onCheckedChange={toggleSelectAll}
          />
        </div>
      ),
      id: "select",
      maxSize: 48,
      minSize: 40,
      size: 40,
    },
    {
      accessorKey: "name",
      cell: ({ row }) => (
        <span className="block truncate font-mono text-sm" title={row.original.name}>
          {row.original.name}
        </span>
      ),
      header: "Name",
      meta: { orderingField: "name" },
      minSize: 160,
      size: 240,
    },
    {
      accessorKey: "status",
      cell: ({ row }) => {
        const r = row.original;
        return (
          <StatusBadge
            progress={r.status === "running" ? runProgressPercent(r.progress) : null}
            status={r.status ?? "pending"}
          />
        );
      },
      header: "Status",
      meta: { noTruncate: true, orderingField: "status" },
      minSize: 120,
      size: 140,
    },
    {
      accessorKey: "capabilityName",
      cell: ({ row }) => {
        const r = row.original;
        return r.capabilityId ? (
          <EntityRef id={r.capabilityId} kind="capability" name={r.capabilityName} />
        ) : (
          <span className="text-muted-foreground">—</span>
        );
      },
      header: "Capability",
      meta: { noTruncate: true },
      minSize: 120,
      size: 144,
    },
    {
      accessorKey: "datasetName",
      cell: ({ row }) => {
        const r = row.original;
        return r.dataset ? (
          <EntityRef id={r.dataset} kind="dataset" name={r.datasetName} projectId={projectId} />
        ) : (
          <span className="truncate text-xs text-muted-foreground">
            {r.dataSource ? sentenceCase(r.dataSource) : "—"}
          </span>
        );
      },
      header: "Dataset",
      meta: { noTruncate: true },
      minSize: 120,
      size: 144,
    },
    {
      accessorKey: "variantCount",
      cell: ({ row }) => (
        <span className="tabular-nums text-muted-foreground">{row.original.variantCount}</span>
      ),
      header: "Variants",
      minSize: 80,
      size: 100,
    },
    {
      accessorKey: "evaluatorCount",
      cell: ({ row }) => (
        <span className="tabular-nums text-muted-foreground">{row.original.evaluatorCount}</span>
      ),
      header: "Evaluators",
      minSize: 90,
      size: 112,
    },
    {
      accessorKey: "createdAt",
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          <DateTime value={row.original.createdAt} />
        </span>
      ),
      header: "Created",
      meta: { orderingField: "created_at" },
      minSize: 120,
      size: 136,
    },
    {
      cell: ({ row }) => (
        <Button
          aria-label="Delete run"
          className="text-muted-foreground hover:text-destructive"
          // Same click guard as the select checkbox — see that cell.
          onClick={(e) => {
            e.stopPropagation();
            handleDelete(row.original.id!);
          }}
          size="icon-xs"
          title="Delete run"
          variant="ghost"
        >
          <Icon.delete />
        </Button>
      ),
      enableResizing: false,
      header: "Actions",
      id: "actions",
      maxSize: 72,
      minSize: 64,
      size: 64,
    },
  ];

  const toolbar = (
    <ListToolbar
      filters={
        <>
          <Select
            // Same vocabulary the schema's closed set is built from, so the cast holds.
            onValueChange={(v) =>
              onSearchChange({ page: 1, run_status: v as RunsFilters["run_status"] })
            }
            value={statusFilter}
          >
            <SelectTrigger aria-label="Filter by status" className="w-[140px]" size="default">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {Object.values(EvalRunsListStatusEnum).map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_FILTER_LABELS[s] ?? s}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {capabilityOptions.length > 0 && (
            <Select
              onValueChange={(v) => onSearchChange({ page: 1, run_capability: v })}
              value={capabilityFilter}
            >
              <SelectTrigger aria-label="Filter by capability" className="w-[160px]" size="default">
                <SelectValue placeholder="Capability" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All capabilities</SelectItem>
                {capabilityOptions.map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {datasetSelectOptions.length > 0 && (
            <Select
              onValueChange={(v) => onSearchChange({ page: 1, run_dataset: v })}
              value={datasetFilter}
            >
              <SelectTrigger aria-label="Filter by dataset" className="w-[180px]" size="default">
                <SelectValue placeholder="Dataset" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All datasets</SelectItem>
                {datasetSelectOptions.map((d) => (
                  <SelectItem key={d.id} value={d.id}>
                    {d.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </>
      }
      hasActiveFilters={hasActiveFilters}
      onClearFilters={clearFilters}
      primary={action}
      search={
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search runs"
          onChange={(e) => {
            setSearch(e.target.value);
          }}
          onClear={() => {
            setSearch("");
            onSearchChange({ page: 1, run_search: "" });
          }}
          placeholder="Search runs…"
          value={search}
        />
      }
    />
  );

  const banner = someSelected ? (
    <div className="flex shrink-0 items-center gap-2 rounded-md border bg-wash-subtle px-3 py-2 text-sm">
      <Checkbox
        aria-label="Select all runs"
        checked={allSelected ? true : someSelected ? "indeterminate" : false}
        onCheckedChange={toggleSelectAll}
      />
      <span className="flex-1 text-muted-foreground">{selectedIds.size} selected</span>
      <ConfirmDialog
        confirmLabel={`Delete ${plural(selectedIds.size, "run")}`}
        description={`This permanently deletes ${plural(selectedIds.size, "selected run")}.`}
        destructive
        isPending={bulkDeletePending}
        onConfirm={handleBulkDelete}
        title={`Delete ${plural(selectedIds.size, "run")}?`}
        trigger={
          <Button className="px-2 text-xs" size="sm" variant="destructive">
            <Icon.delete /> Delete {selectedIds.size}
          </Button>
        }
      />
      <Button
        aria-label="Clear selection"
        className="px-2 text-xs"
        onClick={clearSelection}
        size="sm"
        variant="ghost"
      >
        <Icon.close />
      </Button>
    </div>
  ) : null;

  return (
    <DataTable
      banner={banner}
      columns={columns}
      data={pageData}
      emptyState={
        <EmptyState
          className="flex-1"
          description="Evaluation runs appear here as they are launched."
          icon={Icon.evaluations}
          iconClassName="dark:invert [image-rendering:pixelated]"
          size="section"
          title="No runs yet"
        />
      }
      error={error}
      getRowId={(row) => row.id!}
      hasActiveFilters={hasActiveFilters}
      isLoading={isLoading}
      onClearFilters={clearFilters}
      onOrderingChange={(o) => {
        setOrdering(o ?? "");
        resetToFirstPage();
      }}
      onPageChange={(p) => onSearchChange({ page: p })}
      onPageSizeChange={(s) => onSearchChange({ page: 1, page_size: s })}
      // Carry the whole search, not just `projectId`: the breadcrumb back out
      // rebuilds this URL from the detail page's search.
      onRowClick={(r) =>
        navigate({
          params: { runId: r.id! },
          resetScroll: false,
          search: (prev) => prev,
          to: "/evaluations/runs/$runId",
        })
      }
      onRowMouseEnter={(r) => {
        if (r.id) clearUnseenCompletion(`eval:${r.id}`);
      }}
      ordering={ordering || undefined}
      page={page}
      pageSize={pageSize}
      rowClassName={(r) =>
        cn(
          selectedIds.has(r.id!) && "bg-primary/5",
          r.id && unseenKeys.has(`eval:${r.id}`) && "bg-wash-subtle"
        )
      }
      storageKey="evaluations:runs-table-cols"
      toolbar={toolbar}
    />
  );
}
