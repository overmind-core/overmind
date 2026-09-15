import { type ReactNode, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { IntentBadge, SOURCE_KIND_LABEL, StateBadge } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import { Button } from "@/components/ui/button";
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
import { datasetDisplayName, useDeleteDatasetMutation } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import { formatNumber } from "@/lib/formatters";
import { cn } from "@/lib/utils";
import type { Dataset } from "@/openapi";

const NO_CAPABILITY = "__none__";

const filterDatasets = (
  datasets: Dataset[],
  search: string,
  intentFilter = "all",
  capabilityFilter = "all"
): Dataset[] => {
  const q = search.trim().toLowerCase();
  return datasets.filter((ds) => {
    if (intentFilter !== "all" && ds.intent !== intentFilter) return false;
    if (capabilityFilter !== "all" && (ds.capability || NO_CAPABILITY) !== capabilityFilter) {
      return false;
    }
    if (!q) return true;
    return (
      datasetDisplayName(ds).toLowerCase().includes(q) ||
      ds.id.toLowerCase().startsWith(q) ||
      (ds.capabilityName ?? "").toLowerCase().includes(q) ||
      (SOURCE_KIND_LABEL[ds.sourceKind] ?? "").toLowerCase().includes(q)
    );
  });
};

export interface DatasetsFilterProps {
  datasets: Dataset[];
  search: string;
  setSearch: (v: string) => void;
  intentFilter: string;
  setIntentFilter: (v: string) => void;
  capabilityFilter?: string;
  setCapabilityFilter?: (v: string) => void;
  capabilityNameById?: Record<string, string>;
}

export function DatasetsToolbar({
  datasets,
  search,
  setSearch,
  intentFilter,
  setIntentFilter,
  capabilityFilter = "all",
  setCapabilityFilter,
  capabilityNameById = {},
  onCreate,
}: DatasetsFilterProps & { onCreate: () => void }) {
  const guard = useGuestGate();
  const capabilityIds = useMemo(() => {
    const ids = new Set<string>(Object.keys(capabilityNameById));
    for (const ds of datasets) ids.add(ds.capability || NO_CAPABILITY);
    ids.add(NO_CAPABILITY);
    return [...ids].sort((a, b) => {
      if (a === NO_CAPABILITY) return 1;
      if (b === NO_CAPABILITY) return -1;
      return (capabilityNameById[a] ?? a).localeCompare(capabilityNameById[b] ?? b);
    });
  }, [datasets, capabilityNameById]);
  const hasActiveFilters = intentFilter !== "all" || capabilityFilter !== "all" || !!search;

  return (
    <ListToolbar
      filters={
        <>
          <Select onValueChange={setIntentFilter} value={intentFilter}>
            <SelectTrigger aria-label="Filter by intent" className="w-[140px]" size="default">
              <SelectValue placeholder="All intents" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All intents</SelectItem>
              <SelectItem value="train">Train</SelectItem>
              <SelectItem value="eval">Eval</SelectItem>
              <SelectItem value="pending">Pending</SelectItem>
            </SelectContent>
          </Select>
          {setCapabilityFilter && (
            <Select onValueChange={setCapabilityFilter} value={capabilityFilter}>
              <SelectTrigger aria-label="Filter by capability" className="w-[180px]" size="default">
                <SelectValue placeholder="All capabilities" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All capabilities</SelectItem>
                {capabilityIds.map((id) => (
                  <SelectItem key={id} value={id}>
                    {id === NO_CAPABILITY ? "No capability" : (capabilityNameById[id] ?? id)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </>
      }
      hasActiveFilters={hasActiveFilters}
      onClearFilters={() => {
        setSearch("");
        setIntentFilter("all");
        setCapabilityFilter?.("all");
      }}
      primary={
        <Button onClick={guard(onCreate)}>
          <Icon.datasetAdd />
          New dataset
        </Button>
      }
      search={
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search datasets"
          onChange={(e) => setSearch(e.target.value)}
          onClear={() => setSearch("")}
          placeholder="Search datasets"
          value={search}
        />
      }
    />
  );
}

type SortKey = "name" | "intent" | "rows" | "version" | "capability" | "updated";

const sortValue = (ds: Dataset, key: SortKey): string | number => {
  switch (key) {
    case "name":
      return datasetDisplayName(ds).toLowerCase();
    case "intent":
      return ds.intent ?? "";
    case "rows":
      return ds.rows ?? 0;
    case "version":
      return ds.activeVersion ?? "";
    case "capability":
      return (ds.capabilityName ?? "").toLowerCase();
    case "updated":
      return new Date(ds.updatedAt).getTime();
  }
};

function getColumns(opts: {
  onDelete: (ds: Dataset) => void;
  onDeleteClick?: (e: React.MouseEvent) => void;
  deletePendingId?: string | null;
  hideCapabilityColumn?: boolean;
}): ColumnDef<Dataset>[] {
  const cols: ColumnDef<Dataset>[] = [
    {
      accessorFn: (row) => datasetDisplayName(row),
      cell: ({ row }) => (
        <span className="flex min-w-0 items-center gap-2">
          <span
            className="truncate font-mono text-sm text-foreground"
            title={datasetDisplayName(row.original)}
          >
            {datasetDisplayName(row.original)}
          </span>
        </span>
      ),
      header: "Name",
      id: "name",
      meta: { label: "Name", orderingField: "name" },
      minSize: 160,
      size: 260,
    },
    {
      accessorFn: (row) => row.intent ?? "",
      cell: ({ row }) => (
        <span className="flex items-center gap-1.5">
          <IntentBadge intent={row.original.intent} />
          <StateBadge dataset={row.original} />
        </span>
      ),
      header: "Intent",
      id: "intent",
      meta: { label: "Intent", noTruncate: true, orderingField: "intent" },
      minSize: 110,
      size: 150,
    },
    {
      accessorFn: (row) => row.activeVersion ?? "",
      cell: ({ row }) => (
        <span className="font-mono text-sm text-foreground">
          {row.original.activeVersion || <span className="text-muted-foreground">—</span>}
        </span>
      ),
      header: "Version",
      id: "version",
      meta: { label: "Version", orderingField: "version" },
      minSize: 70,
      size: 90,
    },
    {
      accessorFn: (row) => row.rows ?? 0,
      cell: ({ row }) => (
        <span className="tabular-nums text-sm text-foreground">
          {formatNumber(row.original.rows ?? 0)}
        </span>
      ),
      header: "Rows",
      id: "rows",
      meta: { label: "Rows", orderingField: "rows" },
      minSize: 80,
      size: 110,
    },
    {
      accessorKey: "sourceKind",
      cell: ({ row }) => (
        <span className="text-sm text-foreground">
          {SOURCE_KIND_LABEL[row.original.sourceKind] ?? row.original.sourceKind}
        </span>
      ),
      header: "Source",
      id: "source",
      meta: { label: "Source" },
      minSize: 80,
      size: 100,
    },
  ];

  if (!opts.hideCapabilityColumn) {
    cols.push({
      accessorFn: (row) => row.capabilityName ?? "",
      cell: ({ row }) =>
        row.original.capability ? (
          <div className="flex w-full justify-start">
            <EntityRef
              id={row.original.capability}
              kind="capability"
              name={row.original.capabilityName}
            />
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
      header: "Capability",
      id: "capability",
      meta: { label: "Capability", noTruncate: true, orderingField: "capability" },
      minSize: 120,
      size: 160,
    });
  }

  cols.push(
    {
      accessorKey: "updatedAt",
      cell: ({ row }) => (
        <DateTime
          className="whitespace-nowrap text-sm text-muted-foreground"
          value={row.original.updatedAt}
        />
      ),
      header: "Updated",
      id: "updated",
      meta: { label: "Updated", orderingField: "updated" },
      minSize: 110,
      size: 120,
    },
    {
      cell: ({ row }) => {
        const ds = row.original;
        return (
          <div className="flex w-full justify-start">
            {/* biome-ignore lint/a11y/useKeyWithClickEvents: guard only, not interactive */}
            <span onClick={(e) => e.stopPropagation()}>
              <ConfirmDialog
                confirmLabel="Delete"
                description={`${datasetDisplayName(ds)} and every version of it will be removed. Runs that used a version keep this dataset in place.`}
                destructive
                isPending={opts.deletePendingId === ds.id}
                onConfirm={() => opts.onDelete(ds)}
                title="Delete dataset?"
                trigger={
                  <Button
                    aria-label={`Delete ${datasetDisplayName(ds)}`}
                    className="text-muted-foreground/60 hover:text-destructive"
                    onClick={opts.onDeleteClick}
                    size="icon-sm"
                    variant="ghost"
                  >
                    <Icon.delete className="size-3.5" />
                  </Button>
                }
              />
            </span>
          </div>
        );
      },
      enableResizing: false,
      header: "",
      id: "actions",
      meta: { noTruncate: true },
      size: 44,
    }
  );
  return cols;
}

export function DatasetsBrowser({
  datasets,
  search,
  setSearch,
  intentFilter,
  setIntentFilter,
  capabilityFilter = "all",
  setCapabilityFilter,
  isLoading,
  error,
  hideCapabilityColumn,
  emptyAction,
  emptyDescription = "Upload a file, paste rows, or land traces to create the first one.",
  fill = true,
  rowSearch,
}: DatasetsFilterProps & {
  isLoading: boolean;
  error?: unknown;
  hideCapabilityColumn?: boolean;
  emptyAction?: ReactNode;
  emptyDescription?: string;
  fill?: boolean;
  rowSearch?: Record<string, string>;
}) {
  const navigate = useNavigate();
  const guard = useGuestGate();
  const deleteMutation = useDeleteDatasetMutation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState<string | undefined>("-updated");

  const filtered = useMemo(
    () => filterDatasets(datasets, search, intentFilter, capabilityFilter),
    [datasets, search, intentFilter, capabilityFilter]
  );
  const sorted = useMemo(() => {
    const parsed = parseDrfOrdering(ordering);
    if (!parsed) return filtered;
    const key = parsed.field as SortKey;
    const dir = parsed.dir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const va = sortValue(a, key);
      const vb = sortValue(b, key);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return 0;
    });
  }, [filtered, ordering]);
  const pageRows = sorted.slice((page - 1) * pageSize, page * pageSize);
  const hasActiveFilters = intentFilter !== "all" || capabilityFilter !== "all" || !!search;

  const columns = useMemo(
    () =>
      getColumns({
        deletePendingId: deleteMutation.isPending ? deleteMutation.variables : null,
        hideCapabilityColumn,
        onDelete: (ds) => deleteMutation.mutate(ds.id),
        onDeleteClick: guard(() => undefined),
      }),
    [deleteMutation, guard, hideCapabilityColumn]
  );

  return (
    <DataTable
      className={cn(fill && "min-h-0 flex-1")}
      columns={columns}
      data={{ count: sorted.length, results: pageRows }}
      emptyState={
        <EmptyState
          action={emptyAction}
          className="flex-1"
          description={emptyDescription}
          icon={Icon.dataset}
          size="section"
          title="No datasets yet"
        />
      }
      error={error}
      getRowId={(row) => row.id}
      hasActiveFilters={hasActiveFilters}
      isLoading={isLoading}
      onClearFilters={() => {
        setSearch("");
        setIntentFilter("all");
        setCapabilityFilter?.("all");
      }}
      onOrderingChange={(o) => {
        setOrdering(o);
        setPage(1);
      }}
      onPageChange={setPage}
      onPageSizeChange={(s) => {
        setPageSize(s);
        setPage(1);
      }}
      onRowClick={(ds) =>
        navigate({
          params: { datasetId: ds.id },
          search: (prev) => ({ ...prev, ...(rowSearch ?? {}) }),
          to: "/datasets/$datasetId",
        })
      }
      ordering={ordering}
      page={page}
      pageSize={pageSize}
      storageKey="datasets-table"
    />
  );
}
