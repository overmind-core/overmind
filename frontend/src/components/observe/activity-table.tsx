import { type ReactNode, useMemo } from "react";

import type { ColumnDef } from "@tanstack/react-table";

import { sessionsColumns } from "@/components/traces/sessions-columns";
import { tracesColumns } from "@/components/traces/traces-columns";
import { Button } from "@/components/ui/button";
import { DataTable } from "@/components/ui/data-table";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { type IssueScope, useCapabilityActivity, useIssuePage } from "@/hooks/use-issues";
import { type SessionRow, useSessionsList } from "@/hooks/use-sessions";
import { type TraceRow, useTracesList } from "@/hooks/use-traces";
import { formatCost, formatNumber } from "@/lib/formatters";
import type { ObserveSearch } from "@/lib/schemas";
import { cn } from "@/lib/utils";
import type { CapabilityActivity, IssueRow } from "@/openapi";
import { formatRate, SeverityBadge, Sparkline, stateLabel } from "./issue-parts";

const QUIET_STATES = new Set(["ongoing", "resolved", "muted"]);

function Numeric({ children }: { children: ReactNode }) {
  return <span className="block w-full text-right tabular-nums">{children}</span>;
}

const DAY_MS = 86_400_000;
const RANGE_MS = {
  "7d": 7 * DAY_MS,
  "24h": DAY_MS,
  "30d": 30 * DAY_MS,
  "90d": 90 * DAY_MS,
  "180d": 180 * DAY_MS,
};

const issueColumns: ColumnDef<IssueRow>[] = [
  {
    cell: ({ row }) => <SeverityBadge severity={row.original.severity} />,
    header: "Severity",
    id: "severity",
    size: 90,
  },
  {
    cell: ({ row }) => (
      <span className={cn(QUIET_STATES.has(row.original.state) && "text-muted-foreground")}>
        {stateLabel(row.original.state)}
      </span>
    ),
    header: "State",
    id: "state",
    size: 90,
  },
  {
    cell: ({ row }) => (
      <span
        className={cn("block truncate", row.original.severity === "low" && "text-muted-foreground")}
        title={row.original.title}
      >
        {row.original.title}
      </span>
    ),
    header: "Issue",
    id: "title",
    size: 410,
  },
  {
    cell: ({ row }) => (
      <span className="text-muted-foreground">{row.original.capabilityName || "—"}</span>
    ),
    header: "Capability",
    id: "capability",
    size: 130,
  },
  {
    cell: ({ row }) => <Numeric>{formatRate(row.original.rate)}</Numeric>,
    header: () => <Numeric>Rate</Numeric>,
    id: "rate",
    size: 70,
  },
  {
    cell: ({ row }) => <Sparkline values={row.original.trend} />,
    header: "Trend",
    id: "trend",
    size: 90,
  },
  {
    cell: ({ row }) => <Numeric>{formatNumber(row.original.traces)}</Numeric>,
    header: () => <Numeric>Traces</Numeric>,
    id: "traces",
    size: 65,
  },
  {
    cell: ({ row }) => <DateTime value={row.original.lastSeen} />,
    header: "Last seen",
    id: "lastSeen",
    size: 110,
  },
];

function capabilityColumns(
  onTrain: (signal: { capability: string; capabilityName: string }) => void
): ColumnDef<CapabilityActivity>[] {
  return [
    {
      cell: ({ row }) =>
        row.original.severity ? (
          <SeverityBadge severity={row.original.severity} />
        ) : (
          <span className="text-muted-foreground">None</span>
        ),
      header: "Worst issue",
      id: "severity",
      size: 100,
    },
    { accessorKey: "capabilityName", header: "Capability", size: 240 },
    {
      cell: ({ row }) => <Numeric>{formatNumber(row.original.issues)}</Numeric>,
      header: () => <Numeric>Issues</Numeric>,
      id: "issues",
      size: 70,
    },
    {
      cell: ({ row }) => <Numeric>{formatRate(row.original.failureRate)}</Numeric>,
      header: () => <Numeric>Failure rate</Numeric>,
      id: "failureRate",
      size: 100,
    },
    {
      cell: ({ row }) => <Numeric>{formatNumber(row.original.traces)}</Numeric>,
      header: () => <Numeric>Traces</Numeric>,
      id: "traces",
      size: 90,
    },
    {
      cell: ({ row }) => <Numeric>{row.original.score?.toFixed(2) ?? "Not scored"}</Numeric>,
      header: () => <Numeric>Score</Numeric>,
      id: "score",
      size: 90,
    },
    {
      cell: ({ row }) => <Numeric>{formatCost(row.original.cost)}</Numeric>,
      header: () => <Numeric>Cost</Numeric>,
      id: "cost",
      size: 80,
    },
    {
      cell: ({ row }) =>
        row.original.canTrain ? (
          <Button
            onClick={(event) => {
              event.stopPropagation();
              onTrain(row.original);
            }}
            size="xs"
            variant="secondary"
          >
            <Icon.training />
            Train
          </Button>
        ) : null,
      header: "",
      id: "train",
      size: 90,
    },
  ];
}

const CAUSES = [
  { label: "All issues", value: "all" },
  { label: "Trainable", value: "model" },
  { label: "System", value: "system" },
  { label: "Data", value: "data" },
] as const;

function whole<T>(rows: T[] | undefined) {
  return rows && { count: rows.length, next: null, previous: null, results: rows };
}

export function ActivityTable({
  scope,
  search,
  onChange,
  onOpenIssue,
  onOpenTrace,
  onTrain,
}: {
  scope: IssueScope;
  search: ObserveSearch;
  onChange: (updates: Partial<ObserveSearch>) => void;
  onOpenIssue: (id: string) => void;
  onOpenTrace: (traceId: string) => void;
  onTrain: (signal: { capability: string; capabilityName: string }) => void;
}) {
  const { group, page, page_size: pageSize, issueFilter, session, cause } = search;
  const issues = useIssuePage(scope, { cause, page, pageSize }, group === "issue");
  const queueColumns = useMemo(() => capabilityColumns(onTrain), [onTrain]);
  const capabilities = useCapabilityActivity(scope, group === "capability");
  const sessions = useSessionsList({
    capability: scope.capability,
    enabled: group === "session",
    ordering: "-last_span_ns",
    page,
    pageSize,
    project_id: scope.projectId,
  });
  const filters = useMemo(() => {
    const since = Date.now() - RANGE_MS[search.range];
    const next: Record<string, string> = {
      all_spans: "false",
      start_time_ns__gte: String(since * 1e6),
    };
    if (scope.capability) next.capability = scope.capability;
    if (issueFilter) next.issue = issueFilter;
    if (session) next.session = session;
    return next;
  }, [issueFilter, scope.capability, search.range, session]);
  const traces = useTracesList({
    enabled: group === "trace",
    filters,
    ordering: "-start_time_ns",
    page,
    pageSize,
    project_id: scope.projectId,
  });

  const tabs = (
    <div className="mb-3 flex flex-wrap items-center gap-3">
      <h2 className="text-sm text-foreground">All activity</h2>
      {(issueFilter || session) && (
        <Button
          onClick={() => onChange({ issueFilter: undefined, page: 1, session: undefined })}
          size="xs"
          variant="secondary"
        >
          <Icon.close />
          {issueFilter ? "Issue filter" : "Session filter"}
        </Button>
      )}
      {group === "issue" && (
        <Select
          onValueChange={(value) =>
            onChange({
              cause: value === "all" ? undefined : (value as ObserveSearch["cause"]),
              page: 1,
            })
          }
          value={cause ?? "all"}
        >
          <SelectTrigger aria-label="Issue kind" className="ml-auto w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {CAUSES.map((item) => (
              <SelectItem key={item.value} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Tabs
        className={cn("w-fit shrink-0", group !== "issue" && "ml-auto")}
        onValueChange={(next) =>
          onChange({
            group: next as ObserveSearch["group"],
            issueFilter: undefined,
            page: 1,
            session: undefined,
          })
        }
        value={group}
      >
        <TabsList aria-label="Group by">
          <TabsTrigger value="issue">Issue</TabsTrigger>
          <TabsTrigger value="capability">Capability</TabsTrigger>
          <TabsTrigger value="session">Session</TabsTrigger>
          <TabsTrigger value="trace">Trace</TabsTrigger>
        </TabsList>
      </Tabs>
    </div>
  );

  const shared = {
    hidePagination: false,
    onPageChange: (next: number) => onChange({ page: next }),
    onPageSizeChange: (next: number) => onChange({ page: 1, page_size: next }),
    page,
    pageSize,
  };

  return (
    <section aria-label="All activity" className="flex flex-col">
      {tabs}
      {group === "issue" && (
        <DataTable<IssueRow>
          {...shared}
          columns={issueColumns}
          data={issues.data}
          error={issues.error}
          getRowId={(row) => row.id}
          isLoading={!issues.isFetched}
          onRowClick={(row) => onOpenIssue(row.id)}
        />
      )}
      {group === "capability" && (
        <DataTable<CapabilityActivity>
          {...shared}
          columns={queueColumns}
          data={whole(capabilities.data?.capabilities)}
          error={capabilities.error}
          getRowId={(row) => row.capability}
          hidePagination
          isLoading={!capabilities.isFetched}
          onRowClick={(row) => onChange({ capability: row.capability, group: "issue", page: 1 })}
        />
      )}
      {group === "session" && (
        <DataTable<SessionRow>
          {...shared}
          columns={sessionsColumns}
          data={sessions.data}
          error={sessions.error}
          getRowId={(row) => row.id}
          isLoading={!sessions.isFetched}
          onRowClick={(row) => onChange({ group: "trace", page: 1, session: row.id })}
        />
      )}
      {group === "trace" && (
        <DataTable<TraceRow>
          {...shared}
          columns={tracesColumns.filter((column) => column.id !== "select")}
          data={traces.data}
          error={traces.error}
          getRowId={(row) => row.spanId || row.traceId}
          isLoading={!traces.isFetched}
          onRowClick={(row) => onOpenTrace(row.traceId)}
        />
      )}
    </section>
  );
}
