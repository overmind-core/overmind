import { type ReactNode, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef, OnChangeFn, RowSelectionState } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { FileTypeIcon } from "@/components/file-type-icon";
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
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useActivityHighlights } from "@/hooks/use-activity";
import {
  compactCount,
  datasetDisplayName,
  INTENT_LABEL,
  intentLabel,
  intentOf,
  SPLIT_LABEL,
  useDeleteDatasetMutation,
  workText,
} from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import type { WorkspaceDataset } from "@/lib/dataset-workspaces";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import { ResolvedStatusBadge, resolveStatus } from "@/lib/job-status";
import { cn } from "@/lib/utils";
import type { Dataset } from "@/openapi";

/** What a dataset is for, from its intent: the one value the Role column shows. */
export const ROLE_LABEL: Record<string, string> = {
  decision: intentLabel("train", "decision"),
  eval: intentLabel("eval"),
  pending: intentLabel("pending"),
  sft: intentLabel("train", "sft"),
  train: intentLabel("train"),
};

const roleOf = (ds: Dataset): string => {
  const intent = intentOf(ds);
  return intent === "train" ? ds.trainFormat || "train" : intent;
};

// In the order a model meets them; a workspace merges its members' uses in any order.
const USES: [string, string][] = [
  ["training", SPLIT_LABEL.train],
  ["development", SPLIT_LABEL.development],
  ["calibration", SPLIT_LABEL.calibration],
  ["evaluation", INTENT_LABEL.eval],
];

/** Where runs and partitions used the dataset, as one sentence for the role's tooltip. */
const usedText = (ds: Dataset): string | null => {
  if (!ds.roles.length) return null;
  const uses = ds.roles.map((role) => role.toLowerCase());
  return `Used for ${uses.length > 1 ? `${uses.slice(0, -1).join(", ")} and ${uses.at(-1)}` : uses[0]}`;
};

const NO_CAPABILITY = "__none__";

const filterDatasets = (
  datasets: Dataset[],
  search: string,
  roleFilter = "all",
  capabilityFilter = "all",
  nameById: Record<string, string> = {}
): Dataset[] => {
  const q = search.trim().toLowerCase();
  return datasets.filter((ds) => {
    if (roleFilter !== "all" && roleOf(ds) !== roleFilter) return false;
    if (capabilityFilter !== "all" && (ds.capability || NO_CAPABILITY) !== capabilityFilter) {
      return false;
    }
    if (!q) return true;
    return (
      datasetDisplayName(ds).toLowerCase().includes(q) ||
      ds.id.toLowerCase().startsWith(q) ||
      (ds.capabilityName ?? "").toLowerCase().includes(q) ||
      [
        describeSource(ds, nameById)?.label ?? "",
        ...(describeSource(ds, nameById)?.more ?? []),
      ].some((text) => text.toLowerCase().includes(q))
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
            <SelectTrigger aria-label="Filter by role" className="w-[160px]" size="default">
              <SelectValue placeholder="All roles" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All roles</SelectItem>
              {Object.entries(ROLE_LABEL).map(([value, label]) => (
                <SelectItem key={value} value={value}>
                  {label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {setCapabilityFilter && capabilityIds.length > 1 && (
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

type SortKey = "source" | "name" | "role" | "rows" | "capability" | "updated";

const sourceText = (ds: Dataset, nameById: Record<string, string>): string =>
  describeSource(ds, nameById)?.label ?? "";

const sortValue = (
  ds: Dataset,
  key: SortKey,
  nameById: Record<string, string>
): string | number => {
  switch (key) {
    case "source":
      return sourceText(ds, nameById).toLowerCase();
    case "name":
      return datasetDisplayName(ds).toLowerCase();
    case "role":
      return ROLE_LABEL[roleOf(ds)];
    case "rows":
      return ds.activeVersion ? ds.rows : -1;
    case "capability":
      return (ds.capabilityName ?? "").toLowerCase();
    case "updated":
      return new Date(ds.updatedAt).getTime();
  }
};

/** One height, border and type size for every chip the table shows. */
const TABLE_CHIP =
  "inline-flex h-5 max-w-full min-w-0 items-center gap-1.5 rounded-sm border border-border/70 bg-transparent px-1.5 py-0 text-xs text-foreground";

function Chip({ children, title }: { children: ReactNode; title?: string | null }) {
  if (!title) return <span className={TABLE_CHIP}>{children}</span>;
  return (
    <Tooltip>
      {/* Focusable, so a keyboard reaches what the tooltip says. */}
      <TooltipTrigger asChild>
        <span
          className={cn(TABLE_CHIP, "outline-none focus-visible:ring-2 focus-visible:ring-ring")}
          // biome-ignore lint/a11y/noNoninteractiveTabindex: the tooltip is its content
          tabIndex={0}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{title}</TooltipContent>
    </Tooltip>
  );
}

const Dash = () => <span className="text-sm text-muted-foreground">—</span>;

interface SourceDescription {
  icon: ReactNode;
  label: string;
  more: string[];
}

/** One label for any source: files, pasted rows, traces, LLM calls or another dataset. */
function describeSource(ds: Dataset, nameById: Record<string, string>): SourceDescription | null {
  const spec = (ds.sourceSpec ?? {}) as {
    sources?: { filename: string }[];
    filename?: string;
    pasted?: boolean;
    derived_from?: { dataset_id?: string; version?: string };
    partition_plan?: string;
    role?: string;
  };
  const icon = (Glyph: (typeof Icon)[keyof typeof Icon]) => <Glyph className="size-3.5 shrink-0" />;
  if (spec.derived_from) {
    const name = nameById[spec.derived_from.dataset_id ?? ""] ?? "a dataset";
    const version = spec.derived_from.version ? ` ${spec.derived_from.version}` : "";
    return { icon: icon(Icon.gitBranch), label: `From ${name}${version}`, more: [] };
  }
  if (spec.partition_plan)
    return {
      icon: icon(Icon.gitBranch),
      label: `Split · ${SPLIT_LABEL[spec.role ?? ""] ?? spec.role}`,
      more: [],
    };
  if (ds.sourceKind === "traces")
    return { icon: icon(Icon.observability), label: "Traces", more: [] };
  if (ds.sourceKind === "llm_calls")
    return { icon: icon(Icon.model), label: "LLM calls", more: [] };
  const files = spec.sources?.length
    ? spec.sources.map((source) => source.filename)
    : spec.filename
      ? [spec.filename]
      : [];
  // Rows sent inline (pasted, or given through the API) name no file.
  if (!files.length)
    return ds.activeVersion && ds.sourceKind === "file"
      ? { icon: icon(Icon.clipboard), label: "Inline rows", more: [] }
      : null;
  return {
    icon: <FileTypeIcon className="size-3.5" filename={files[0]} />,
    label: files[0],
    more: files.slice(1),
  };
}

/** Busy or failed work beside the name; an idle dataset shows its name alone. */
function NameStatus({ dataset }: { dataset: Dataset }) {
  const work = dataset.work;
  const failed = !work && dataset.state === "error";
  const queued =
    work?.status === "queued" ||
    (!work && dataset.state === "landing" && dataset.operation?.state === "queued");
  const busy = !!work || dataset.state === "running" || dataset.state === "landing";
  if (!busy && !failed) return null;
  const completed = work?.completed;
  const total = work?.total;
  const percent =
    typeof completed === "number" && typeof total === "number" && total > 0
      ? (completed / total) * 100
      : null;
  const label = failed
    ? "Failed"
    : queued
      ? "Queued"
      : work
        ? workText(work, { compact: true })
        : dataset.state === "landing"
          ? "Importing"
          : "Transforming";
  return (
    <span
      aria-live="polite"
      className="inline-flex shrink-0"
      title={failed ? dataset.error : undefined}
    >
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
  nameById: Record<string, string>;
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
            className="truncate text-sm text-foreground"
            title={datasetDisplayName(row.original)}
          >
            {datasetDisplayName(row.original)}
          </span>
          <NameStatus dataset={row.original} />
        </span>
      ),
      header: "Name",
      id: "name",
      meta: { label: "Name", orderingField: "name" },
      minSize: 200,
      size: 280,
    },
    {
      accessorFn: (row) => ROLE_LABEL[roleOf(row)],
      cell: ({ row }) => {
        const role = roleOf(row.original);
        const used = usedText(row.original);
        if (role === "pending" && !used) return <Dash />;
        // With no intent, a used dataset (often the source of a split) shows its uses.
        const fromUses = role === "pending";
        return (
          <Chip title={fromUses ? `Intent not set · ${used}` : used}>
            <span className="truncate font-mono">
              {fromUses
                ? USES.filter(([use]) => row.original.roles.includes(use as never))
                    .map(([use, short]) => {
                      // The training half of a split names its format: "Train · SFT + Eval".
                      const format = (row.original as WorkspaceDataset).useFormat;
                      return use === "training" && format ? intentLabel("train", format) : short;
                    })
                    .join(" + ")
                : ROLE_LABEL[role]}
            </span>
          </Chip>
        );
      },
      header: "Role",
      id: "role",
      meta: { label: "Role", noTruncate: true, orderingField: "role" },
      minSize: 130,
      size: 140,
    },
  ];

  if (!opts.hideCapabilityColumn) {
    cols.push({
      accessorFn: (row) => row.capabilityName ?? "",
      cell: ({ row }) =>
        row.original.capability ? (
          <div className="flex w-full min-w-0 justify-start">
            <EntityRef
              className={TABLE_CHIP}
              id={row.original.capability}
              kind="capability"
              name={row.original.capabilityName}
            />
          </div>
        ) : (
          <Dash />
        ),
      header: "Capability",
      id: "capability",
      meta: { label: "Capability", noTruncate: true, orderingField: "capability" },
      minSize: 120,
      size: 150,
    });
  }

  cols.push(
    {
      accessorFn: (row) => row.rows ?? 0,
      cell: ({ row }) =>
        row.original.activeVersion ? (
          <span
            className="text-sm tabular-nums text-foreground"
            title={row.original.rows.toLocaleString()}
          >
            {compactCount(row.original.rows)}
          </span>
        ) : (
          <Dash />
        ),
      header: "Datapoints",
      id: "rows",
      meta: { label: "Datapoints", orderingField: "rows" },
      minSize: 96,
      size: 104,
    },
    {
      accessorFn: (row) => sourceText(row, opts.nameById),
      cell: ({ row }) => {
        const source = describeSource(row.original, opts.nameById);
        if (!source) return <Dash />;
        return (
          <Chip title={[source.label, ...source.more].join(", ")}>
            {source.icon}
            <span className="min-w-0 truncate">{source.label}</span>
            {source.more.length > 0 && (
              <span className="shrink-0 text-muted-foreground">+{source.more.length}</span>
            )}
          </Chip>
        );
      },
      header: "Source",
      id: "source",
      meta: { label: "Source", noTruncate: true, orderingField: "source" },
      minSize: 140,
      size: 200,
    },
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
      size: 110,
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

const ATTACH_COLUMN: ColumnDef<Dataset> = {
  cell: ({ row }) => {
    const published = !!row.original.activeVersion;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">
            <Checkbox
              aria-label={`Attach ${datasetDisplayName(row.original)} to chat`}
              checked={row.getIsSelected()}
              disabled={!published}
              onCheckedChange={(checked) => row.toggleSelected(checked === true)}
              onClick={(event) => event.stopPropagation()}
            />
          </span>
        </TooltipTrigger>
        <TooltipContent>
          {!published
            ? "This dataset has no published data."
            : row.getIsSelected()
              ? "Remove from chat"
              : "Attach to chat"}
        </TooltipContent>
      </Tooltip>
    );
  },
  enableResizing: false,
  enableSorting: false,
  header: "",
  id: "attach",
  meta: { label: "Attach to chat", noTruncate: true },
  size: 36,
};

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
  onRetry,
  hideCapabilityColumn,
  fill = true,
  rowSearch,
  rowSelection,
  onRowSelectionChange,
}: DatasetsFilterProps & {
  /** With both set, each row gets a checkbox that attaches its dataset to the assistant. */
  rowSelection?: RowSelectionState;
  onRowSelectionChange?: OnChangeFn<RowSelectionState>;
  isLoading: boolean;
  error?: unknown;
  onRetry?: () => unknown;
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

  // Keyed by the names themselves: the inventory is a new array on every render, and new
  // columns would reset the table.
  const names = JSON.stringify(
    datasets.map((dataset) => [dataset.id, datasetDisplayName(dataset)])
  );
  const nameById = useMemo(() => Object.fromEntries(JSON.parse(names)), [names]);
  const filtered = useMemo(
    () => filterDatasets(datasets, search, roleFilter, capabilityFilter, nameById),
    [datasets, search, roleFilter, capabilityFilter, nameById]
  );
  const sorted = useMemo(() => {
    const parsed = parseDrfOrdering(ordering);
    if (!parsed) return filtered;
    const key = parsed.field as SortKey;
    const dir = parsed.dir === "desc" ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const va = sortValue(a, key, nameById);
      const vb = sortValue(b, key, nameById);
      if (va < vb) return -dir;
      if (va > vb) return dir;
      return 0;
    });
  }, [filtered, ordering, nameById]);
  const currentPage = Math.min(page, Math.max(1, Math.ceil(sorted.length / pageSize)));
  const pageRows = sorted.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  const hasActiveFilters = roleFilter !== "all" || capabilityFilter !== "all" || !!search;

  const selectable = !!onRowSelectionChange;
  const columns = useMemo(() => {
    const base = getColumns({
      deletePendingId: deleteMutation.isPending ? deleteMutation.variables : null,
      hideCapabilityColumn,
      nameById,
      onDelete: (ds) => deleteMutation.mutate(ds.id),
      onDeleteClick: guard(() => undefined),
    });
    return selectable ? [ATTACH_COLUMN, ...base] : base;
  }, [deleteMutation, guard, hideCapabilityColumn, nameById, selectable]);

  return (
    <DataTable
      className={cn(fill && "min-h-0 flex-1")}
      columns={columns}
      data={{ count: sorted.length, results: pageRows }}
      emptyState={
        <EmptyState className="flex-1" icon={Icon.dataset} size="section" title="No datasets yet" />
      }
      enableRowSelection={selectable}
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
      onRetry={onRetry}
      onRowClick={(ds) =>
        navigate({
          params: { datasetId: ds.id },
          search: (prev) => ({ ...prev, ...(rowSearch ?? {}) }),
          to: "/datasets/$datasetId",
        })
      }
      onRowSelectionChange={onRowSelectionChange}
      ordering={ordering}
      page={currentPage}
      pageSize={pageSize}
      rowClassName={(row) => (highlights[`dataset:${row.id}`] ? "activity-highlight" : undefined)}
      rowSelection={rowSelection}
      storageKey="datasets-table"
    />
  );
}
