import { useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { SOURCE_KIND_LABEL } from "@/components/datasets/badges";
import { EntityRef } from "@/components/entity-ref";
import { Badge } from "@/components/ui/badge";
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
import { useActivityHighlights } from "@/hooks/use-activity";
import { datasetDisplayName, useDeleteDatasetMutation } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import { ResolvedStatusBadge, resolveStatus } from "@/lib/job-status";
import { cn } from "@/lib/utils";
import type { Dataset } from "@/openapi";

const DATASET_ROLES: Record<string, string> = {
  calibration: "Calibration",
  development: "Development",
  evaluation: "Evaluation",
  source: "Source",
  training: "Training",
};

const NO_CAPABILITY = "__none__";

const filterDatasets = (
  datasets: Dataset[],
  search: string,
  roleFilter = "all",
  capabilityFilter = "all"
): Dataset[] => {
  const q = search.trim().toLowerCase();
  return datasets.filter((ds) => {
    if (
      roleFilter === "unassigned"
        ? ds.roles.length > 0
        : roleFilter !== "all" && !ds.roles.some((role) => role === roleFilter)
    )
      return false;
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
  roleFilter: string;
  setRoleFilter: (v: string) => void;
  capabilityFilter?: string;
  setCapabilityFilter?: (v: string) => void;
  capabilityNameById?: Record<string, string>;
}

export function DatasetsToolbar({
  datasets,
  search,
  setSearch,
  roleFilter,
  setRoleFilter,
  capabilityFilter = "all",
  setCapabilityFilter,
  capabilityNameById = {},
}: DatasetsFilterProps) {
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
  const hasActiveFilters = roleFilter !== "all" || capabilityFilter !== "all" || !!search;

  return (
    <ListToolbar
      filters={
        <>
          <Select onValueChange={setRoleFilter} value={roleFilter}>
            <SelectTrigger aria-label="Filter by role" className="w-[140px]" size="default">
              <SelectValue placeholder="All roles" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All roles</SelectItem>
              {Object.entries(DATASET_ROLES).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
              <SelectItem value="unassigned">Unassigned</SelectItem>
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
        setRoleFilter("all");
        setCapabilityFilter?.("all");
      }}
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

type SortKey = "source" | "name" | "role" | "rows" | "version" | "capability" | "updated";

const sortValue = (ds: Dataset, key: SortKey): string | number => {
  switch (key) {
    case "source":
      return SOURCE_KIND_LABEL[ds.sourceKind] ?? ds.sourceKind;
    case "name":
      return datasetDisplayName(ds).toLowerCase();
    case "role":
      return ds.roles.map((role) => DATASET_ROLES[role]).join(", ");
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

function DatasetStatus({ dataset }: { dataset: Dataset }) {
  const work = dataset.work;
  const queued =
    work?.status === "queued" ||
    (!work && dataset.state === "landing" && dataset.operation?.state === "queued");
  const busy = !!work || dataset.state === "running" || dataset.state === "landing";
  const failed = !work && dataset.state === "error";
  if (!busy && !failed) {
    return (
      <span className="text-sm text-muted-foreground">
        {dataset.activeVersion ? "Ready" : "Waiting for source"}
      </span>
    );
  }
  const label = failed
    ? "Failed"
    : queued
      ? "Queued"
      : (work?.label ?? (dataset.state === "landing" ? "Importing" : "Transforming"));
  const completed = work?.completed;
  const total = work?.total;
  const percent =
    typeof completed === "number" && typeof total === "number" && total > 0
      ? (completed / total) * 100
      : null;
  return (
    <span aria-live="polite" className="inline-flex items-center gap-2">
      <ResolvedStatusBadge
        actorKind={work?.actorKind ?? undefined}
        cfg={resolveStatus(
          failed ? "failed" : (work?.status ?? (queued ? "queued" : "running")),
          "running"
        )}
        label={label}
        progress={percent}
      />
    </span>
  );
}

function getColumns(opts: {
  roleWidth: number;
  sourceWidth: number;
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
      cell: ({ row }) => <DatasetStatus dataset={row.original} />,
      header: "Status",
      id: "status",
      meta: { label: "Status", noTruncate: true },
      minSize: 140,
      size: 190,
    },
    {
      accessorFn: (row) => row.roles.join(", "),
      cell: ({ row }) =>
        row.original.roles.length ? (
          <span className="inline-flex flex-nowrap gap-1">
            {row.original.roles.map((role) => (
              <Badge key={role} size="chip" variant="outline">
                {DATASET_ROLES[role]}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
      header: "Role",
      id: "role",
      meta: { label: "Role", noTruncate: true, orderingField: "role" },
      minSize: opts.roleWidth,
      size: opts.roleWidth,
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
          {row.original.activeVersion ? (
            row.original.rows.toLocaleString()
          ) : (
            <span className="text-muted-foreground">—</span>
          )}
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
      cell: ({ row }) => {
        const dataset = row.original;
        const sources: { filename: string }[] = dataset.sourceSpec?.sources ?? [];
        const filenames = sources.length
          ? sources.map((source) => source.filename)
          : dataset.sourceSpec?.filename
            ? [String(dataset.sourceSpec.filename)]
            : [];
        return filenames.length ? (
          <span className="flex min-w-0 items-center gap-1 whitespace-nowrap">
            {filenames.slice(0, 3).map((filename, index) => {
              const extension = filename.replace(/\.gz$/i, "").match(/\.([^.]+)$/)?.[1];
              return (
                <Badge
                  className="min-w-0 max-w-44 gap-1.5"
                  key={`${filename}:${index}`}
                  size="chip"
                  variant="outline"
                >
                  <Icon.file className="size-3 shrink-0 text-muted-foreground" />
                  <span className="min-w-0 truncate">{filename}</span>
                  <span className="shrink-0 text-muted-foreground">
                    {extension?.toUpperCase() ?? "File"}
                  </span>
                </Badge>
              );
            })}
            {filenames.length > 3 && (
              <Badge className="shrink-0" size="chip" variant="outline">
                +{filenames.length - 3} files
              </Badge>
            )}
          </span>
        ) : (
          <span className="text-sm text-foreground">
            {SOURCE_KIND_LABEL[dataset.sourceKind] ?? dataset.sourceKind}
          </span>
        );
      },
      header: "Source",
      id: "source",
      meta: { label: "Source", noTruncate: true, orderingField: "source" },
      minSize: opts.sourceWidth,
      size: opts.sourceWidth,
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
  roleFilter,
  setRoleFilter,
  capabilityFilter = "all",
  setCapabilityFilter,
  isLoading,
  error,
  hideCapabilityColumn,
  fill = true,
  rowSearch,
}: DatasetsFilterProps & {
  isLoading: boolean;
  error?: unknown;
  hideCapabilityColumn?: boolean;
  fill?: boolean;
  rowSearch?: Record<string, string>;
}) {
  const highlights = useActivityHighlights();
  const navigate = useNavigate();
  const guard = useGuestGate();
  const deleteMutation = useDeleteDatasetMutation();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [ordering, setOrdering] = useState<string | undefined>("-updated");

  const filtered = useMemo(
    () => filterDatasets(datasets, search, roleFilter, capabilityFilter),
    [datasets, search, roleFilter, capabilityFilter]
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
  const currentPage = Math.min(page, Math.max(1, Math.ceil(sorted.length / pageSize)));
  const pageRows = sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const hasActiveFilters = roleFilter !== "all" || capabilityFilter !== "all" || !!search;

  const roleWidth = Math.max(
    130,
    ...datasets.map(
      (dataset) =>
        20 + dataset.roles.reduce((width, role) => width + DATASET_ROLES[role].length * 8 + 22, 0)
    )
  );

  const sourceWidth = Math.max(
    180,
    ...datasets.map((dataset) => {
      const count = dataset.sourceSpec?.sources?.length ?? (dataset.sourceSpec?.filename ? 1 : 0);
      return Math.min(3, count) * 180 + 24 + (count > 3 ? 80 : 0);
    })
  );

  const columns = useMemo(
    () =>
      getColumns({
        deletePendingId: deleteMutation.isPending ? deleteMutation.variables : null,
        hideCapabilityColumn,
        onDelete: (ds) => deleteMutation.mutate(ds.id),
        onDeleteClick: guard(() => undefined),
        roleWidth,
        sourceWidth,
      }),
    [deleteMutation, guard, hideCapabilityColumn, roleWidth, sourceWidth]
  );

  return (
    <DataTable
      className={cn(fill && "min-h-0 flex-1")}
      columns={columns}
      data={{ count: sorted.length, results: pageRows }}
      emptyState={
        <EmptyState className="flex-1" icon={Icon.dataset} size="section" title="No datasets yet" />
      }
      error={error}
      getRowId={(row) => row.id}
      hasActiveFilters={hasActiveFilters}
      isLoading={isLoading}
      onClearFilters={() => {
        setSearch("");
        setRoleFilter("all");
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
      page={currentPage}
      pageSize={pageSize}
      rowClassName={(row) => (highlights[`dataset:${row.id}`] ? "activity-highlight" : undefined)}
      storageKey="datasets-table"
    />
  );
}
