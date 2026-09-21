import { useMemo } from "react";

import { formatPayload } from "@/components/evaluations/payload-format";
import { type ElbowItem, ElbowList, elbowHeightPx } from "@/components/ui/elbow-list";
import type { IconName } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export { elbowHeightPx };

export const ASKING_OVERMIND_LABEL = "Asking Overmind";

export interface AgentActivityPart {
  type: "activity";
  phase: "thinking" | "tool_start" | "tool_done";
  id: string;
  status?: "running" | "done";
  durationMs?: number;
  duration_ms?: number;
  tool?: string;
  title?: string;
  summary?: string;
  preview?: string;
  ok?: boolean;
  text?: string;
  text_offset?: number;
}

export type TimelineStep =
  | {
      kind: "thinking";
      id: string;
      status: "running" | "done";
      durationMs?: number;
      text?: string;
    }
  | {
      kind: "tool";
      id: string;
      tool: string;
      title: string;
      summary?: string;
      status: "running" | "done";
      preview?: string;
      ok?: boolean;
    };

export function buildTimelineSteps(parts: AgentActivityPart[]): TimelineStep[] {
  const steps: TimelineStep[] = [];
  const toolById = new Map<string, Extract<TimelineStep, { kind: "tool" }>>();

  for (const part of parts) {
    if (part.phase === "thinking") {
      const existing = steps.find(
        (step): step is Extract<TimelineStep, { kind: "thinking" }> =>
          step.kind === "thinking" && step.id === part.id
      );
      const durationMs = part.durationMs ?? part.duration_ms;
      if (existing) {
        existing.status = part.status === "done" ? "done" : existing.status;
        if (durationMs != null) existing.durationMs = durationMs;
        if (part.text) {
          existing.text = part.status === "done" ? part.text : (existing.text ?? "") + part.text;
        }
      } else {
        steps.push({
          durationMs,
          id: part.id,
          kind: "thinking",
          status: part.status === "done" ? "done" : "running",
          text: part.text,
        });
      }
      continue;
    }

    if (part.phase === "tool_start") {
      const step: Extract<TimelineStep, { kind: "tool" }> = {
        id: part.id,
        kind: "tool",
        ok: undefined,
        preview: undefined,
        status: "running",
        summary: part.summary,
        title: part.title ?? part.tool ?? "Tool",
        tool: part.tool ?? "tool",
      };
      toolById.set(part.id, step);
      steps.push(step);
      continue;
    }

    if (part.phase === "tool_done") {
      const step = toolById.get(part.id);
      if (step) {
        step.status = "done";
        step.preview = part.preview;
        step.ok = part.ok;
      } else {
        steps.push({
          id: part.id,
          kind: "tool",
          ok: part.ok,
          preview: part.preview,
          status: "done",
          title: part.tool ?? "Tool",
          tool: part.tool ?? "tool",
        });
      }
    }
  }

  return steps;
}

function formatThoughtDuration(ms?: number): string {
  if (ms == null) return ASKING_OVERMIND_LABEL;
  const seconds = Math.max(1, Math.round(ms / 1000));
  return `Thought for ${seconds}s`;
}

export const TOOL_ICONS: Record<string, IconName> = {
  add_cell: "code",
  diff: "diff",
  edit_cell: "code",
  inspect: "search",
  install: "tool",
  notebook_cell: "code",
  notebook_read: "code",
  notebook_write: "code",
  query: "search",
  set_intent: "target",
  status: "dataset",
  try_script: "code",
};

const stepIconName = (step: TimelineStep): IconName =>
  step.kind === "thinking" ? "overmind" : (TOOL_ICONS[step.tool] ?? "tool");

function IoBlock({ caption, text, failed }: { caption: string; text: string; failed?: boolean }) {
  return (
    <div className="space-y-0.5">
      <span className="text-xs font-medium text-muted-foreground">{caption}</span>
      <pre
        className={cn(
          "max-h-32 overflow-auto whitespace-pre-wrap font-mono text-xs leading-relaxed text-muted-foreground",
          failed && "text-destructive/90"
        )}
      >
        {text}
      </pre>
    </div>
  );
}

export function toolDetail(step: {
  summary?: string;
  preview?: string;
  ok?: boolean;
  input?: unknown;
  output?: unknown;
}) {
  const inn = formatPayload(step.input) || formatPayload(step.summary);
  const out = formatPayload(step.output) || formatPayload(step.preview);
  if (!inn && !out) return undefined;
  return (
    <div className="space-y-1.5">
      {inn ? <IoBlock caption="In" text={inn} /> : null}
      {out ? <IoBlock caption="Out" failed={step.ok === false} text={out} /> : null}
    </div>
  );
}

function thoughtDetail(text: string) {
  return (
    <div className="space-y-1.5">
      <IoBlock caption="Thought" text={text} />
    </div>
  );
}

function timelineItem(step: TimelineStep): ElbowItem {
  const running = step.status === "running";
  return {
    detail:
      step.kind === "tool" ? toolDetail(step) : step.text ? thoughtDetail(step.text) : undefined,
    failed: step.kind === "tool" && step.ok === false,
    icon: stepIconName(step),
    id: step.id,
    label:
      step.kind === "tool"
        ? step.title
        : running
          ? ASKING_OVERMIND_LABEL
          : formatThoughtDuration(step.durationMs),
    running,
  };
}

export function ActivityTimeline({
  parts,
  isStreaming = false,
  className,
}: {
  parts: AgentActivityPart[];
  isStreaming?: boolean;
  className?: string;
}) {
  const steps = useMemo(() => buildTimelineSteps(parts), [parts]);
  const items = useMemo(() => steps.map(timelineItem), [steps]);
  return <ElbowList className={className} isStreaming={isStreaming} items={items} />;
}
