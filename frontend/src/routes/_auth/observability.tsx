// Filter params follow django-filter conventions (`field` eq, `field__lookup`
// otherwise), so the URL doubles as the DRF query.

import { useCallback, useEffect, useMemo, useState } from "react";

import { createFileRoute, Link, Outlet, useMatches } from "@tanstack/react-router";
import type { OnChangeFn, RowSelectionState, VisibilityState } from "@tanstack/react-table";

import { NewDatasetDialog } from "@/components/datasets/new-dataset-dialog";
import { ObservabilitySetupDialog } from "@/components/observability-setup-dialog";
import { ProjectRequiredEmptyState } from "@/components/project-required-empty-state";
import {
  parseFiltersFromSearchParams,
  serializeFiltersToSearchParams,
  useRecentFilterParams,
} from "@/components/traces/filters";
import { sessionsColumns } from "@/components/traces/sessions-columns";
import { SignalEvents } from "@/components/traces/signal-events";
import { TraceSelectionBar } from "@/components/traces/trace-selection-bar";
import { tracesColumns } from "@/components/traces/traces-columns";
import {
  resolveTimeRange,
  type TimeRangePreset,
  TracesTableToolbar,
  type TracesViewMode,
} from "@/components/traces/traces-table-toolbar";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { PageHeader } from "@/components/ui/page-header";
import { PageShell } from "@/components/ui/page-shell";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { TraceSelectionSpec } from "@/hooks/use-datasets";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { type SessionRow, useSessionDetail, useSessionsList } from "@/hooks/use-sessions";
import {
  type TraceRef,
  type TraceRow,
  UNBOUND_CAPABILITY,
  useTracesList,
} from "@/hooks/use-traces";
import { featureFlags } from "@/lib/feature-flags";
import { tracesSearchSchema } from "@/lib/schemas";

const DEFAULT_TRACES_ORDERING = "-start_time_ns";
const DEFAULT_SESSIONS_ORDERING = "-last_span_ns";

// The shared `ordering` param can carry trace fields this view rejects.
const SESSION_ORDERING_FIELDS = new Set([
  "capability",
  "capability__name",
  "created_at",
  "external_id",
  "first_span_ns",
  "last_span_ns",
  "model",
  "name",
  "span_count",
  "timespan",
  "total_cost",
  "total_tokens",
  "trace_count",
]);

// A cleared sort lands back on the view default; clearing the default column
// flips its direction instead, so the click is never a no-op.
function nextOrdering(next: string | undefined, current: string, fallback: string): string {
  if (next) return next;
  if (current !== fallback) return fallback;
  return fallback.startsWith("-") ? fallback.slice(1) : `-${fallback}`;
}

export const Route = createFileRoute("/_auth/observability")({
  component: TracesPage,
  validateSearch: tracesSearchSchema,
});

function TracesPage() {
  const navigate = Route.useNavigate();
  const matches = useMatches();
  const traceMatch = matches.find((m) => m.routeId === "/_auth/observability/$traceId");
  const traceId = traceMatch?.params?.traceId as string | undefined;

  const search = Route.useSearch();
  const {
    projectId,
    page,
    page_size: pageSize,
    ordering,
    search: searchTerm,
    timeRange,
    detailExpanded,
    error__isnull,
    view,
    session,
    signal_event: signalEvent,
  } = search;
  const isSessionsView = view === "sessions";
  const isSignalsView = featureFlags.signals && view === "signals";
  const tracesView: TracesViewMode = isSessionsView ? "sessions" : "roots";

  const status: "all" | "success" | "error" =
    error__isnull === "true" ? "error" : error__isnull === "false" ? "success" : "all";

  const advancedFilters = useMemo(
    () => parseFiltersFromSearchParams(search as unknown as Record<string, string | undefined>),
    [search]
  );

  // Separates "no matches" from "no data at all", so the empty state can point
  // at the filters rather than at SDK setup.
  const hasActiveFilters =
    advancedFilters.length > 0 ||
    !!searchTerm ||
    timeRange !== "all" ||
    status !== "all" ||
    !!session ||
    (featureFlags.signals && !!signalEvent);

  const [searchInput, setSearchInput] = useState(searchTerm ?? "");

  const setSearch = useCallback(
    (updates: Record<string, string | number | boolean | undefined>) => {
      navigate({
        search: { ...search, ...updates, projectId: projectId! } as never,
        to: "/observability",
      });
    },
    [projectId, navigate, search]
  );

  useEffect(() => {
    const urlQuery = searchTerm ?? "";
    if (searchInput === urlQuery) return;
    const t = setTimeout(() => {
      setSearch({ page: 1, search: searchInput || undefined });
    }, 400);
    return () => clearTimeout(t);
  }, [searchInput, searchTerm, setSearch]);

  useEffect(() => {
    setSearchInput(searchTerm ?? "");
  }, [searchTerm]);

  const customTimeRange = useMemo(
    () => ({ gte: search.received_at__gte, lte: search.received_at__lte }),
    [search.received_at__gte, search.received_at__lte]
  );

  const handleTimeRangeChange = useCallback(
    (preset: TimeRangePreset, custom?: { gte?: string; lte?: string }) => {
      const resolved = resolveTimeRange(preset, custom);
      setSearch({
        page: 1,
        received_at__gte: resolved.gte,
        received_at__lte: resolved.lte,
        timeRange: preset,
      });
    },
    [setSearch]
  );

  const recent = useRecentFilterParams();
  const handleFiltersChange = useCallback(
    (next: Parameters<typeof serializeFiltersToSearchParams>[0]) => {
      const delta = serializeFiltersToSearchParams(next);
      setSearch({ ...delta, page: 1 });
      recent.push(delta);
    },
    [setSearch, recent]
  );

  const handleStatusChange = useCallback(
    (s: "all" | "success" | "error") => {
      setSearch({
        error__isnull: s === "error" ? "true" : s === "success" ? "false" : undefined,
        page: 1,
      });
    },
    [setSearch]
  );

  const handleResetAll = useCallback(() => {
    navigate({
      search: {
        detailExpanded: false,
        ordering: isSessionsView ? DEFAULT_SESSIONS_ORDERING : DEFAULT_TRACES_ORDERING,
        page: 1,
        page_size: pageSize,
        projectId: projectId!,
        timeRange: "all" as const,
        view: tracesView,
      } as never,
      to: "/observability",
    });
    setSearchInput("");
  }, [isSessionsView, navigate, pageSize, projectId, tracesView]);

  const apiFilters = useMemo(() => {
    const f: Record<string, string | undefined> = {};
    Object.assign(f, serializeFiltersToSearchParams(advancedFilters));
    if (error__isnull === "true") f.has_error = "true";
    if (error__isnull === "false") f.has_error = "false";
    // Filters on `start_time_ns`, not `received_at`: a backfilled trace's
    // Time column shows Langfuse's own occurrence date, so the range picker
    // must bound the same field or a backfill lands outside its own range.
    if (search.received_at__gte) {
      f.start_time_ns__gte = String(Date.parse(search.received_at__gte) * 1e6);
    }
    if (search.received_at__lte) {
      f.start_time_ns__lte = String(Date.parse(search.received_at__lte) * 1e6);
    }
    f.all_spans = "false";
    if (session) f.session = session;
    if (featureFlags.signals && signalEvent) f.signal_event = signalEvent;
    return f;
  }, [
    advancedFilters,
    error__isnull,
    search.received_at__gte,
    search.received_at__lte,
    session,
    signalEvent,
  ]);

  const activeCapabilityId = useMemo(() => {
    const entry = advancedFilters.find(
      (f) => f.field === "capability" && f.lookup === "eq" && !!f.value && f.value !== "__any__"
    );
    return entry?.value;
  }, [advancedFilters]);
  // The Unbound sentinel is not a UUID; sessions have no unbound axis.
  const unboundOnly = activeCapabilityId === UNBOUND_CAPABILITY;
  const capabilityUuidFilter = unboundOnly ? undefined : activeCapabilityId;

  const tracesOrdering = ordering ?? DEFAULT_TRACES_ORDERING;

  const { data, isFetched, isFetching, isRefetching, error, refetch, dataUpdatedAt } =
    useTracesList({
      enabled: !isSessionsView && !isSignalsView,
      filters: apiFilters,
      ordering: tracesOrdering,
      page,
      pageSize,
      project_id: projectId!,
      search: searchTerm || undefined,
    });

  const sessionOrdering = useMemo(() => {
    const raw = ordering ?? DEFAULT_SESSIONS_ORDERING;
    return SESSION_ORDERING_FIELDS.has(raw.replace(/^-/, "")) ? raw : DEFAULT_SESSIONS_ORDERING;
  }, [ordering]);

  const {
    data: sessionsData,
    isFetched: sessionsFetched,
    isFetching: sessionsFetching,
    isRefetching: sessionsRefetching,
    error: sessionsError,
    refetch: refetchSessions,
    dataUpdatedAt: sessionsDataUpdatedAt,
  } = useSessionsList({
    capability: capabilityUuidFilter,
    enabled: isSessionsView,
    ordering: sessionOrdering,
    page,
    pageSize,
    project_id: projectId!,
    search: searchTerm || undefined,
  });

  const { data: sessionDetail } = useSessionDetail(!isSessionsView ? session : undefined);

  const rows = useMemo(() => data?.results ?? [], [data]);
  const activeCount = (isSessionsView ? sessionsData?.count : data?.count) ?? 0;

  const [columnVisibility, setColumnVisibility] = usePersistedState<VisibilityState>(
    "traces:columnVisibility",
    {
      model: false,
      prompt: true,
      span_type: false,
      status_message: false,
      trace_id: false,
    }
  );

  const [rowSelection, setRowSelection] = useState<RowSelectionState>({});
  // tanstack's `rowSelection` only resolves rows on the current page, so picks
  // are mirrored into a rowId → TraceRef map captured at select time.
  const [selectedRefsById, setSelectedRefsById] = useState<Record<string, TraceRef>>({});
  // "Select all matching" holds the query that selects them, never the resolved
  // ids — constant cost whether it matches 20 traces or 200 000.
  const [allPagesSelection, setAllPagesSelection] = useState<{
    selection: TraceSelectionSpec;
    total: number;
  } | null>(null);
  const [showAddToDataset, setShowAddToDataset] = useState(false);

  // Checking a box drops any "all matching" selection: the two are
  // alternatives, never a union.
  const handleRowSelectionChange: OnChangeFn<RowSelectionState> = useCallback(
    (updater) => {
      setAllPagesSelection(null);
      setRowSelection((prev) => {
        const next = typeof updater === "function" ? updater(prev) : updater;
        // Only current-page rows are resolvable from `rows`, so keep the refs
        // already known and drop ids missing from `next`.
        setSelectedRefsById((prevRefs) => {
          const byRowId = new Map(rows.map((r) => [r.spanId || r.traceId, r]));
          const out: Record<string, TraceRef> = {};
          for (const id of Object.keys(next)) {
            if (!next[id]) continue;
            const existing = prevRefs[id];
            if (existing) {
              out[id] = existing;
              continue;
            }
            const row = byRowId.get(id);
            if (row?.traceId) out[id] = { capabilityId: row.capability ?? null, id: row.traceId };
          }
          return out;
        });
        return next;
      });
    },
    [rows]
  );

  // Several rows of one trace can be checked; the wizard wants each trace once.
  const manualTraceRefs = useMemo(() => {
    const byTrace = new Map<string, TraceRef>();
    for (const ref of Object.values(selectedRefsById)) {
      if (!byTrace.has(ref.id)) byTrace.set(ref.id, ref);
    }
    return [...byTrace.values()];
  }, [selectedRefsById]);
  const selectedTraceRefs = allPagesSelection ? [] : manualTraceRefs;
  const selectionCount = allPagesSelection?.total ?? manualTraceRefs.length;
  // Preselects the dataset's capability when every ticked trace shares one.
  const sharedCapabilityId = useMemo(() => {
    const ids = new Set(selectedTraceRefs.map((ref) => ref.capabilityId).filter(Boolean));
    return ids.size === 1 ? ([...ids][0] ?? undefined) : undefined;
  }, [selectedTraceRefs]);

  const handleSelectAllAcrossPages = useCallback(() => {
    if (!projectId) return;
    setAllPagesSelection({
      selection: {
        // `apiFilters` holds no project (the list request passes it separately),
        // but a selection resolves across every project the user belongs to, so
        // it has to carry its own scope.
        filters: { ...apiFilters, project: projectId },
        ordering: ordering ?? "-start_time_ns",
        search: searchTerm || undefined,
      },
      total: activeCount,
    });
    setRowSelection({});
    setSelectedRefsById({});
  }, [projectId, apiFilters, activeCount, ordering, searchTerm]);

  const handleClearSelection = useCallback(() => {
    setAllPagesSelection(null);
    setRowSelection({});
    setSelectedRefsById({});
  }, []);

  const handleTraceClick = useCallback(
    (clickedTraceId: string) => {
      navigate({
        params: { traceId: clickedTraceId },
        resetScroll: false,
        search: { ...search },
        to: "/observability/$traceId",
      });
    },
    [navigate, search]
  );

  const handleCloseTrace = useCallback(() => {
    navigate({ resetScroll: false, search: { ...search }, to: "/observability" });
  }, [navigate, search]);

  const header = (
    <PageHeader
      actions={
        <Button asChild size="sm" variant="secondary">
          <Link search={(prev) => prev} to="/observability/integrations">
            <Icon.integrations />
            Integrations
          </Link>
        </Button>
      }
      description={
        featureFlags.signals
          ? "Explore sessions, investigate signals and inspect their traces."
          : "Explore sessions and inspect their traces."
      }
      icon={
        <Icon.observabilityTitle
          aria-hidden
          className="size-6 shrink-0 [image-rendering:pixelated] invert"
        />
      }
      title="Observability"
    />
  );

  if (!projectId) {
    return (
      <PageShell header={header} variant="full">
        <ProjectRequiredEmptyState />
      </PageShell>
    );
  }

  // Gate on a poll/refetch, not the initial load — that one is already
  // covered by the table's own skeleton.
  const isPolling = isSessionsView ? sessionsFetching && sessionsFetched : isFetching && isFetched;

  // `useSessionsList` polls at a fixed cadence; `useTracesList` backs off to
  // 5s while the list is empty (faster first-trace feedback) and 10s once it
  // isn't — mirror that here so "next" doesn't drift from the real interval.
  const lastUpdatedAt = isSessionsView ? sessionsDataUpdatedAt : dataUpdatedAt;
  const listCount = data?.count;
  const pollIntervalMs = isSessionsView ? 10_000 : (listCount ?? 0) > 0 ? 10_000 : 5_000;
  const nextPollAt = lastUpdatedAt ? lastUpdatedAt + pollIntervalMs : undefined;

  const toolbarProps = {
    customTimeRange,
    filters: advancedFilters,
    isFetching: isPolling,
    isRefetching: isSessionsView ? sessionsRefetching : isRefetching,
    lastUpdatedAt: lastUpdatedAt || undefined,
    nextPollAt,
    onFiltersChange: handleFiltersChange,
    onRefresh: () => (isSessionsView ? refetchSessions() : refetch()),
    onResetAll: handleResetAll,
    onSearchChange: setSearchInput,
    onStatusChange: handleStatusChange,
    onTimeRangeChange: handleTimeRangeChange,
    projectId,
    searchValue: searchInput,
    status,
    timeRange: timeRange as TimeRangePreset,
    view: tracesView,
  };

  return (
    <PageShell header={header} variant="full">
      <Tabs
        className="w-fit pb-3"
        onValueChange={(next) => {
          navigate({
            search: { page: 1, projectId, timeRange: "all", view: next as typeof view },
            to: "/observability",
          });
          setSearchInput("");
          handleClearSelection();
        }}
        value={traceId || (view === "signals" && !featureFlags.signals) ? "roots" : view}
      >
        <TabsList aria-label="Observability views">
          <TabsTrigger value="sessions">Sessions</TabsTrigger>
          {featureFlags.signals && <TabsTrigger value="signals">Signals</TabsTrigger>}
          <TabsTrigger value="roots">Traces</TabsTrigger>
        </TabsList>
      </Tabs>
      {isSignalsView ? (
        <SignalEvents
          onChange={setSearch}
          onSelect={(id, conversation) => {
            navigate({
              search: {
                page: 1,
                projectId,
                session: conversation || undefined,
                signal_event: id,
                timeRange: "all",
                view: "roots",
              },
              to: "/observability",
            });
            setSearchInput("");
          }}
          page={page}
          projectId={projectId}
          session={session}
          signal={search.signal}
          unseen={!!search.unseen}
        />
      ) : isSessionsView ? (
        <DataTable<SessionRow>
          columns={sessionsColumns}
          data={sessionsData}
          emptyState={<SessionsEmptyState />}
          error={sessionsError}
          getRowId={(row) => row.id}
          hasActiveFilters={hasActiveFilters}
          // `isFetched`, not `isLoading`: a disabled or unstarted query reports
          // isLoading false while holding no rows, which would render the empty
          // state over data still on its way.
          isLoading={!sessionsFetched}
          onClearFilters={handleResetAll}
          onOrderingChange={(next) =>
            setSearch({
              ordering: nextOrdering(next, sessionOrdering, DEFAULT_SESSIONS_ORDERING),
              page: 1,
            })
          }
          onPageChange={(p) => setSearch({ page: p })}
          onPageSizeChange={(s) => setSearch({ page: 1, page_size: s })}
          onRowClick={(row) =>
            setSearch({
              ordering: undefined,
              page: 1,
              session: row.id,
              signal_event: undefined,
              view: "roots",
            })
          }
          ordering={sessionOrdering}
          page={page}
          pageSize={pageSize}
          storageKey="traces:sessions-cols"
          toolbar={(t) => <TracesTableToolbar {...toolbarProps} table={t} />}
        />
      ) : (
        <DataTable<TraceRow>
          banner={
            featureFlags.signals && signalEvent ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span>Traces checked for the selected signal.</span>
                <Button
                  aria-label="Clear signal filter"
                  onClick={() =>
                    setSearch({ page: 1, session: undefined, signal_event: undefined })
                  }
                  size="icon-sm"
                  variant="ghost"
                >
                  <Icon.close />
                </Button>
              </div>
            ) : session ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="chip-label inline-flex h-7 max-w-full items-center gap-1.5 whitespace-nowrap rounded-sm border border-primary/40 bg-primary/5 px-2.5 text-xs text-primary">
                  <Icon.session className="size-3.5 shrink-0" />
                  <span className="truncate">
                    Session:{" "}
                    {sessionDetail?.name || sessionDetail?.externalId || `${session.slice(0, 8)}…`}
                  </span>
                  <button
                    aria-label="Clear session filter"
                    className="-mr-1 shrink-0 rounded-sm p-0.5 hover:bg-primary/20"
                    onClick={() => setSearch({ page: 1, session: undefined })}
                    type="button"
                  >
                    <Icon.close className="size-3" />
                  </button>
                </span>
              </div>
            ) : null
          }
          columns={tracesColumns}
          columnVisibility={columnVisibility}
          data={data}
          // Keyed so a project switch remounts it and the cached-key lookup re-runs.
          emptyState={<TracesEmptyState key={projectId} />}
          enableRowSelection
          error={error}
          getRowId={(row) => row.spanId || row.traceId}
          hasActiveFilters={hasActiveFilters}
          // See the sessions table above: settledness is `isFetched`, not `isLoading`.
          isLoading={!isFetched}
          isRowActive={(row) => !!traceId && row.traceId === traceId}
          onClearFilters={handleResetAll}
          onColumnVisibilityChange={setColumnVisibility}
          onOrderingChange={(next) =>
            setSearch({
              ordering: nextOrdering(next, tracesOrdering, DEFAULT_TRACES_ORDERING),
              page: 1,
            })
          }
          onPageChange={(p) => setSearch({ page: p })}
          onPageSizeChange={(s) => setSearch({ page: 1, page_size: s })}
          onRowClick={(row) => handleTraceClick(row.traceId)}
          onRowSelectionChange={handleRowSelectionChange}
          ordering={tracesOrdering}
          page={page}
          pageSize={pageSize}
          rowSelection={rowSelection}
          storageKey="traces:table-cols"
          toolbar={(t) => <TracesTableToolbar {...toolbarProps} table={t} />}
        />
      )}

      {!isSessionsView && !isSignalsView && (
        <TraceSelectionBar
          allPagesSelected={allPagesSelection != null}
          onAddToDataset={featureFlags.datasets ? () => setShowAddToDataset(true) : undefined}
          onClear={handleClearSelection}
          onSelectAllPages={handleSelectAllAcrossPages}
          selectedCount={selectionCount}
          totalCount={activeCount}
        />
      )}

      {featureFlags.datasets && (
        <NewDatasetDialog
          initialCapabilityId={sharedCapabilityId}
          initialSelection={allPagesSelection?.selection ?? null}
          initialSelectionCount={allPagesSelection?.total}
          initialTraceIds={selectedTraceRefs.map((ref) => ref.id)}
          onDone={() => {
            setRowSelection({});
            setSelectedRefsById({});
            setAllPagesSelection(null);
          }}
          onOpenChange={setShowAddToDataset}
          open={showAddToDataset}
          projectId={projectId!}
        />
      )}

      {/* Outside interaction is suppressed so picking another row doesn't
          dismiss the panel, which leaves Escape and the close button as the
          only ways out — the close button is not optional here. */}
      <Sheet
        modal={false}
        onOpenChange={(next) => {
          if (!next) handleCloseTrace();
        }}
        open={!!traceId}
      >
        <SheetContent
          // Lines the close button up with `TracePanelNavigationHeader`'s own
          // button row (SheetBody py-4 + its py-1.5), and right-aligns it with
          // the Span Details card (SheetBody px-5 + the card's p-4 = right-9).
          closeButtonClassName="top-[22px] right-9"
          floatingClose
          onEscapeKeyDown={handleCloseTrace}
          onInteractOutside={(e) => e.preventDefault()}
          showOverlay={false}
          side="right"
          size={detailExpanded ? "full" : "lg"}
        >
          <SheetTitle className="sr-only">Trace detail</SheetTitle>
          <SheetDescription className="sr-only">
            Spans, timings and payloads for the selected trace.
          </SheetDescription>
          <SheetBody className="flex flex-col">
            <Outlet />
          </SheetBody>
        </SheetContent>
      </Sheet>
    </PageShell>
  );
}

function SessionsEmptyState() {
  return (
    <EmptyState
      className="flex-1"
      description={
        <>
          Stamp a <code className="font-mono text-sm">conversation.id</code> on your traces (e.g.
          via the SDK&apos;s <code className="font-mono text-sm">set_conversation_id</code>) to
          group multi-turn interactions into sessions.
        </>
      }
      icon={Icon.session}
      size="section"
      title="No sessions found"
    />
  );
}

function SetupActions() {
  const [showSetup, setShowSetup] = useState(false);

  return (
    <>
      <div className="mt-6 grid w-full grid-cols-2 gap-2">
        <Button className="w-full" onClick={() => setShowSetup(true)} size="sm">
          <Icon.terminal />
          Add observability
        </Button>
        <Button asChild className="w-full" size="sm" variant="secondary">
          <a
            href="https://docs.overmindlab.ai/core/observability"
            rel="noopener noreferrer"
            target="_blank"
          >
            <Icon.docs />
            View the full guide
          </a>
        </Button>
      </div>
      <ObservabilitySetupDialog onOpenChange={setShowSetup} open={showSetup} />
    </>
  );
}

function TracesEmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-4 py-12">
      <div className="flex w-full max-w-md flex-col items-center">
        <EmptyState
          className="w-full px-0 py-0"
          description="No traces yet."
          icon={Icon.observability}
          size="section"
          title="No traces yet"
        />
        <SetupActions />
      </div>
    </div>
  );
}
