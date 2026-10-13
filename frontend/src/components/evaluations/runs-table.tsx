import { useEffect, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { asRunSummary, ScoreChip } from "@/components/evaluations/run-comparison";
import { Badge } from "@/components/ui/badge";
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
  useEvalRunDatasetsQuery,
  useEvalRunsQuery,
  useProjectCapabilitiesQuery,
} from "@/hooks/use-evaluations";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { StatusBadge as JobStatusBadge } from "@/lib/job-status";
import type { EvaluationsSearch } from "@/lib/schemas";
import { clearUnseenCompletion, useUnseenCompletionKeys } from "@/lib/unseen-job-completions";
import type { EvalRun, EvalRunProgress } from "@/openapi";
import { EvalRunsListStatusEnum } from "@/openapi";

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
  if (!progress || progress.rows === 0) return null;
  return ((progress.done + progress.errored) / progress.rows) * 100;
}

const MODE_LABEL: Record<string, string> = {
  generate: "Generated",
  provided: "Provided",
  recorded: "Recorded",
};

type RunsFilters = Pick<
  EvaluationsSearch,
  "run_capability" | "run_dataset" | "run_search" | "run_status"
>;
interface RunsTableProps {
  projectId: string;
  page: number;
  pageSize: number;
  filters: RunsFilters;
  onSearchChange: (updates: Partial<RunsFilters> & { page?: number; page_size?: number }) => void;
}

export function RunsTable({ projectId, page, pageSize, filters, onSearchChange }: RunsTableProps) {
  const navigate = useNavigate();
  const unseenKeys = useUnseenCompletionKeys();
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

  const columns: ColumnDef<EvalRun>[] = [
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
          <span className="text-muted-foreground">—</span>
        );
      },
      header: "Dataset",
      meta: { noTruncate: true },
      minSize: 120,
      size: 144,
    },
    {
      accessorKey: "label",
      cell: ({ row }) => {
        const r = row.original;
        return (
          <span className="flex min-w-0 items-center gap-1.5">
            <span className="truncate text-sm" title={r.resolvedModel || r.modelName}>
              {r.label || MODE_LABEL[r.mode] || r.mode}
            </span>
            {r.isBaseline && (
              <Badge size="chip" variant="neutral">
                Baseline
              </Badge>
            )}
          </span>
        );
      },
      header: "Target",
      meta: { noTruncate: true },
      minSize: 120,
      size: 180,
    },
    {
      cell: ({ row }) => <ScoreChip value={asRunSummary(row.original.summary).score} />,
      header: "Score",
      id: "score",
      minSize: 72,
      size: 88,
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

  return (
    <DataTable
      columns={columns}
      data={data}
      emptyState={
        <EmptyState
          className="flex-1"
          description="Runs appear here when started from MCP, the CLI, training or the optimiser."
          icon={Icon.evaluations}
          iconClassName="invert [image-rendering:pixelated]"
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
      rowClassName={(r) => (r.id && unseenKeys.has(`eval:${r.id}`) ? "bg-wash-subtle" : undefined)}
      storageKey="evaluations:runs-table-cols"
      toolbar={toolbar}
    />
  );
}
