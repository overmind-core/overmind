import { type ReactNode, useEffect, useMemo, useState } from "react";

import { useNavigate } from "@tanstack/react-router";
import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import {
  EXPERIMENT_STATUS_LABELS,
  ExperimentStatusChip,
  experimentScores,
  OptimizerRunTypeBadge,
  optimizerModelIds,
  optimizerRunType,
  optimizerRunTypeMeta,
  prettyStatus,
} from "@/components/optimiser/experiment-status";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { DeltaChip } from "@/components/ui/delta-chip";
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
import { useOptimizerExperimentsQuery } from "@/hooks/use-optimizer";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { parseDrfOrdering } from "@/lib/drf-ordering";
import type { OptimiserSearch } from "@/lib/schemas";
import { clearUnseenCompletion, useUnseenCompletionKeys } from "@/lib/unseen-job-completions";
import type { OptimizerExperiment } from "@/openapi";
import { OptimizerExperimentStatusEnum } from "@/openapi";

type SortKey = "capability" | "status" | "dataset" | "score" | "created";

function ScoreCell({
  baseline,
  best,
  delta,
}: {
  baseline: number | null;
  best: number | null;
  delta: number | null;
}) {
  if (best == null && baseline == null) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="inline-flex items-center gap-1.5 tabular-nums text-foreground">
      <span>{best != null ? best.toFixed(1) : "—"}</span>
      {delta != null && <DeltaChip value={delta} />}
    </span>
  );
}

type ExperimentFilters = Pick<OptimiserSearch, "run_capability" | "run_search" | "run_status">;

interface ExperimentsTableProps {
  projectId: string;
  action?: ReactNode;
  page: number;
  pageSize: number;
  filters: ExperimentFilters;
  onSearchChange: (
    updates: Partial<ExperimentFilters> & { page?: number; page_size?: number }
  ) => void;
}

export function ExperimentsTable({
  projectId,
  action,
  page,
  pageSize,
  filters,
  onSearchChange,
}: ExperimentsTableProps) {
  const navigate = useNavigate();
  const unseenKeys = useUnseenCompletionKeys();
  const { data, isFetched, error } = useOptimizerExperimentsQuery(projectId);
  const {
    run_capability: capabilityFilter,
    run_search: searchParam,
    run_status: statusFilter,
  } = filters;
  // Local so typing doesn't write a URL entry per keystroke.
  const [search, setSearch] = useState(searchParam);
  const debouncedSearch = useDebouncedValue(search, 400);
  // Client-side sort, encoded as a DRF ordering string for DataTable headers.
  const [ordering, setOrdering] = usePersistedState<string>(
    "optimiser:experimentOrdering",
    "-created"
  );

  const allExperiments = data?.results ?? [];

  const resetToFirstPage = () => {
    if (page !== 1) onSearchChange({ page: 1 });
  };

  // The guard matters: the debounce seeds with the incoming value, so an
  // unguarded effect would reset a deep-linked `?page=` on mount.
  useEffect(() => {
    if (debouncedSearch !== searchParam) onSearchChange({ page: 1, run_search: debouncedSearch });
  }, [debouncedSearch, searchParam, onSearchChange]);

  // Keyed on the capability id, not the name: two capabilities can share a display name, and
  // a nameless one still has to be selectable.
  const capabilityOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const e of allExperiments) {
      if (e.capability) byId.set(e.capability, e.capabilityName || "Unnamed capability");
    }
    // A `run_capability` no experiment carries (stale link, deleted capability) would
    // leave the trigger blank and the empty table unexplained.
    if (capabilityFilter !== "all" && !byId.has(capabilityFilter))
      byId.set(capabilityFilter, "Unknown capability");
    return [...byId]
      .map(([id, name]) => ({ id, name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [allExperiments, capabilityFilter]);

  const hasActiveFilters =
    statusFilter !== "all" || capabilityFilter !== "all" || search.trim() !== "";

  const experiments = useMemo(() => {
    const q = searchParam.trim().toLowerCase();
    const filtered = allExperiments.filter((e) => {
      if (statusFilter !== "all" && e.status !== statusFilter) return false;
      if (capabilityFilter !== "all" && e.capability !== capabilityFilter) return false;
      if (q) {
        const runType = optimizerRunType(e);
        const haystack =
          `${e.capabilityName} ${e.datasetName} ${e.evalSetName} ${e.status} ${optimizerRunTypeMeta(runType).label} ${optimizerRunTypeMeta(runType).description} ${optimizerModelIds(e).join(" ")}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });

    const sort = parseDrfOrdering(ordering || undefined);
    const requestedKey = sort?.field;
    const key: SortKey =
      requestedKey === "capability" ||
      requestedKey === "status" ||
      requestedKey === "dataset" ||
      requestedKey === "score" ||
      requestedKey === "created"
        ? requestedKey
        : "created";
    const dir = sort?.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (key) {
        case "capability":
          cmp = a.capabilityName.localeCompare(b.capabilityName);
          break;
        case "status":
          cmp = a.status.localeCompare(b.status);
          break;
        case "dataset":
          cmp = (a.datasetName || "").localeCompare(b.datasetName || "");
          break;
        case "score": {
          const aBest = experimentScores(a).best ?? -Infinity;
          const bBest = experimentScores(b).best ?? -Infinity;
          cmp = aBest - bBest;
          break;
        }
        case "created":
          cmp = new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime();
          break;
      }
      return cmp * dir;
    });
  }, [allExperiments, searchParam, statusFilter, capabilityFilter, ordering]);

  const totalPages = Math.max(1, Math.ceil(experiments.length / pageSize));
  const safePage = Math.min(page, totalPages);
  // Clamping waits for the fetch: an empty first render would otherwise rewrite
  // a deep-linked `?page=` to 1.
  useEffect(() => {
    if (isFetched && page !== safePage) onSearchChange({ page: safePage });
  }, [isFetched, page, safePage, onSearchChange]);
  const pageRows = experiments.slice((safePage - 1) * pageSize, safePage * pageSize);

  const clearFilters = () => {
    setSearch("");
    onSearchChange({ page: 1, run_capability: "all", run_search: "", run_status: "all" });
  };

  const columns: ColumnDef<OptimizerExperiment>[] = [
    {
      accessorKey: "capabilityName",
      cell: ({ row }) => {
        const e = row.original;
        return e.capability ? (
          <EntityRef id={e.capability} kind="capability" name={e.capabilityName} />
        ) : (
          <span className="text-muted-foreground">—</span>
        );
      },
      header: "Capability",
      meta: { noTruncate: true, orderingField: "capability" },
      minSize: 140,
      size: 200,
    },
    {
      cell: ({ row }) => (
        <OptimizerRunTypeBadge experiment={row.original} showModelCount size="chip" />
      ),
      header: "Run type",
      id: "mode",
      meta: { noTruncate: true },
      minSize: 130,
      size: 148,
    },
    {
      accessorKey: "status",
      cell: ({ row }) => {
        const experiment = row.original;
        const progress =
          experiment.status === "iterating" && experiment.numIterations > 0
            ? (experiment.currentIteration / experiment.numIterations) * 100
            : null;
        return <ExperimentStatusChip progress={progress} status={experiment.status} />;
      },
      header: "Status",
      meta: { noTruncate: true, orderingField: "status" },
      minSize: 120,
      size: 140,
    },
    {
      accessorKey: "datasetName",
      cell: ({ row }) => {
        const e = row.original;
        return e.dataset ? (
          <EntityRef id={e.dataset} kind="dataset" name={e.datasetName} projectId={projectId} />
        ) : (
          <span className="text-muted-foreground">—</span>
        );
      },
      header: "Dataset",
      meta: { noTruncate: true, orderingField: "dataset" },
      minSize: 120,
      size: 144,
    },
    {
      cell: ({ row }) => {
        const { baseline, best, delta } = experimentScores(row.original);
        return <ScoreCell baseline={baseline} best={best} delta={delta} />;
      },
      header: "Best score",
      id: "score",
      meta: { noTruncate: true, orderingField: "score" },
      minSize: 110,
      size: 130,
    },
    {
      accessorKey: "createdAt",
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          <DateTime value={row.original.createdAt} />
        </span>
      ),
      header: "Created",
      meta: { orderingField: "created" },
      minSize: 120,
      size: 136,
    },
  ];

  const toolbar = (
    <ListToolbar
      filters={
        <>
          <Select
            onValueChange={(v) => onSearchChange({ page: 1, run_status: v })}
            value={statusFilter}
          >
            <SelectTrigger aria-label="Filter by status" className="w-[180px]" size="default">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {Object.values(OptimizerExperimentStatusEnum).map((s) => (
                <SelectItem key={s} value={s}>
                  {EXPERIMENT_STATUS_LABELS[s] ?? prettyStatus(s)}
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
        </>
      }
      hasActiveFilters={hasActiveFilters}
      onClearFilters={clearFilters}
      primary={action}
      search={
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search experiments"
          onChange={(e) => setSearch(e.target.value)}
          onClear={() => {
            setSearch("");
            onSearchChange({ page: 1, run_search: "" });
          }}
          placeholder="Search experiments…"
          value={search}
        />
      }
    />
  );

  return (
    <DataTable
      columns={columns}
      data={{ count: experiments.length, results: pageRows }}
      emptyState={
        <EmptyState
          className="flex-1"
          description="Runs appear after /overmind optimise or /overmind backtest."
          icon={Icon.optimiser}
          size="section"
          title="No runs yet"
        />
      }
      error={error}
      getRowId={(row) => row.id}
      hasActiveFilters={hasActiveFilters}
      // `data` is synthesized client-side here, so `unsettled` can't infer
      // settledness from it — pass the query's own `isFetched`.
      isLoading={!isFetched}
      onClearFilters={clearFilters}
      onOrderingChange={(o) => {
        setOrdering(o ?? "");
        resetToFirstPage();
      }}
      onPageChange={(p) => onSearchChange({ page: p })}
      onPageSizeChange={(s) => onSearchChange({ page: 1, page_size: s })}
      // Carry the whole search, not just `projectId` — see the runs table.
      onRowClick={(e) =>
        navigate({
          params: { experimentId: e.id },
          resetScroll: false,
          search: (prev) => prev,
          to: "/optimiser/$experimentId",
        })
      }
      onRowMouseEnter={(e) => clearUnseenCompletion(`optimizer:${e.id}`)}
      ordering={ordering || undefined}
      page={safePage}
      pageSize={pageSize}
      rowClassName={(e) => (unseenKeys.has(`optimizer:${e.id}`) ? "bg-wash-subtle" : undefined)}
      storageKey="optimiser:experiments-cols"
      toolbar={toolbar}
    />
  );
}
