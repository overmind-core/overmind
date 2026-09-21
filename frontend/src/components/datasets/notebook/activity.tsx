import { useEffect, useId, useMemo, useState } from "react";

import {
  type AgentActivityPart,
  buildTimelineSteps,
  type TimelineStep,
  TOOL_ICONS,
  toolDetail,
} from "@/components/agent-activity/activity-timeline";
import { type ElbowItem, ElbowList } from "@/components/ui/elbow-list";
import { Icon } from "@/components/ui/icons";
import { MarkdownContent } from "@/components/ui/markdown";
import { Progress } from "@/components/ui/progress";
import { Spinner } from "@/components/ui/spinner";
import type { WorkshopProgress } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";

export function elapsedLabel(ms: number) {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function useNow() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function thinkingItem(step: TimelineStep, live: boolean): ElbowItem {
  const running = live && step.status === "running";
  if (step.kind === "thinking") {
    return {
      defaultOpen: true,
      detail: step.text ? (
        <MarkdownContent
          className="text-sm leading-relaxed text-muted-foreground"
          compact
          dividers={false}
        >
          {step.text}
        </MarkdownContent>
      ) : undefined,
      icon: "overmind",
      id: step.id,
      label: running
        ? "Thinking"
        : step.durationMs !== undefined
          ? `Thought for ${elapsedLabel(step.durationMs)}`
          : "Thinking step",
      running,
    };
  }
  return {
    defaultOpen: step.ok === false,
    detail: toolDetail(step),
    failed: step.ok === false,
    icon: TOOL_ICONS[step.tool] ?? "tool",
    id: step.id,
    label: step.title,
    running,
    trailing: running ? <Spinner size="sm" /> : undefined,
  };
}

export function WorkshopThinking({ parts, live }: { parts: AgentActivityPart[]; live: boolean }) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null);
  const steps = useMemo(
    () =>
      buildTimelineSteps(parts).filter(
        (step) =>
          step.kind !== "thinking" ||
          step.status === "running" ||
          !!step.text ||
          (step.durationMs ?? 0) >= 100
      ),
    [parts]
  );
  const items = useMemo(() => steps.map((step) => thinkingItem(step, live)), [steps, live]);
  const contentId = useId();
  if (!live && steps.length === 0) return null;
  const open = userOpen ?? live;
  const current = steps.at(-1);
  const elapsed = steps.reduce(
    (total, step) => total + (step.kind === "thinking" ? (step.durationMs ?? 0) : 0),
    0
  );
  const label = live
    ? current?.kind === "tool" && current.status === "running"
      ? current.title
      : "Thinking…"
    : elapsed > 0
      ? `Thought for ${elapsedLabel(elapsed)}`
      : "View steps";
  return (
    <section aria-label="Thinking and steps" className="space-y-3">
      <button
        aria-controls={contentId}
        aria-expanded={open}
        className="flex items-center gap-2 py-1 text-left text-sm text-muted-foreground hover:text-foreground"
        onClick={() => setUserOpen(!open)}
        type="button"
      >
        {live && <Spinner size="sm" />}
        <span>{label}</span>
        <Icon.chevronRight
          aria-hidden
          className={cn("size-3 transition-transform duration-150", open && "rotate-90")}
        />
      </button>
      <div hidden={!open} id={contentId}>
        <ElbowList ariaLabel="Thinking steps" isStreaming={live} items={items} />
      </div>
    </section>
  );
}

export function WorkshopActivity({ progress }: { progress?: WorkshopProgress }) {
  const now = useNow();
  const requested = (progress?.target_rows ?? 0) - (progress?.rows_before ?? 0);
  const generated = progress?.generated_rows ?? 0;
  const quietMs = progress?.updated_at ? now - Date.parse(progress.updated_at) : 0;
  if (requested <= 0 && quietMs < 30_000) return null;
  return (
    <div aria-label="Workshop activity" className="space-y-3">
      {requested > 0 && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span>
              {generated.toLocaleString()} of {requested.toLocaleString()} rows added
            </span>
          </div>
          <Progress label="Generated rows added" percent={(generated / requested) * 100} />
        </div>
      )}
      {quietMs >= 30_000 && (
        <p className="text-xs text-muted-foreground" role="status">
          No new activity for {elapsedLabel(quietMs)}.
        </p>
      )}
    </div>
  );
}
