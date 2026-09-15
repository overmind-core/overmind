import { useMemo, useState } from "react";

import {
  ActivityTimeline,
  type AgentActivityPart,
  ASKING_OVERMIND_LABEL,
  buildTimelineSteps,
} from "@/components/agent-activity/activity-timeline";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

export function TurnSteps({
  parts,
  isStreaming,
  turnMs,
  defaultOpen = true,
}: {
  parts: AgentActivityPart[];
  isStreaming: boolean;
  turnMs?: number;
  /** Whether a streaming turn shows its rail open before the user toggles it. */
  defaultOpen?: boolean;
}) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const steps = useMemo(() => buildTimelineSteps(parts), [parts]);
  if (steps.length === 0) return null;

  const open = userOpen ?? (defaultOpen && isStreaming);
  const stepCount = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  const failedCount = steps.filter((step) => step.kind === "tool" && step.ok === false).length;
  // Restored turns carry no stats — fall back to summed thinking durations.
  const thoughtMs = steps.reduce(
    (total, step) => total + (step.kind === "thinking" ? (step.durationMs ?? 0) : 0),
    0
  );
  const totalMs = turnMs ?? (thoughtMs > 0 ? thoughtMs : undefined);

  const activeStep = steps.filter((step) => step.status === "running").at(-1);
  const activeLabel =
    activeStep?.kind === "tool" ? activeStep.title : activeStep ? ASKING_OVERMIND_LABEL : null;
  const label = isStreaming
    ? open
      ? stepCount
      : (activeLabel ?? stepCount)
    : [
        `Ran ${stepCount}`,
        failedCount > 0 ? `${failedCount} failed` : null,
        totalMs != null ? `${(totalMs / 1000).toFixed(1)}s` : null,
      ]
        .filter(Boolean)
        .join(" · ");

  return (
    <div>
      <button
        aria-expanded={open}
        className="group flex items-center gap-1.5 py-0.5 text-left"
        onClick={() => setUserOpen(!open)}
        type="button"
      >
        <Icon.chevronRight
          aria-hidden="true"
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform duration-150 group-hover:text-foreground",
            open && "rotate-90"
          )}
        />
        <span
          className={cn(
            "pixel-label text-xs transition-colors group-hover:text-foreground",
            failedCount > 0 && !isStreaming ? "text-destructive" : "text-muted-foreground",
            isStreaming && !open && "motion-safe:animate-pulse"
          )}
        >
          {label}
        </span>
        {isStreaming ? <Spinner size="sm" /> : null}
      </button>
      <div
        className={cn(
          "grid motion-safe:transition-[grid-template-rows] motion-safe:duration-200 motion-safe:ease-out",
          open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
        )}
      >
        <div className="overflow-hidden">
          <div className="flex flex-col gap-1 pt-1.5">
            <ActivityTimeline isStreaming={isStreaming} parts={parts} />
          </div>
        </div>
      </div>
    </div>
  );
}
