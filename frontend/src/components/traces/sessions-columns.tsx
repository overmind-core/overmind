import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { TraceExecutionScore } from "@/components/traces/trace-score-chips";
import { DateTime } from "@/components/ui/datetime";
import { nsToIso, type SessionRow } from "@/hooks/use-sessions";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";

export const sessionsColumns: ColumnDef<SessionRow>[] = [
  {
    accessorFn: (row) => row.name || row.externalId,
    cell: ({ row }) => {
      const label = row.original.name || row.original.externalId;
      return (
        <span className="block truncate font-mono text-sm" title={label}>
          {label}
        </span>
      );
    },
    header: "Session",
    id: "session",
    meta: { label: "Session", orderingField: "name" },
    size: 280,
  },
  {
    accessorKey: "capability",
    cell: ({ row }) => {
      const value = row.original.capability;
      if (!value) return <span className="text-muted-foreground">—</span>;
      const name = row.original.capabilityName;
      const display = name ?? (value.length > 14 ? `${value.slice(0, 12)}…` : value);
      return <EntityRef id={value} kind="capability" name={display} />;
    },
    header: "Capability",
    id: "capability",
    meta: { label: "Capability", orderingField: "capability__name" },
    size: 180,
  },
  {
    accessorFn: (row) => row.sessionScore ?? null,
    cell: ({ row }) => (
      <TraceExecutionScore score={row.original.sessionScore ?? null} tooltip="Session score" />
    ),
    header: "Score",
    id: "session_score",
    meta: { label: "Score", orderingField: "session_score" },
    size: 90,
  },
  {
    accessorKey: "traceCount",
    cell: ({ row }) => (
      <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
        {formatNumber(row.original.traceCount)}
      </span>
    ),
    header: "Traces",
    id: "trace_count",
    meta: { label: "Traces", orderingField: "trace_count" },
    size: 104,
  },
  {
    accessorKey: "spanCount",
    cell: ({ row }) => (
      <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
        {formatNumber(row.original.spanCount)}
      </span>
    ),
    header: "Spans",
    id: "span_count",
    meta: { label: "Spans", orderingField: "span_count" },
    size: 100,
  },
  {
    accessorKey: "totalTokens",
    cell: ({ row }) => {
      const n = row.original.totalTokens;
      if (n == null || n < 0) return <span className="text-muted-foreground">—</span>;
      return (
        <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
          {formatNumber(n)}
        </span>
      );
    },
    header: "Tokens",
    id: "total_tokens",
    meta: { label: "Tokens", orderingField: "total_tokens" },
    size: 104,
  },
  {
    accessorKey: "totalCost",
    cell: ({ row }) => {
      const c = row.original.totalCost;
      if (c == null) return <span className="text-muted-foreground">—</span>;
      return (
        <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
          {formatCost(c)}
        </span>
      );
    },
    header: "Cost",
    id: "total_cost",
    meta: { label: "Cost", orderingField: "total_cost" },
    size: 96,
  },
  {
    accessorFn: (row) => row.model ?? "",
    cell: ({ row }) => {
      const model = row.original.model;
      if (!model) return <span className="text-muted-foreground">—</span>;
      return (
        <FinetuningModelChip
          className="max-w-full"
          compact
          model={model}
          projectId={row.original.project}
        />
      );
    },
    header: "Model",
    id: "model",
    meta: { label: "Model", orderingField: "model" },
    size: 180,
  },
  {
    accessorFn: (row) =>
      row.firstSpanNs != null && row.lastSpanNs != null ? row.lastSpanNs - row.firstSpanNs : null,
    cell: ({ row }) => {
      const { firstSpanNs, lastSpanNs } = row.original;
      if (firstSpanNs == null || lastSpanNs == null || lastSpanNs <= firstSpanNs)
        return <span className="text-muted-foreground">—</span>;
      return (
        <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
          {formatDuration((lastSpanNs - firstSpanNs) / 1_000_000)}
        </span>
      );
    },
    header: "Timespan",
    id: "timespan",
    meta: { label: "Timespan", orderingField: "timespan" },
    size: 116,
  },
  {
    accessorKey: "lastSpanNs",
    cell: ({ row }) => (
      <DateTime
        className="whitespace-nowrap text-sm text-muted-foreground"
        value={nsToIso(row.original.lastSpanNs)}
      />
    ),
    header: "Last activity",
    id: "last_activity",
    meta: { label: "Last activity", orderingField: "last_span_ns" },
    size: 132,
  },
];
