import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ScriptCode } from "@/components/datasets/notebook/script-code";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon } from "@/components/ui/icons";
import { MarkdownContent } from "@/components/ui/markdown";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { writeClipboardText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";
import { AssistantResult, type ResultActions } from "./assistant-result";
import type { AssistantEvent, Draft, Presentation, ToolCall } from "./types";

type Result = Extract<AssistantEvent, { kind: "tool_result" }>["payload"];
type Status = Extract<AssistantEvent, { kind: "status" }>["payload"];
type CallState = "Running" | "Done" | "Failed" | "Interrupted";

interface Step {
  call: ToolCall;
  state: CallState;
  result?: Result;
}

type Segment =
  | { kind: "text"; key: string; text: string }
  | { kind: "tools"; key: string; steps: Step[] }
  | { kind: "result"; key: string; call: string; presentation: Presentation }
  | { kind: "status"; key: string; status: Status }
  | { kind: "note"; key: string; text: string };

type Turn =
  | { kind: "user"; event: Extract<AssistantEvent, { kind: "user" }> }
  | { kind: "run"; key: number; segments: Segment[] };

/** A run is everything the assistant did between two user messages. Tool calls of
 *  consecutive replies form one group until text or a result separates them. A call with
 *  no result is running only while it is the thread's newest work; otherwise it was
 *  abandoned. */
export function turnsOf(events: AssistantEvent[], active: boolean): Turn[] {
  const turns: Turn[] = [];
  const results = new Map<string, Extract<AssistantEvent, { kind: "tool_result" }>>();
  for (const event of events)
    if (event.kind === "tool_result") results.set(event.payload.call, event);
  const lastReply = [...events].reverse().find((event) => event.kind === "assistant");
  const closed = events.at(-1)?.kind === "status" || events.at(-1)?.kind === "user";
  for (const event of events) {
    if (event.kind === "user") {
      turns.push({ event, kind: "user" });
      continue;
    }
    if (event.kind === "tool_result") continue;
    let run = turns.at(-1);
    if (run?.kind !== "run") {
      run = { key: event.sequence, kind: "run", segments: [] };
      turns.push(run);
    }
    const { segments } = run;
    const key = String(event.sequence);
    if (event.kind === "status") {
      segments.push({ key, kind: "status", status: event.payload });
      continue;
    }
    if (event.kind === "summary") {
      segments.push({ key, kind: "note", text: "Earlier conversation summarized" });
      continue;
    }
    if (event.payload.text) segments.push({ key, kind: "text", text: event.payload.text });
    for (const call of event.payload.calls) {
      const answer = results.get(call.id);
      const result = answer?.payload;
      const state: CallState = result
        ? result.ok
          ? "Done"
          : "Failed"
        : active && event === lastReply && !closed
          ? "Running"
          : "Interrupted";
      const group = segments.at(-1);
      if (group?.kind === "tools") group.steps.push({ call, result, state });
      else segments.push({ key: call.id, kind: "tools", steps: [{ call, result, state }] });
      if (answer?.presentation)
        segments.push({
          call: call.id,
          key: `${call.id}:result`,
          kind: "result",
          presentation: answer.presentation,
        });
    }
  }
  return turns;
}

function argumentsOf(call: ToolCall): Record<string, unknown> {
  try {
    const parsed = JSON.parse(call.raw);
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

/** One line for a call: its code's first line, its query, or its first text argument. */
function summaryOf(call: ToolCall): string {
  const args = argumentsOf(call);
  const text = [args.code, args.sql, args.name, args.dataset, args.cell, args.pipeline].find(
    (value): value is string => typeof value === "string" && value.trim() !== ""
  );
  return (
    text
      ?.split("\n")
      .find((line) => line.trim())
      ?.trim() ?? ""
  );
}

function errorOf(result: Result | undefined): string {
  if (!result || result.ok) return "";
  const content = result.content as { error?: unknown } | null;
  const error = content?.error;
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && "message" in error) return String(error.message);
  return "The call failed.";
}

function ToolRow({ step }: { step: Step }) {
  const { call, result, state } = step;
  const python = call.name === "run_python";
  const ToolIcon = python ? Icon.python : call.name === "present" ? Icon.chart : Icon.terminal;
  const args = argumentsOf(call);
  const code = python ? String(args.code ?? "") : JSON.stringify(args, null, 2);
  const output = python ? String((result?.content as { output?: string })?.output ?? "") : "";
  const failure = errorOf(result);
  const hasDetails = call.name !== "present" && code.trim() !== "{}";
  const Trigger = hasDetails ? CollapsibleTrigger : "div";
  return (
    <Collapsible className="assistant-tool-row group/tool">
      <Trigger className="flex w-full min-w-0 items-center gap-2 py-1.5 text-left text-xs text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60">
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="flex shrink-0">
              <ToolIcon
                className={cn("size-4 shrink-0", call.name === "present" && "text-cat-4")}
              />
            </span>
          </TooltipTrigger>
          <TooltipContent>{python ? "Python" : call.name}</TooltipContent>
        </Tooltip>
        <span className={python ? "sr-only" : "assistant-tool-name shrink-0 font-mono"}>
          {call.name}
        </span>
        <span className="min-w-0 truncate">{summaryOf(call)}</span>
        <span
          className={cn(
            "sr-only",
            state === "Failed" && "not-sr-only text-error",
            state === "Interrupted" && "not-sr-only text-warning"
          )}
        >
          {state}
        </span>
        {hasDetails && (
          <Icon.chevronRight className="size-3 shrink-0 transition-transform group-data-[state=open]/tool:rotate-90" />
        )}
      </Trigger>
      {hasDetails && (
        <CollapsibleContent className="flex flex-col gap-2 pt-1 pb-2 ps-6">
          <ScriptCode kind={python ? "python" : "shell"} source={code} />
          {output && (
            <pre className="max-h-64 overflow-auto rounded-sm bg-wash-raised p-2 text-xs leading-relaxed">
              {output}
            </pre>
          )}
          {failure && <p className="break-words text-xs text-error">{failure}</p>}
        </CollapsibleContent>
      )}
    </Collapsible>
  );
}

function ToolGroup({ steps, active }: { steps: Step[]; active: boolean }) {
  const running = steps.some((step) => step.state === "Running");
  const failed = steps.some((step) => step.state === "Failed");
  const [open, setOpen] = useState<boolean>();
  const label = `${steps.length} tool ${steps.length === 1 ? "call" : "calls"}`;
  const status = running
    ? "Running"
    : failed
      ? "Failed"
      : steps.some((step) => step.state === "Interrupted")
        ? "Interrupted"
        : "Done";
  return (
    <Collapsible onOpenChange={setOpen} open={open ?? (running || failed || active)}>
      <CollapsibleTrigger
        aria-label={`${label} ${status}`}
        className="group/trigger flex items-center gap-2 py-1.5 text-xs leading-none text-muted-foreground transition-colors hover:text-foreground"
      >
        {running && <Spinner size="sm" />}
        <span className="assistant-tool-count">{label}</span>
        <Icon.chevronDown className="size-3 shrink-0 -rotate-90 transition-transform group-data-[state=open]/trigger:rotate-0 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      <CollapsibleContent className="ps-4">
        {steps.map((step) => (
          <ToolRow key={step.call.id} step={step} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

const STATUS_NOTE = {
  failed: "text-error",
  interrupted: "text-warning",
  stopped: "text-muted-foreground",
};

export function AssistantTranscript({
  events,
  draft,
  active,
  activity,
  cells,
  actions,
  onSelectCell,
}: {
  events: AssistantEvent[];
  draft: Draft | null;
  active: boolean;
  /** Shown while the run works and nothing else on screen says so. */
  activity?: string;
  cells: Cell[];
  actions: ResultActions;
  onSelectCell: (id: string) => void;
}) {
  const turns = useMemo(() => turnsOf(events, active), [events, active]);
  const viewport = useRef<HTMLDivElement>(null);
  const [atEnd, setAtEnd] = useState(true);
  const follow = useRef(true);
  const scrollToEnd = useCallback(
    () => viewport.current?.scrollTo({ top: viewport.current.scrollHeight }),
    []
  );
  // Follows new content only while the reader is at the end.
  // biome-ignore lint/correctness/useExhaustiveDependencies: scrolls when the content changes
  useEffect(() => {
    if (follow.current) scrollToEnd();
  }, [events, draft, scrollToEnd]);

  return (
    <div className="assistant-history relative flex min-h-0 flex-1 flex-col">
      <div
        className="assistant-conversation min-h-0 flex-1 overflow-y-auto overscroll-contain"
        onScroll={(event) => {
          const node = event.currentTarget;
          follow.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
          setAtEnd(follow.current);
        }}
        ref={viewport}
      >
        {turns.map((turn) =>
          turn.kind === "user" ? (
            <article
              aria-label="Your message"
              className="assistant-message min-w-0"
              data-role="user"
              key={turn.event.sequence}
            >
              <div className="assistant-user-prompt text-sm leading-relaxed">
                {turn.event.payload.text}
              </div>
              {turn.event.payload.cells.length > 0 && (
                <div className="mt-1 flex flex-wrap justify-end gap-x-3 gap-y-1">
                  {turn.event.payload.cells.map((id) => {
                    const cell = cells.find((item) => item.id === id);
                    return (
                      <Button
                        className="max-w-full justify-start text-muted-foreground"
                        disabled={!cell}
                        key={id}
                        onClick={() => onSelectCell(id)}
                        size="xs"
                        variant="secondary"
                      >
                        <Icon.dataset className="size-3 text-cat-2" />
                        <span className="truncate">{cell?.title ?? "Dataset version"}</span>
                      </Button>
                    );
                  })}
                </div>
              )}
            </article>
          ) : (
            <RunTurn
              actions={actions}
              key={turn.key}
              last={turn === turns.at(-1) && active}
              segments={turn.segments}
            />
          )
        )}
        {draft && (draft.text || draft.tools.length > 0) && (
          <article aria-label="Overmind response" className="assistant-message min-w-0">
            <div className="flex min-w-0 flex-col gap-4">
              {draft.text && (
                <MarkdownContent className="break-words">{draft.text}</MarkdownContent>
              )}
              {draft.tools.length > 0 && (
                <p className="flex items-center gap-2 py-1.5 text-xs text-muted-foreground">
                  <Spinner size="sm" />
                  <span className="assistant-tool-name font-mono">{draft.tools.at(-1)}</span>
                  Preparing
                </p>
              )}
            </div>
          </article>
        )}
        {activity && (
          <div
            aria-live="polite"
            className="flex items-center gap-2 px-3 py-3 text-xs text-muted-foreground"
            role="status"
          >
            <Spinner size="sm" />
            <span>{activity}</span>
          </div>
        )}
        {!turns.length && !draft && !activity && (
          <p className="py-2 text-xs text-muted-foreground">No messages</p>
        )}
      </div>
      {!atEnd && (
        <Button
          aria-label="Scroll to latest message"
          className="absolute right-3 bottom-2 z-10 rounded-md border border-border bg-popover"
          onClick={() => {
            follow.current = true;
            scrollToEnd();
          }}
          size="icon-xs"
          variant="ghost"
        >
          <Icon.arrowDown />
        </Button>
      )}
    </div>
  );
}

function RunTurn({
  segments,
  last,
  actions,
}: {
  segments: Segment[];
  last: boolean;
  actions: ResultActions;
}) {
  const text = segments
    .flatMap((segment) => (segment.kind === "text" ? [segment.text] : []))
    .join("\n\n");
  return (
    <article aria-label="Overmind response" className="assistant-message min-w-0">
      <div className="flex min-w-0 flex-col gap-4">
        {segments.map((segment) =>
          segment.kind === "text" ? (
            <MarkdownContent className="break-words" key={segment.key}>
              {segment.text}
            </MarkdownContent>
          ) : segment.kind === "tools" ? (
            <ToolGroup
              active={last && segment === segments.at(-1)}
              key={segment.key}
              steps={segment.steps}
            />
          ) : segment.kind === "result" ? (
            <AssistantResult
              actions={actions}
              call={segment.call}
              key={segment.key}
              presentation={segment.presentation}
            />
          ) : segment.kind === "note" ? (
            <p className="text-xs text-muted-foreground" key={segment.key}>
              {segment.text}
            </p>
          ) : (
            <p className={cn("text-xs", STATUS_NOTE[segment.status.state])} key={segment.key}>
              {segment.status.detail}
            </p>
          )
        )}
      </div>
      {text && !last && (
        <div className="mt-2 flex items-center gap-1 text-muted-foreground">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="Copy response"
                onClick={() => void writeClipboardText(text)}
                size="icon-xs"
                variant="ghost"
              >
                <Icon.copy />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Copy response</TooltipContent>
          </Tooltip>
        </div>
      )}
    </article>
  );
}
