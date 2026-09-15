import type { ColumnDef } from "@tanstack/react-table";

import { EntityRef } from "@/components/entity-ref";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { TraceExecutionScore } from "@/components/traces/trace-score-chips";
import { DateTime } from "@/components/ui/datetime";
import {
  executionConversationId,
  isUnboundExecution,
  shortConversationId,
  unboundReason,
} from "@/hooks/use-task-executions";
import { TONE_CHIP } from "@/lib/colors";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import type { TaskExecutionList } from "@/openapi";

const CHIP = "chip-label inline-flex h-6 items-center rounded-sm border px-2 text-xs font-medium";

export function buildExecutionsColumns(options: {
  capabilityNameById: Map<string, string>;
  behaviourNameById: Map<string, string>;
  projectId: string;
}): ColumnDef<TaskExecutionList>[] {
  const { behaviourNameById, capabilityNameById, projectId } = options;
  return [
    {
      accessorKey: "capability",
      cell: ({ row }) => {
        const capabilityId = row.original.capability;
        if (!capabilityId) return <span className="text-muted-foreground">—</span>;
        return (
          <EntityRef
            id={capabilityId}
            kind="capability"
            name={capabilityNameById.get(capabilityId)}
            projectId={projectId}
          />
        );
      },
      header: "Capability",
      id: "capability",
      meta: { label: "Capability" },
      size: 180,
    },
    {
      accessorKey: "behaviourKey",
      cell: ({ row }) => {
        if (isUnboundExecution(row.original)) {
          return (
            <span className={`${CHIP} ${TONE_CHIP.warning}`}>{unboundReason(row.original)}</span>
          );
        }
        const behaviourId = row.original.behaviour;
        const name =
          (behaviourId ? behaviourNameById.get(behaviourId) : undefined) ||
          row.original.behaviourKey;
        return (
          <span className="block truncate text-sm font-medium" title={name}>
            {name}
          </span>
        );
      },
      header: "Task",
      id: "behaviour",
      meta: { label: "Task", orderingField: "behaviour__key" },
      size: 220,
    },
    {
      accessorFn: (row) => row.successScore ?? null,
      cell: ({ row }) => (
        <TraceExecutionScore
          score={row.original.successScore ?? null}
          scoringPending={row.original.scoringPending}
        />
      ),
      header: "Score",
      id: "success_score",
      meta: { label: "Score", orderingField: "success_score" },
      size: 90,
    },
    {
      accessorKey: "status",
      cell: ({ row }) => {
        const { status } = row.original;
        if (status === "error") {
          return <span className={`${CHIP} ${TONE_CHIP.error}`}>Error</span>;
        }
        if (status === "interrupted") {
          return <span className={`${CHIP} ${TONE_CHIP.warning}`}>Interrupted</span>;
        }
        return <span className={`${CHIP} ${TONE_CHIP.success}`}>Completed</span>;
      },
      header: "Status",
      id: "status",
      meta: { label: "Status", orderingField: "status" },
      size: 110,
    },
    {
      accessorKey: "durationMs",
      cell: ({ row }) => {
        const ms = row.original.durationMs;
        if (ms == null || ms <= 0) return <span className="text-muted-foreground">—</span>;
        return (
          <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
            {formatDuration(ms)}
          </span>
        );
      },
      header: "Duration",
      id: "duration_ms",
      meta: { label: "Duration", orderingField: "duration_ms" },
      size: 100,
    },
    {
      accessorKey: "totalTokens",
      cell: ({ row }) => {
        const tokens = row.original.totalTokens;
        if (tokens == null || tokens < 0) return <span className="text-muted-foreground">—</span>;
        return (
          <span className="block whitespace-nowrap text-sm tabular-nums text-muted-foreground">
            {formatNumber(tokens)}
          </span>
        );
      },
      header: "Tokens",
      id: "total_tokens",
      meta: { label: "Tokens" },
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
      meta: { label: "Cost" },
      size: 96,
    },
    {
      accessorFn: (row) => row.model ?? "",
      cell: ({ row }) => {
        const model = row.original.model;
        if (!model) return <span className="text-muted-foreground">—</span>;
        return (
          <FinetuningModelChip className="max-w-full" compact model={model} projectId={projectId} />
        );
      },
      header: "Model",
      id: "model",
      meta: { label: "Model" },
      size: 160,
    },
    {
      accessorKey: "conversationId",
      cell: ({ row }) => {
        const id = executionConversationId(row.original);
        if (!id) return <span className="text-muted-foreground">—</span>;
        return (
          <span className="block truncate font-mono text-xs text-muted-foreground" title={id}>
            {shortConversationId(id)}
          </span>
        );
      },
      header: "Conversation",
      id: "conversation_id",
      meta: { label: "Conversation", orderingField: "conversation_id" },
      size: 128,
    },
    {
      accessorKey: "terminalKind",
      cell: ({ row }) => {
        const kind = row.original.terminalKind;
        if (!kind) return <span className="text-muted-foreground">—</span>;
        return <span className="block truncate text-sm text-muted-foreground">{kind}</span>;
      },
      header: "Terminal",
      id: "terminal_kind",
      meta: { label: "Terminal", orderingField: "terminal_kind" },
      size: 130,
    },
    {
      accessorKey: "startedAt",
      cell: ({ row }) => (
        <DateTime
          className="whitespace-nowrap text-sm text-muted-foreground"
          value={row.original.startedAt}
        />
      ),
      header: "Time",
      id: "started_at",
      meta: { label: "Time", orderingField: "started_at" },
      size: 110,
    },
  ];
}
