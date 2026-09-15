import { Fragment } from "react";

import { useSearch } from "@tanstack/react-router";

import { EntityRef } from "@/components/entity-ref";
import { TraceExecutionScore } from "@/components/traces/trace-score-chips";
import { Icon } from "@/components/ui/icons";
import {
  capabilityHandoffChain,
  executionCapabilityLabel,
  executionCapabilitySlot,
  isUnboundExecution,
  shortConversationId,
} from "@/hooks/use-task-executions";
import { TONE_CHIP } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { TaskExecutionList } from "@/openapi";

const CHIP = "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium";

export function TraceGroupHeader({
  traceId,
  executions,
  capabilityNameById,
  count,
}: {
  traceId: string;
  executions: TaskExecutionList[];
  capabilityNameById: Map<string, string>;
  count: number;
}) {
  const chain = capabilityHandoffChain(executions, capabilityNameById);
  return (
    <span className="flex min-w-0 items-center gap-2 text-foreground">
      <Icon.observability aria-hidden className="size-3.5 shrink-0" />
      <span className="shrink-0 font-mono" title={traceId}>
        {shortConversationId(traceId)}
      </span>
      <span className="flex min-w-0 items-center gap-1">
        {chain.map((name, index) => (
          <Fragment key={`${name}-${index}`}>
            {index > 0 && <Icon.forward aria-label="hands off to" className="size-3 shrink-0" />}
            <span className="truncate">{name}</span>
          </Fragment>
        ))}
      </span>
      <span className="shrink-0 text-muted-foreground">{count} executions</span>
    </span>
  );
}

export function TraceExecutionFlow({
  executions,
  capabilityNameById,
  currentId,
  onSelect,
}: {
  executions: TaskExecutionList[];
  capabilityNameById: Map<string, string>;
  currentId?: string;
  onSelect?: (row: TaskExecutionList) => void;
}) {
  const { projectId } = useSearch({ from: "/_auth" });
  return (
    <ol className="flex flex-col gap-1">
      {executions.map((row, index) => {
        const prev = index > 0 ? executions[index - 1] : null;
        const handoff = prev && executionCapabilitySlot(prev) !== executionCapabilitySlot(row);
        const current = row.id === currentId;
        const taskName = isUnboundExecution(row)
          ? row.terminalKind || "—"
          : row.behaviourKey || "—";
        const selectable = !!onSelect && !current;
        const trailing = (
          <>
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
              {taskName}
            </span>
            {(row.status === "error" || row.status === "interrupted") && (
              <span
                className={cn(
                  CHIP,
                  "shrink-0",
                  row.status === "error" ? TONE_CHIP.error : TONE_CHIP.warning
                )}
              >
                {row.status === "error" ? "Error" : "Interrupted"}
              </span>
            )}
            <TraceExecutionScore
              score={row.successScore ?? null}
              scoringPending={row.scoringPending}
            />
          </>
        );
        return (
          <li className="flex flex-col gap-1" key={row.id}>
            {handoff && prev && (
              <div className="flex items-center gap-2 py-0.5">
                <span aria-hidden className="h-px flex-1 bg-border/70" />
                <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
                  <span className="truncate">
                    {executionCapabilityLabel(prev, capabilityNameById)}
                  </span>
                  <Icon.forward aria-label="hands off to" className="size-3 shrink-0" />
                  <span className="truncate">
                    {executionCapabilityLabel(row, capabilityNameById)}
                  </span>
                </span>
                <span aria-hidden className="h-px flex-1 bg-border/70" />
              </div>
            )}
            <div
              aria-current={current ? "true" : undefined}
              className={cn(
                "flex items-center gap-2 rounded-sm border border-border/70 px-2.5 py-1.5",
                current && "border-primary/40 bg-primary/5"
              )}
            >
              {row.capability ? (
                <EntityRef
                  className="min-w-0 max-w-[45%] shrink-0"
                  id={row.capability}
                  kind="capability"
                  name={executionCapabilityLabel(row, capabilityNameById)}
                  projectId={projectId}
                />
              ) : (
                <span className={cn(CHIP, "shrink-0", TONE_CHIP.warning)}>Unbound</span>
              )}
              {selectable ? (
                <button
                  aria-label={`View execution ${taskName}`}
                  className="flex min-w-0 flex-1 items-center gap-2 rounded-sm text-left hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  onClick={() => onSelect(row)}
                  type="button"
                >
                  {trailing}
                  <Icon.chevronRight
                    aria-hidden
                    className="size-3.5 shrink-0 text-muted-foreground"
                  />
                </button>
              ) : (
                <span className="flex min-w-0 flex-1 items-center gap-2">{trailing}</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
