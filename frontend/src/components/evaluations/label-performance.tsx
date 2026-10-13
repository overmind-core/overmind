import { useEffect, useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";

import apiClient from "@/client";
import { ModelProviderChip } from "@/components/model-provider-chip";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDebouncedValue } from "@/hooks/use-debounced-value";
import { seriesColor } from "@/lib/colors";
import { TITLE } from "@/lib/typography";
import type { NativeLabelMetrics } from "@/openapi";

const metrics = {
  f1: {
    description: "Balances precision and recall for each label. Higher is better.",
    name: "F1",
  },
  precision: {
    description: "Of predictions of this label, how many were correct. Higher is better.",
    name: "Precision",
  },
  recall: {
    description: "Of scored examples with this label, how many were found. Higher is better.",
    name: "Recall",
  },
};

export function LabelPerformance({
  colorIndices,
  evaluation,
  suiteRole: role,
  names,
  stopped,
}: {
  colorIndices: Record<string, number>;
  evaluation: string;
  suiteRole: "final" | "calibration";
  names: Record<string, string>;
  stopped: boolean;
}) {
  const [metric, setMetric] = useState<keyof typeof metrics>("f1");
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search.trim(), 400);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [ordering, setOrdering] = useState<string | undefined>("label");
  const query = useQuery({
    queryFn: () =>
      apiClient.nativeEvaluations.nativeEvaluationsMonitorRetrieve({
        id: evaluation,
        limit: pageSize,
        offset: (page - 1) * pageSize,
        ordering,
        role,
        search: debouncedSearch || undefined,
        view: "labels",
      }),
    queryKey: [
      "native-labels",
      evaluation,
      role,
      page,
      pageSize,
      debouncedSearch,
      ordering,
      stopped,
    ],
    refetchInterval: (query) => (!stopped || query.state.data?.state === "pending" ? 5000 : false),
  });
  useEffect(() => {
    if (query.data && page > 1 && (page - 1) * pageSize >= query.data.count)
      setPage(Math.max(1, Math.ceil(query.data.count / pageSize)));
  }, [query.data, page, pageSize]);
  const columns = useMemo<ColumnDef<NativeLabelMetrics>[]>(
    () => [
      {
        cell: ({ row }) => (
          <span title={`${row.original.benchmark} · ${row.original.label}`}>
            {row.original.label}
          </span>
        ),
        header: "Label",
        id: "label",
        meta: { orderingField: "label" },
        size: 280,
      },
      {
        accessorKey: "benchmark",
        header: "Benchmark",
        id: "benchmark",
        meta: { orderingField: "benchmark" },
        size: 180,
      },
      {
        cell: ({ row }) => (
          <span className="font-mono tabular-nums" title="Single-answer references for this label">
            {row.original.expected.toLocaleString()}
          </span>
        ),
        header: "Examples",
        id: "expected",
        meta: { orderingField: "expected" },
        size: 100,
      },
      ...Object.entries(names).map(
        ([key, name]): ColumnDef<NativeLabelMetrics> => ({
          cell: ({ row }) => {
            const score = row.original.models[key];
            const value = score?.[metric];
            return (
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    className="flex w-full items-center gap-3 rounded-xs text-xs focus-visible:outline focus-visible:outline-ring"
                    type="button"
                  >
                    <span className="w-14 shrink-0 text-left font-mono tabular-nums">
                      {value == null ? "—" : `${(value * 100).toFixed(1)}%`}
                    </span>
                    <span aria-hidden className="h-1 min-w-8 flex-1 bg-muted">
                      {value != null && (
                        <span
                          className="block h-full"
                          style={{
                            backgroundColor: seriesColor(colorIndices[key]),
                            width: `${value * 100}%`,
                          }}
                        />
                      )}
                    </span>
                  </button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>
                    {name} · {metrics[metric].name}
                  </p>
                  <p>
                    {score?.support ?? 0} / {row.original.expected} reference examples scored
                  </p>
                  <p>
                    {score?.predicted ?? 0} predictions · {score?.correct ?? 0} correct
                  </p>
                  {value == null && (
                    <p>Not enough scored examples or predictions for this metric.</p>
                  )}
                </TooltipContent>
              </Tooltip>
            );
          },
          header: () => <ModelProviderChip compact model={name} />,
          id: key,
          meta: { label: name, orderingField: `${key}:${metric}` },
          size: 220,
        })
      ),
    ],
    [names, metric, colorIndices]
  );
  const clearSearch = () => {
    setSearch("");
    setPage(1);
  };
  return (
    <section aria-label="Per-label performance" className="min-w-0">
      <DataTable
        columns={columns}
        data={query.data ? { count: query.data.count, results: query.data.labels } : undefined}
        emptyState={
          <EmptyState
            description="Requires single-answer references and the same label options within each benchmark. Soft targets and missing outputs are not counted as incorrect."
            icon={Icon.analytics}
            size="section"
            title="No per-label results"
          />
        }
        error={query.error ?? (query.data?.error || undefined)}
        getRowId={(row) => JSON.stringify([row.benchmark, row.index])}
        hasActiveFilters={!!debouncedSearch}
        isLoading={query.isPending || query.data?.state === "pending"}
        onClearFilters={clearSearch}
        onOrderingChange={(next) => {
          setOrdering(next);
          setPage(1);
        }}
        onPageChange={setPage}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPage(1);
        }}
        ordering={ordering}
        page={page}
        pageSize={pageSize}
        storageKey="native-label-performance"
        toolbar={
          <>
            <div className="mr-auto min-w-0">
              <h3 className={TITLE.card}>Per-label performance</h3>
              <p className="mt-1 text-xs text-muted-foreground">
                {metrics[metric].description} Single-answer references only.
                {!stopped && " Results so far."}
              </p>
            </div>
            <SearchInput
              className="w-56 max-w-full"
              label="Search labels"
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(1);
              }}
              onClear={clearSearch}
              placeholder="Search labels…"
              size="sm"
              value={search}
            />
            <Select
              onValueChange={(value) => {
                setMetric(value as keyof typeof metrics);
                setOrdering("label");
                setPage(1);
              }}
              value={metric}
            >
              <SelectTrigger aria-label="Per-label metric" className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(metrics).map(([key, value]) => (
                  <SelectItem key={key} value={key}>
                    {value.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </>
        }
      />
    </section>
  );
}
