// The server pages over whole runs, so a row always arrives with its complete
// job set — that is what makes the per-run aggregates and the single-job test
// below correct at any page size.

import { type ReactNode, useEffect, useMemo, useState } from "react";

import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { FtStatusBadge } from "@/components/finetuning/finetuning-chrome";
import {
  groupPercent,
  groupRunName,
  groupStatusOf,
  isTerminalStatus,
  jobElapsedSeconds,
} from "@/components/finetuning/job-snapshot";
import { ModelOptionLabel } from "@/components/model-option-label";
import { getModelProviderInfo, ModelProviderChip } from "@/components/model-provider-chip";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CountChip } from "@/components/ui/count-chip";
import { CreditsAmount } from "@/components/ui/credits";
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
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import {
  useFinetuningRunBaseModelsQuery,
  useFinetuningRunDatasetsQuery,
  useFinetuningRunsQuery,
} from "@/hooks/use-finetuning";
import { groupCostUsd } from "@/lib/finetuning-cost";
import { formatElapsed } from "@/lib/formatters";
import { resolveStatus } from "@/lib/job-status";
import type { TrainingSearch } from "@/lib/schemas";
import { clearUnseenCompletion, useUnseenCompletionKeys } from "@/lib/unseen-job-completions";
import type { FinetuningJobList, FinetuningJobRun } from "@/openapi";
import { FinetuningJobsListStatusEnum } from "@/openapi";

function finetuningUnseenKeys(jobs: FinetuningJobList[]): string[] {
  return jobs.flatMap((j) => [`ft:${j.id}`, `model-swap:${j.id}`]);
}

const HISTORY_MODELS_VISIBLE = 2;

function HistoryModelsCell({ jobs }: { jobs: FinetuningJobList[] }) {
  const visible = jobs.slice(0, HISTORY_MODELS_VISIBLE);
  const overflow = jobs.length - visible.length;
  return (
    <div className="flex min-w-0 items-center gap-1.5 overflow-hidden whitespace-nowrap">
      {visible.map((j) => (
        // min-w-0: each chip truncates its own label instead of the second
        // being clipped at the column edge.
        <ModelProviderChip className="min-w-0" compact key={j.id} model={j.baseModel} />
      ))}
      {overflow > 0 && (
        <Badge
          className="h-6 shrink-0 px-1.5 font-mono leading-none tabular-nums"
          title={`${overflow} more model${overflow === 1 ? "" : "s"}`}
          variant="neutral"
        >
          +{overflow}
        </Badge>
      )}
    </div>
  );
}

const FT_STATUS_FILTER_ORDER: FinetuningJobsListStatusEnum[] = [
  "queued",
  "preparing",
  "running",
  "deploying",
  "succeeded",
  "failed",
  "cancelled",
];

/** Options come from the enum, not the loaded rows: an option with no rows is
 *  survivable, a row whose status has no option is not. Unknown statuses last. */
const FT_STATUS_OPTIONS = Object.values(FinetuningJobsListStatusEnum).sort((a, b) => {
  const rank = (s: FinetuningJobsListStatusEnum) => {
    const i = FT_STATUS_FILTER_ORDER.indexOf(s);
    return i < 0 ? FT_STATUS_FILTER_ORDER.length : i;
  };
  return rank(a) - rank(b);
});

type RunFilters = Pick<TrainingSearch, "ft_dataset" | "ft_model" | "ft_search" | "ft_status">;

interface JobsHistoryProps {
  projectId: string;
  action?: ReactNode;
  page: number;
  pageSize: number;
  filters: RunFilters;
  onSearchChange: (updates: Partial<RunFilters> & { page?: number; page_size?: number }) => void;
  onMonitorRun: (run: FinetuningJobRun) => void;
  onPeekJob: (jobId: string) => void;
}

export function JobsHistory({
  projectId,
  action,
  page,
  pageSize,
  filters,
  onSearchChange,
  onMonitorRun,
  onPeekJob,
}: JobsHistoryProps) {
  const {
    ft_dataset: datasetFilter,
    ft_model: modelFilter,
    ft_search: searchParam,
    ft_status: statusFilter,
  } = filters;
  // Local so typing doesn't write a URL entry per keystroke.
  const [search, setSearch] = useState(searchParam);
  const debouncedSearch = useDebouncedValue(search, 400);

  const unseenKeys = useUnseenCompletionKeys();
  const { data: datasetFacet = [] } = useFinetuningRunDatasetsQuery(projectId);
  const { data: modelFacet = [] } = useFinetuningRunBaseModelsQuery(projectId);

  const { data, isLoading, error, refetch, isRefetching } = useFinetuningRunsQuery({
    baseModel: modelFilter !== "all" ? modelFilter : undefined,
    dataset: datasetFilter !== "all" ? datasetFilter : undefined,
    page,
    pageSize,
    projectId,
    search: searchParam,
    status: statusFilter !== "all" ? statusFilter : undefined,
  });

  // The guard matters: the debounce seeds with the incoming value, so an
  // unguarded effect would reset a deep-linked `?page=` on mount.
  useEffect(() => {
    if (debouncedSearch !== searchParam) onSearchChange({ ft_search: debouncedSearch, page: 1 });
  }, [debouncedSearch, searchParam, onSearchChange]);

  // A URL filter the facet doesn't carry (stale link, deleted dataset) still
  // filters the query, so it has to stay selectable or Clear is out of reach.
  const datasetOptions = useMemo(() => {
    const rows = datasetFacet.map((d) => ({
      id: d.id,
      name: d.name?.trim() || `Dataset ${d.id.slice(0, 8)}`,
    }));
    if (datasetFilter !== "all" && !rows.some((d) => d.id === datasetFilter))
      rows.push({ id: datasetFilter, name: "Unknown dataset" });
    return rows;
  }, [datasetFacet, datasetFilter]);

  // Facet only, never `datasetOptions`: its synthetic "Unknown dataset" entry
  // would print as a row's dataset name.
  const datasetNameById = useMemo(
    () => new Map(datasetFacet.map((d) => [d.id, d.name])),
    [datasetFacet]
  );

  const modelOptions = useMemo(() => {
    const rows = modelFacet.map((m) => ({
      label: getModelProviderInfo(m).modelLabel || m,
      model: m,
    }));
    if (modelFilter !== "all" && !rows.some((m) => m.model === modelFilter))
      rows.push({
        label: getModelProviderInfo(modelFilter).modelLabel || modelFilter,
        model: modelFilter,
      });
    return rows.sort((a, b) => a.label.localeCompare(b.label));
  }, [modelFacet, modelFilter]);

  const hasActiveFilters =
    statusFilter !== "all" ||
    datasetFilter !== "all" ||
    modelFilter !== "all" ||
    search.trim() !== "";

  const clearFilters = () => {
    setSearch("");
    onSearchChange({
      ft_dataset: "all",
      ft_model: "all",
      ft_search: "",
      ft_status: "all",
      page: 1,
    });
  };

  // No `meta.orderingField`: the endpoint accepts `?ordering=` but always
  // returns runs newest-first, so a sortable header would do nothing.
  const columns: ColumnDef<FinetuningJobRun>[] = [
    {
      cell: ({ row }) => {
        const status = groupStatusOf(row.original.jobs);
        const percent = isTerminalStatus(status) ? null : groupPercent(row.original.jobs);
        return (
          <FtStatusBadge
            className="min-w-[7rem] justify-center"
            fallback="queued"
            progress={status === "running" ? percent : null}
            status={status}
          />
        );
      },
      header: "Status",
      id: "status",
      meta: { noTruncate: true },
      minSize: 140,
      size: 160,
    },
    {
      cell: ({ row }) => {
        const run = row.original;
        const runName = groupRunName(run.jobs);
        return (
          <span className="inline-flex max-w-full items-center gap-1.5 truncate font-medium">
            <span className="truncate" title={runName || undefined}>
              {runName || (
                <span className="font-mono text-muted-foreground">{run.runId.slice(0, 8)}</span>
              )}
            </span>
            {run.jobs.length > 1 && <CountChip className="shrink-0" count={run.jobs.length} />}
          </span>
        );
      },
      header: "Run",
      id: "run",
      meta: { noTruncate: true },
      minSize: 160,
      size: 240,
    },
    {
      cell: ({ row }) => <HistoryModelsCell jobs={row.original.jobs} />,
      header: "Models",
      id: "models",
      meta: { noTruncate: true },
      minSize: 180,
      size: 240,
    },
    {
      cell: ({ row }) => {
        const datasetId = row.original.jobs[0].dataset;
        return (
          <EntityRef
            id={datasetId}
            kind="dataset"
            name={datasetNameById.get(datasetId)}
            projectId={projectId}
          />
        );
      },
      header: "Dataset",
      id: "dataset",
      meta: { noTruncate: true },
      minSize: 120,
      size: 144,
    },
    {
      cell: ({ row }) => {
        const secs = row.original.jobs.reduce<number | null>((max, j) => {
          const elapsed = jobElapsedSeconds(j);
          return elapsed != null && elapsed > (max ?? 0) ? elapsed : max;
        }, null);
        return (
          <span className="tabular-nums text-muted-foreground">
            {secs != null ? formatElapsed(secs) : "—"}
          </span>
        );
      },
      header: "Duration",
      id: "duration",
      minSize: 90,
      size: 110,
    },
    {
      cell: ({ row }) => {
        const cost = groupCostUsd(row.original.jobs);
        return (
          <span className="tabular-nums text-muted-foreground">
            {cost != null ? <CreditsAmount usd={cost} /> : "—"}
          </span>
        );
      },
      header: "Credits",
      id: "credits",
      minSize: 90,
      size: 110,
    },
    {
      cell: ({ row }) => {
        const startedAt = row.original.jobs.reduce(
          (min, j) => (j.createdAt < min ? j.createdAt : min),
          row.original.jobs[0].createdAt
        );
        return (
          <span className="text-muted-foreground">
            <DateTime value={startedAt} />
          </span>
        );
      },
      header: "Started",
      id: "started",
      minSize: 120,
      size: 140,
    },
    {
      cell: ({ row }) => {
        const run = row.original;
        const single = run.jobs.length === 1 ? run.jobs[0] : null;
        if (!single) return <span className="text-muted-foreground">—</span>;
        return (
          <Button
            aria-label={`Quick peek ${groupRunName(run.jobs) || single.baseModel}`}
            onClick={(e) => {
              e.stopPropagation();
              onPeekJob(single.id);
            }}
            size="icon-xs"
            title="Quick peek"
            variant="ghost"
          >
            <Icon.search />
          </Button>
        );
      },
      enableResizing: false,
      header: "Peek",
      id: "peek",
      size: 64,
    },
  ];

  const toolbar = (
    <ListToolbar
      actions={
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-label="Refresh job history"
              disabled={isRefetching}
              onClick={() => refetch()}
              size="icon"
              variant="secondary"
            >
              {isRefetching ? <Spinner size="sm" /> : <Icon.refresh />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>Refresh job history</TooltipContent>
        </Tooltip>
      }
      filters={
        <>
          <Select
            // Same vocabulary the schema's closed set is built from, so the cast holds.
            onValueChange={(v) =>
              onSearchChange({ ft_status: v as RunFilters["ft_status"], page: 1 })
            }
            value={statusFilter}
          >
            <SelectTrigger aria-label="Filter by status" className="w-[140px]" size="default">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              {FT_STATUS_OPTIONS.map((s) => (
                <SelectItem key={s} value={s}>
                  {resolveStatus(s, "queued").label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            onValueChange={(v) => onSearchChange({ ft_dataset: v, page: 1 })}
            value={datasetFilter}
          >
            <SelectTrigger aria-label="Filter by dataset" className="w-[180px]" size="default">
              <SelectValue placeholder="Dataset" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All datasets</SelectItem>
              {datasetOptions.map((d) => (
                <SelectItem key={d.id} value={d.id}>
                  {d.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            onValueChange={(v) => onSearchChange({ ft_model: v, page: 1 })}
            value={modelFilter}
          >
            <SelectTrigger aria-label="Filter by model" className="w-[180px]" size="default">
              <SelectValue placeholder="Model" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All models</SelectItem>
              {modelOptions.map((m) => (
                <SelectItem key={m.model} textValue={m.label} value={m.model}>
                  <ModelOptionLabel model={m.model} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </>
      }
      hasActiveFilters={hasActiveFilters}
      onClearFilters={clearFilters}
      primary={action}
      search={
        <SearchInput
          className="min-w-[200px] flex-1"
          label="Search runs"
          onChange={(e) => setSearch(e.target.value)}
          onClear={() => {
            setSearch("");
            onSearchChange({ ft_search: "", page: 1 });
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
          description="Runs appear here once you train a model."
          icon={Icon.ai}
          size="section"
          title="No training runs yet"
        />
      }
      error={error}
      getRowId={(run) => run.runId}
      hasActiveFilters={hasActiveFilters}
      isLoading={isLoading}
      onClearFilters={clearFilters}
      onPageChange={(p) => onSearchChange({ page: p })}
      onPageSizeChange={(s) => onSearchChange({ page: 1, page_size: s })}
      onRowClick={onMonitorRun}
      onRowMouseEnter={(run) => {
        for (const key of finetuningUnseenKeys(run.jobs)) clearUnseenCompletion(key);
      }}
      page={page}
      pageSize={pageSize}
      rowClassName={(run) =>
        finetuningUnseenKeys(run.jobs).some((key) => unseenKeys.has(key))
          ? "bg-wash-subtle"
          : undefined
      }
      storageKey="training:runs-table-cols"
      toolbar={toolbar}
    />
  );
}
