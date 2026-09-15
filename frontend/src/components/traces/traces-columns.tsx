import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { TraceExecutionScore } from "@/components/traces/trace-score-chips";
import { TraceSourceChip } from "@/components/traces/trace-source-chip";
import { Checkbox } from "@/components/ui/checkbox";
import { DateTime } from "@/components/ui/datetime";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { TraceRow } from "@/hooks/use-traces";
import { TONE_CHIP } from "@/lib/colors";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";

export const tracesColumns: ColumnDef<TraceRow>[] = [
  {
    cell: ({ row }) => (
      <Checkbox
        aria-label="Select row"
        checked={row.getIsSelected()}
        onCheckedChange={(value) => row.toggleSelected(!!value)}
        onClick={(e) => e.stopPropagation()}
      />
    ),
    enableResizing: false,
    enableSorting: false,
    header: ({ table }) => (
      <Checkbox
        aria-label="Select all rows on this page"
        checked={
          table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")
        }
        onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
      />
    ),
    id: "select",
    maxSize: 48,
    meta: { label: "Select" },
    minSize: 40,
    size: 40,
  },
  {
    accessorFn: (row) => row.applicationName ?? row.traceGroup ?? row.traceId,
    cell: ({ row }) => (
      <span className="block truncate font-mono text-sm" title={row.original.applicationName}>
        {row.original.applicationName ??
          row.original.traceGroup ??
          row.original.traceId.slice(0, 12)}
      </span>
    ),
    header: "Name",
    id: "name",
    meta: { label: "Name", orderingField: "name" },
    size: 200,
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
    id: "prompt",
    meta: { label: "Capability", orderingField: "capability__name" },
    size: 180,
  },
  {
    accessorFn: (row) => row.score ?? null,
    cell: ({ row }) => (
      <TraceExecutionScore
        conflict={row.original.conflict}
        evaluations={row.original.evaluations}
        score={row.original.score ?? null}
        scoringPending={row.original.scoringPending}
      />
    ),
    header: "Score",
    id: "trace_scores",
    meta: { label: "Score", orderingField: "trace_scores" },
    size: 120,
  },
  {
    accessorFn: (row) => row.traceStatus ?? (row.error ? "error" : "ok"),
    cell: ({ row }) => {
      const status = row.original.traceStatus;
      if (status === "live") {
        return (
          <span
            className={`chip-label inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium ${TONE_CHIP.info}`}
          >
            Live
          </span>
        );
      }
      if (status === "interrupted") {
        return (
          <span
            className={`chip-label inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium ${TONE_CHIP.warning}`}
          >
            Interrupted
          </span>
        );
      }
      const isError = !!row.original.error;
      return (
        <span
          className={`chip-label inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium ${
            isError ? TONE_CHIP.error : TONE_CHIP.success
          }`}
        >
          {isError ? "Error" : "OK"}
        </span>
      );
    },
    header: "Status",
    id: "status",
    meta: { label: "Status", orderingField: "status_code" },
    size: 100,
  },
  {
    accessorKey: "durationNs",
    cell: ({ row }) => {
      const ns = row.original.durationNs;
      const status = row.original.traceStatus;
      // Without a root the head span's duration is one inner span, not the run.
      if (ns == null || ns <= 0 || (status && status !== "completed"))
        return <span className="text-muted-foreground">—</span>;
      return (
        <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
          {formatDuration(ns / 1_000_000)}
        </span>
      );
    },
    header: "Duration",
    id: "duration",
    meta: { label: "Duration", orderingField: "duration_ns" },
    size: 112,
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
          projectId={row.original.project ?? ""}
        />
      );
    },
    header: "Model",
    id: "model",
    meta: { label: "Model", orderingField: "model" },
    size: 160,
  },
  {
    accessorKey: "traceId",
    cell: ({ row }) => {
      const id = row.original.traceId ?? "";
      const display = id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-8)}` : id;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="block truncate font-mono text-xs text-muted-foreground">
                {display}
              </span>
            </TooltipTrigger>
            <TooltipContent className="font-mono text-xs max-w-md break-all" side="top">
              {id}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: "Trace ID",
    id: "trace_id",
    meta: { label: "Trace ID", orderingField: "trace_id" },
    size: 140,
  },
  {
    accessorKey: "spanType",
    cell: ({ row }) => {
      const { spanType } = row.original;
      if (!spanType) return <span className="text-muted-foreground">—</span>;
      return (
        <span className="chip-label inline-flex h-6 items-center rounded-sm border border-muted-foreground/50 bg-wash-raised px-2 text-xs font-medium">
          {spanType}
        </span>
      );
    },
    header: "Type",
    id: "span_type",
    meta: { label: "Type", orderingField: "span_type" },
    size: 96,
  },
  {
    accessorKey: "error",
    cell: ({ row }) => {
      const msg = row.original.error;
      if (!msg) return <span className="text-muted-foreground">—</span>;
      const truncated = msg.length > 60 ? `${msg.slice(0, 60)}…` : msg;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="block truncate text-xs text-muted-foreground">{truncated}</span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md text-xs whitespace-pre-wrap" side="top">
              {msg}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: "Error",
    id: "status_message",
    meta: { label: "Error", orderingField: "status_message" },
    size: 180,
  },
  {
    accessorKey: "source",
    cell: ({ row }) => <TraceSourceChip source={row.original.source ?? "overmind"} />,
    header: "Source",
    id: "source",
    meta: { label: "Source" },
    size: 120,
  },
  {
    accessorKey: "startTimeNs",
    cell: ({ row }) => (
      <DateTime
        className="whitespace-nowrap text-sm text-muted-foreground"
        exact
        value={row.original.startTimeNs}
      />
    ),
    header: "Time",
    id: "timestamp",
    meta: { label: "Time", orderingField: "start_time_ns" },
    size: 168,
  },
];
