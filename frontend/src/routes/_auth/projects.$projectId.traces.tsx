import { useCallback, useEffect, useMemo, useState } from "react";

import { createFileRoute, Link, Outlet, useMatches } from "@tanstack/react-router";
import type { Table as TableType } from "@tanstack/react-table";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
  type VisibilityState,
} from "@tanstack/react-table";
import { Cog } from "lucide-react";
import { toast } from "sonner";

import {
  filtersToBackendQuery,
  parseFiltersFromSearchParams,
  serializeFiltersToSearchParams,
} from "@/components/traces/filters";
import { tracesColumns } from "@/components/traces/traces-columns";

import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

function getTraceAttr(attrs: Record<string, unknown> | undefined, ...keys: string[]): unknown {
  if (!attrs) return undefined;
  for (const k of keys) {
    const v = attrs[k];
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

import apiClient from "@/client";
import { APIKeySection } from "@/components/api-keys";
import { TracesTablePagination } from "@/components/traces/traces-table-pagination";
import { TracesTableToolbar } from "@/components/traces/traces-table-toolbar";
import { spanStatusLabel, transformSpan, type SpanRow } from "@/hooks/use-traces";
import { getTimeRangeStartTimestamp } from "@/lib/formatters";
import { tracesSearchSchema } from "@/lib/schemas";
import { useQuery } from "@tanstack/react-query";

export const Route = createFileRoute("/_auth/projects/$projectId/traces")({
  component: TracesPage,
  validateSearch: tracesSearchSchema,
});

function TracesPage() {
  const { projectId } = Route.useParams();
  const navigate = Route.useNavigate();
  const matches = useMatches();
  const traceMatch = matches.find(
    (m) => m.routeId === "/_auth/projects/$projectId/traces/$traceId"
  );
  const traceId = traceMatch?.params?.traceId as string | undefined;

  const searchParams = Route.useSearch();
  const {
    flatten,
    timeRange,
    q,
    sortBy,
    sortDirection,
    page,
    pageSize,
    status: statusFilter,
    detailExpanded,
    promptSlug,
    promptVersion,
  } = searchParams;

  const [searchInput, setSearchInput] = useState(q ?? "");
  const advancedFilters = useMemo(
    () =>
      parseFiltersFromSearchParams(searchParams as unknown as Record<string, string | undefined>),
    [searchParams]
  );

  const offset = (page - 1) * pageSize;

  // Build server-side filter strings from all active filter state
  const serverFilter = useMemo(
    () =>
      filtersToBackendQuery(advancedFilters, {
        q: q || undefined,
        status: statusFilter !== "all" ? statusFilter : undefined,
      }),
    [advancedFilters, q, statusFilter]
  );

  const { data, isLoading, error } = useQuery({
    queryFn: async () => {
      const data = await apiClient.traces.listTracesApiV1TracesListGet({
        query: serverFilter.length > 0 ? serverFilter : undefined,
        limit: pageSize,
        offset,
        projectId,
        promptSlug,
        promptVersion,
        rootOnly: !flatten,
        startTimestamp: new Date(getTimeRangeStartTimestamp(timeRange)),
      });
      return {
        ...data,
        traces: data.traces.map(transformSpan),
      };
    },
    queryKey: [
      "traces",
      projectId,
      timeRange,
      pageSize,
      offset,
      promptSlug,
      promptVersion,
      serverFilter,
      flatten,
    ],
  });

  const setSearch = useCallback(
    (updates: Partial<typeof searchParams>) => {
      navigate({
        params: { projectId },
        search: { ...searchParams, ...updates },
        to: "/projects/$projectId/traces",
      });
    },
    [projectId, navigate, searchParams]
  );

  useEffect(() => {
    const urlQuery = q ?? "";
    if (searchInput === urlQuery) return;
    const timer = setTimeout(() => {
      setSearch({ page: 1, q: searchInput || undefined });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput, q, setSearch]);

  useEffect(() => {
    setSearchInput(q ?? "");
  }, [q]);

  // Filtering is now entirely server-side; client only sorts the current page
  const filteredAndSorted = useMemo(() => {
    if (!data?.traces) return [];

    const result = [...data.traces].sort((a: SpanRow, b: SpanRow) => {
      let cmp = 0;
      switch (sortBy) {
        case "name":
          cmp = (a.scopeName ?? a.name ?? "").localeCompare(b.scopeName ?? b.name ?? "");
          break;
        case "timestamp":
          cmp = (a.startTimeUnixNano ?? 0) - (b.startTimeUnixNano ?? 0);
          break;
        case "duration":
          cmp = (a.durationNano ?? 0) - (b.durationNano ?? 0);
          break;
        case "status":
          cmp = spanStatusLabel(a.statusCode).localeCompare(spanStatusLabel(b.statusCode));
          break;
        case "trace_id":
          cmp = (a.traceId ?? "").localeCompare(b.traceId ?? "");
          break;
        case "status_message": {
          const aMsg = String(a.spanAttributes?.status_message ?? "");
          const bMsg = String(b.spanAttributes?.status_message ?? "");
          cmp = aMsg.localeCompare(bMsg);
          break;
        }
        case "model": {
          const aModel =
            getTraceAttr(a.spanAttributes, "gen_ai.request.model", "gen_ai.response.model") ??
            getTraceAttr(a.resourceAttributes, "gen_ai.request.model", "gen_ai.response.model") ??
            "";
          const bModel =
            getTraceAttr(b.spanAttributes, "gen_ai.request.model", "gen_ai.response.model") ??
            getTraceAttr(b.resourceAttributes, "gen_ai.request.model", "gen_ai.response.model") ??
            "";
          cmp = String(aModel).localeCompare(String(bModel));
          break;
        }
        case "tokens": {
          const aTotal = getTraceAttr(a.spanAttributes, "llm.usage.total_tokens") as
            | number
            | undefined;
          const bTotal = getTraceAttr(b.spanAttributes, "llm.usage.total_tokens") as
            | number
            | undefined;
          cmp = (aTotal ?? 0) - (bTotal ?? 0);
          break;
        }
        case "cost": {
          cmp = (a.cost ?? 0) - (b.cost ?? 0);
          break;
        }
        default:
          cmp = (a.startTimeUnixNano ?? 0) - (b.startTimeUnixNano ?? 0);
      }
      return sortDirection === "desc" ? -cmp : cmp;
    });
    return result;
  }, [data, sortBy, sortDirection]);

  const totalOnPage = filteredAndSorted.length;
  const showPagination = totalOnPage > 0 || page > 1;

  const handleDrawerClose = (open: boolean) => {
    if (!open)
      navigate({
        params: { projectId },
        search: { ...searchParams },
        to: "/projects/$projectId/traces",
      });
  };

  const [rowSelection, setRowSelection] = useState({});
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({
    prompt: false,
    status_message: false,
    system: false,
    trace_id: false,
  });

  const sorting: SortingState = useMemo(
    () => [{ desc: sortDirection === "desc", id: sortBy ?? "timestamp" }],
    [sortBy, sortDirection]
  );

  const table = useReactTable({
    columns: tracesColumns,
    data: filteredAndSorted,
    enableRowSelection: true,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: (row) => row.traceId,
    getSortedRowModel: getSortedRowModel(),
    manualSorting: true,
    onColumnVisibilityChange: setColumnVisibility,
    onRowSelectionChange: setRowSelection,
    onSortingChange: (updater) => {
      const next = typeof updater === "function" ? updater(sorting) : sorting;
      const col = next[0];
      if (col)
        setSearch({
          page: 1,
          sortBy: col.id as
            | "timestamp"
            | "name"
            | "duration"
            | "status"
            | "trace_id"
            | "status_message"
            | "model"
            | "tokens"
            | "cost",
          sortDirection: col.desc ? "desc" : "asc",
        });
    },
    state: {
      columnVisibility,
      rowSelection,
      sorting,
    },
  });

  return (
    <div className="page-wrapper">
      <PageHeader />
      <TracesTableToolbar
        filters={advancedFilters}
        onFiltersChange={(filters) =>
          setSearch({ ...serializeFiltersToSearchParams(filters), page: 1 })
        }
        onPageSizeChange={(v) => setSearch({ page: 1, pageSize: v })}
        onSearchChange={setSearchInput}
        onStatusChange={(v) => setSearch({ page: 1, status: v as "all" | "success" | "error" })}
        onTimeRangeChange={(v) =>
          setSearch({ page: 1, timeRange: v as "all" | "past24h" | "past7d" | "past30d" })
        }
        pageSize={pageSize}
        projectId={projectId}
        searchValue={searchInput}
        status={statusFilter}
        table={table}
        timeRange={timeRange}
      />

      <div className="page-content">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden rounded-md border border-border">
          {isLoading && (
            <div className="space-y-2 p-4">
              {[1, 2, 3, 4, 5].map((i) => (
                <Skeleton className="h-12 w-full" key={i} />
              ))}
            </div>
          )}
          {error && (
            <Alert className="m-4" variant="destructive">
              Failed to load traces: {(error as Error).message}
            </Alert>
          )}
          {!isLoading && !projectId && (
            <div className="flex flex-1 items-center justify-center py-12 text-center text-muted-foreground">
              No project selected. Select a project to view traces.
            </div>
          )}
          {!isLoading && projectId && data?.traces.length === 0 && (
            <div className="flex flex-1 items-center justify-center py-12 text-center text-muted-foreground">
              No traces found, adjust your filters or set up your API key to start tracing your AI
              agent with Overmind.
            </div>
          )}
          {!isLoading && filteredAndSorted.length > 0 && <RenderTable table={table} />}
        </div>
        {!isLoading && projectId && data?.traces.length === 0 && (
          <>
            <div className="h-4" />
            <APIKeySection projectId={projectId} />
          </>
        )}

        {showPagination && (
          <TracesTablePagination
            onPageChange={(p) => setSearch({ page: p })}
            onPageSizeChange={(s) => setSearch({ page: 1, pageSize: s })}
            page={page}
            pageSize={pageSize}
            count={data?.count ?? 0}
          />
        )}
      </div>

      <Sheet modal={false} onOpenChange={handleDrawerClose} open={!!traceId}>
        <SheetContent
          className={`flex w-full flex-col overflow-hidden border-l ${detailExpanded ? "sm:max-w-[90vw]" : "sm:max-w-[70vw]"}`}
          showCloseButton={false}
          showOverlay={false}
          side="right"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <div className="-m-4 flex flex-1 flex-col overflow-y-auto p-8">
            <Outlet />
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
const PageHeader = () => {
  const { projectId } = Route.useParams();
  return (
    <div className="shrink-0 flex items-center flex-row justify-between gap-2">
      <h1 className="text-xl font-bold">Traces</h1>
      <Link params={{ projectId }} to="/projects/$projectId">
        <Button size="icon" variant="ghost">
          <Cog className="size-4" />
        </Button>
      </Link>
    </div>
  );
};

const TRACES_SELECTION_TOAST_ID = "traces-row-selection";

const RenderTable = ({ table }: { table: TableType<SpanRow> }) => {
  const navigate = Route.useNavigate();
  const { projectId } = Route.useParams();
  const search = Route.useSearch();
  const selectedCount = table.getFilteredSelectedRowModel().rows.length;

  useEffect(() => {
    if (selectedCount > 0) {
      toast(`${selectedCount} row(s) selected`, {
        id: TRACES_SELECTION_TOAST_ID,
      });
    } else {
      toast.dismiss(TRACES_SELECTION_TOAST_ID);
    }
  }, [selectedCount]);

  const handleTraceClick = (id: string) => {
    navigate({
      params: { projectId, traceId: id },
      search: { ...search },
      to: "/projects/$projectId/traces/$traceId",
      resetScroll: false,
    });
  };
  return (
    <div className="min-h-0 flex-1 overflow-x-auto overflow-y-auto">
      <Table className="min-w-max">
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id}>
              {headerGroup.headers.map((header) => (
                <TableHead key={header.id}>
                  {header.isPlaceholder
                    ? null
                    : flexRender(header.column.columnDef.header, header.getContext())}
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow
              className="cursor-pointer hover:bg-muted/50"
              data-state={row.getIsSelected() ? "selected" : undefined}
              key={row.original.spanId}
              onClick={() => handleTraceClick(row.original.traceId ?? "")}
            >
              {row.getVisibleCells().map((cell) => (
                <TableCell
                  key={cell.id}
                  onClick={(e) => (cell.column.id === "select" ? e.stopPropagation() : undefined)}
                >
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
};
