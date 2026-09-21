import { type KeyboardEvent, memo, useEffect, useMemo, useRef, useState } from "react";

import type { AgentActivityPart } from "@/components/agent-activity/activity-timeline";
import { WorkshopActivity, WorkshopThinking } from "@/components/datasets/notebook/activity";
import { chatSections } from "@/components/datasets/notebook/chat-flow";
import { ProposalImpact } from "@/components/datasets/notebook/preparation";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon, type IconName } from "@/components/ui/icons";
import { MarkdownContent } from "@/components/ui/markdown";
import { Spinner } from "@/components/ui/spinner";
import type { ChatCellRef, ChatTurn, WorkshopProgress } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

type ChipState = "created" | "edited" | "failed" | "ran";

const CHIP_STYLE: Record<ChipState, string> = {
  created: "border-border/70 text-foreground",
  edited: "border-info/40 bg-info/10 text-foreground",
  failed: "border-destructive/40 bg-destructive/10 text-destructive",
  ran: "border-success/40 bg-success/10 text-foreground",
};

const CHIP_ICON: Record<ChipState, IconName> = {
  created: "add",
  edited: "edit",
  failed: "warning",
  ran: "success",
};

/** What the agent is doing right now, before its turn lands on the dataset. */
export interface LiveTurn {
  text: string;
  cells: ChatCellRef[];
  steps: AgentActivityPart[];
  progress?: WorkshopProgress;
}

/** The chip shows the cell as it is now: a proposal the user ran reads as ran. */
function chipState(ref: ChatCellRef, cell: Cell): ChipState {
  if (cell.state === "ok" && cell.fingerprint) return "ran";
  if (cell.state === "failed") return "failed";
  return ref.action === "edited" ? "edited" : "created";
}

function CellResult({
  ref: cellRef,
  cell,
  onSelect,
}: {
  ref: ChatCellRef;
  cell: Cell;
  onSelect: (id: string) => void;
}) {
  const state = chipState(cellRef, cell);
  const Glyph = Icon[CHIP_ICON[state]];
  const label = cell.title;
  return (
    <Button
      aria-label={[
        label,
        cell?.state === "ok" ? `${cell.rows ?? 0} rows` : "",
        cell?.version,
        state,
      ]
        .filter(Boolean)
        .join(" · ")}
      className={cn(
        "h-auto min-h-9 w-full justify-start gap-2 px-3 py-2 text-left text-sm",
        CHIP_STYLE[state],
        "hover:bg-accent/60"
      )}
      onClick={() => onSelect(cell.id)}
      title={state}
      type="button"
      variant="outline"
    >
      <Glyph aria-hidden className="size-4 shrink-0" />
      <span className="min-w-0 flex-1 whitespace-normal">{label}</span>
      {cell?.state === "ok" && (
        <span className="shrink-0 text-xs text-muted-foreground">
          {(cell.rows ?? 0).toLocaleString()} rows
        </span>
      )}
      {cell?.version && (
        <span className="shrink-0 font-mono text-xs text-muted-foreground">{cell.version}</span>
      )}
      <span className="sr-only">{state}</span>
    </Button>
  );
}

function ProposalCard({
  cell,
  busy,
  onAccept,
  onDiscard,
  fingerprint,
}: {
  cell: Cell;
  busy: boolean;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
  fingerprint?: string;
}) {
  const review = cell.review as {
    input_fingerprint?: string;
  } | null;
  const stale = !!review?.input_fingerprint && review.input_fingerprint !== fingerprint;
  return (
    <Card className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span>{cell.title}</span>
        <Badge variant="warning">{stale ? "Out of date" : "Needs review"}</Badge>
      </div>
      {cell.note && <p className="text-xs text-muted-foreground">{cell.note}</p>}
      <ProposalImpact cell={cell} />
      <p className="text-xs text-muted-foreground">
        {stale
          ? "The source version changed. Request a new proposal before applying it."
          : "Not applied. Review the changes before adding them to the dataset."}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy || stale} onClick={() => onAccept(cell.id)} size="xs">
          <Icon.success />
          Approve
        </Button>
        <Button disabled={busy} onClick={() => onDiscard(cell.id)} size="xs" variant="secondary">
          Deny
        </Button>
      </div>
    </Card>
  );
}

const Turn = memo(function Turn({
  turn,
  cellsById,
  busy,
  onSelect,
  live,
}: {
  turn: ChatTurn | LiveTurn;
  cellsById: Map<string, Cell>;
  busy: boolean;
  onSelect: (id: string) => void;
  live?: boolean;
}) {
  const isUser = "role" in turn && turn.role === "user";
  const error = "error" in turn ? turn.error : undefined;
  const steps = turn.steps ?? [];
  if (isUser) {
    return (
      <div className="flex justify-end">
        <p className="max-w-[90%] whitespace-pre-wrap break-words rounded-md border border-border bg-wash-raised px-3 py-2 text-sm leading-relaxed text-foreground">
          {turn.text}
        </p>
      </div>
    );
  }
  const chips = (turn.cells ?? []).filter((ref) => {
    const cell = cellsById.get(ref.id);
    return cell && cell.state !== "proposed";
  });
  const sections = chatSections(turn.text, steps, chips, !!live);
  const interrupted = "status" in turn && turn.status === "running" && !busy;
  const awaitingApproval = "status" in turn && turn.status === "awaiting_approval";
  if (
    !live &&
    !turn.text &&
    !steps.length &&
    !chips.length &&
    !error &&
    !interrupted &&
    !awaitingApproval
  )
    return null;
  return (
    <div className="flex flex-col gap-4 pb-4">
      {(error || interrupted) && <span className="text-xs text-warning">Incomplete</span>}
      {awaitingApproval && (
        <span className="text-xs text-warning" role="status">
          Awaiting approval
        </span>
      )}
      {sections.map((section, index) => (
        <div className="space-y-4" key={section.offset}>
          <WorkshopThinking
            live={!!live && index === sections.length - 1 && !section.text.trim()}
            parts={section.steps}
          />
          {section.cells.map((ref) => (
            <CellResult cell={cellsById.get(ref.id)!} key={ref.id} onSelect={onSelect} ref={ref} />
          ))}
          {section.text.trim() && (
            <MarkdownContent className="text-sm leading-relaxed text-foreground" dividers={false}>
              {section.text}
            </MarkdownContent>
          )}
        </div>
      ))}
      {live && <WorkshopActivity progress={turn.progress} />}
      {(error || interrupted) && (
        <Alert variant="destructive">
          <p>{error || "The request stopped before completion. Saved changes are retained."}</p>
        </Alert>
      )}
    </div>
  );
});

/** What holds the dataset before the agent's first event: a turn that has not
 *  been picked up yet is queued, not working. */
const WAITING: Record<string, string> = {
  diagnosing: "Queued",
  landing: "Landing",
  running: "Running",
};

export function DatasetChat({
  turns,
  live,
  cells,
  busy,
  state,
  error,
  onSend,
  onSelect,
  onAccept,
  onDiscard,
  initialRequest = "",
}: {
  turns: ChatTurn[];
  live: LiveTurn | null;
  cells: Cell[];
  busy: boolean;
  state: string;
  /** The dataset's own error, when its state is `error`. */
  error?: string;
  onSend: (message: string) => void;
  onSelect: (id: string) => void;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
  initialRequest?: string;
}) {
  const [draft, setDraft] = useState(initialRequest);
  const scrollRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const cellsById = useMemo(() => new Map(cells.map((c) => [c.id, c])), [cells]);
  const proposals = cells.filter((cell) => cell.state === "proposed");
  const fingerprint = cells.filter((cell) => cell.state !== "proposed").at(-1)?.fingerprint;
  const lastTurn = turns.at(-1);
  const runningIndex =
    lastTurn?.role === "agent" && lastTurn.status === "running" ? turns.length - 1 : -1;

  const contentRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  // The list follows its newest line until the reader scrolls away. Content
  // grows after mount (markdown, proposal cards), so size drives it, not turns.
  useEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content) return;
    const follow = () => {
      if (pinned.current) el.scrollTop = el.scrollHeight;
    };
    const onScroll = () => {
      pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    const observer = new ResizeObserver(follow);
    observer.observe(content);
    el.addEventListener("scroll", onScroll, { passive: true });
    follow();
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, []);

  const send = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };
  const turnProps = { busy, cellsById, onSelect };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto" ref={scrollRef}>
        <div className="flex flex-col gap-4 px-3 py-3" ref={contentRef}>
          {turns.length === 0 && !live && (
            <p className="text-xs text-muted-foreground">The agent starts when the source lands.</p>
          )}
          {turns.map((turn, i) => (
            <Turn
              key={turn.id ?? `${turn.at}-${i}`}
              live={busy && i === runningIndex}
              turn={
                i === runningIndex && live
                  ? {
                      ...turn,
                      ...live,
                      cells: live.cells.length ? live.cells : turn.cells,
                      progress: live.progress ?? turn.progress,
                      steps: live.steps.length ? live.steps : turn.steps,
                    }
                  : turn
              }
              {...turnProps}
            />
          ))}
          {live && runningIndex < 0 && busy && <Turn live turn={live} {...turnProps} />}
          {busy && !live && runningIndex < 0 && (
            <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Spinner className="size-3" />
              {WAITING[state] ?? "Working"}
            </p>
          )}
          {proposals.length > 0 && (
            <section aria-label="Proposed changes" className="space-y-3">
              <p className="text-xs text-muted-foreground">
                {busy ? "Draft changes" : "Review changes"} · {proposals.length}
              </p>
              {proposals.map((cell) => (
                <ProposalCard
                  busy={busy}
                  cell={cell}
                  fingerprint={fingerprint}
                  key={cell.id}
                  onAccept={onAccept}
                  onDiscard={onDiscard}
                />
              ))}
            </section>
          )}
        </div>
      </div>
      <div className="shrink-0 px-3 py-3">
        {error && (
          <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-xs text-destructive">
            {error}
          </pre>
        )}
        <Card className="p-2">
          <textarea
            aria-label="Message the agent"
            className="block max-h-40 min-h-12 w-full resize-none bg-transparent text-sm outline-none field-sizing-content placeholder:text-muted-foreground"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={
              busy
                ? "Draft your next message…"
                : "Ask about the data, make a change or generate examples…"
            }
            ref={composerRef}
            rows={2}
            value={draft}
          />
          <div className="mt-2 flex justify-end">
            <Button
              aria-label="Send"
              disabled={busy || !draft.trim()}
              onClick={send}
              size="icon-sm"
              variant="secondary"
            >
              {busy ? <Spinner className="size-3.5" /> : <Icon.send className="size-3.5" />}
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
