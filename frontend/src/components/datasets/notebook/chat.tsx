import { type KeyboardEvent, memo, useEffect, useMemo, useRef, useState } from "react";

import type { AgentActivityPart } from "@/components/agent-activity/activity-timeline";
import { TurnSteps } from "@/components/agent-activity/turn-steps";
import { Button } from "@/components/ui/button";
import { Icon, type IconName } from "@/components/ui/icons";
import { MarkdownContent } from "@/components/ui/markdown";
import { Spinner } from "@/components/ui/spinner";
import type { ChatCellRef, ChatTurn } from "@/hooks/use-datasets";
import { cn } from "@/lib/utils";
import type { Cell } from "@/openapi";

type ChipState = "created" | "edited" | "failed" | "ran" | "removed" | "discarded";

const CHIP_STYLE: Record<ChipState, string> = {
  created: "border-border/70 text-foreground",
  discarded: "border-dashed border-border/70 text-muted-foreground line-through",
  edited: "border-info/40 bg-info/10 text-foreground",
  failed: "border-destructive/40 bg-destructive/10 text-destructive",
  ran: "border-success/40 bg-success/10 text-foreground",
  removed: "border-border/70 text-muted-foreground line-through",
};

const CHIP_ICON: Record<ChipState, IconName> = {
  created: "add",
  discarded: "close",
  edited: "edit",
  failed: "warning",
  ran: "success",
  removed: "close",
};

/** One chip per cell: the last thing the turn did to it. */
function collapse(refs: ChatCellRef[]): ChatCellRef[] {
  const last = new Map<string, ChatCellRef>();
  for (const ref of refs) last.set(ref.id, ref);
  return [...last.values()];
}

/** What the agent is doing right now, before its turn lands on the dataset. */
export interface LiveTurn {
  text: string;
  cells: ChatCellRef[];
  steps: AgentActivityPart[];
}

/** The chip shows the cell as it is now: a proposal the user ran reads as ran. */
function chipState(ref: ChatCellRef, cell: Cell | undefined): ChipState {
  if (!cell || ref.action === "removed") return ref.action === "proposed" ? "discarded" : "removed";
  if (cell.state === "ok" && cell.fingerprint) return "ran";
  if (cell.state === "failed") return "failed";
  return ref.action === "edited" ? "edited" : "created";
}

function CellChip({
  ref: cellRef,
  cell,
  onSelect,
}: {
  ref: ChatCellRef;
  cell: Cell | undefined;
  onSelect: (id: string) => void;
}) {
  const state = chipState(cellRef, cell);
  const gone = state === "removed" || state === "discarded";
  const Glyph = Icon[CHIP_ICON[state]];
  const label = cell ? `${cell.version} ${cell.title}` : `${state} cell`;
  return (
    <button
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1 rounded-sm border px-1.5 font-mono text-xs",
        CHIP_STYLE[state],
        !gone && "hover:bg-accent/60"
      )}
      disabled={gone}
      onClick={() => cell && onSelect(cell.id)}
      title={state}
      type="button"
    >
      <Glyph aria-hidden className="size-3 shrink-0" />
      <span className="truncate">{label}</span>
      <span className="sr-only">{state}</span>
    </button>
  );
}

/** A proposal waits here, not in the notebook: Run lands and runs it, Discard drops it. */
function ProposalCard({
  cell,
  busy,
  onAccept,
  onDiscard,
}: {
  cell: Cell;
  busy: boolean;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-md border border-dashed border-border bg-card p-2.5">
      <div className="flex items-center gap-1.5 text-xs">
        <Icon.help aria-hidden className="size-3 shrink-0 text-muted-foreground" />
        <span className="pixel-label text-muted-foreground">Proposal</span>
        <span className="truncate text-foreground">{cell.title}</span>
      </div>
      {cell.note && <p className="text-xs text-muted-foreground">{cell.note}</p>}
      <div className="flex items-center gap-1">
        <Button disabled={busy} onClick={() => onAccept(cell.id)} size="xs">
          <Icon.play />
          Run
        </Button>
        <Button disabled={busy} onClick={() => onDiscard(cell.id)} size="xs" variant="secondary">
          Discard
        </Button>
      </div>
    </div>
  );
}

const Turn = memo(function Turn({
  turn,
  cellsById,
  busy,
  onSelect,
  onAccept,
  onDiscard,
  live,
}: {
  turn: ChatTurn | LiveTurn;
  cellsById: Map<string, Cell>;
  busy: boolean;
  onSelect: (id: string) => void;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
  live?: boolean;
}) {
  const isUser = "role" in turn && turn.role === "user";
  const error = "error" in turn ? turn.error : undefined;
  const steps = turn.steps ?? [];
  const ms = "ms" in turn ? turn.ms : undefined;
  if (isUser) {
    return (
      <div className="flex justify-end">
        <p className="max-w-[80%] whitespace-pre-wrap break-words rounded-md border border-border bg-secondary/60 px-3 py-1.5 text-sm leading-relaxed text-foreground">
          {turn.text}
        </p>
      </div>
    );
  }
  const refs = collapse(turn.cells ?? []);
  const proposals = refs
    .map((ref) => cellsById.get(ref.id))
    .filter((cell): cell is Cell => !!cell && cell.state === "proposed");
  const chips = refs.filter((ref) => cellsById.get(ref.id)?.state !== "proposed");
  return (
    <div className="flex flex-col gap-1.5">
      <TurnSteps defaultOpen={false} isStreaming={!!live} parts={steps} turnMs={ms} />
      {turn.text ? (
        <MarkdownContent className="text-sm text-foreground" compact>
          {turn.text}
        </MarkdownContent>
      ) : null}
      {chips.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {chips.map((ref) => (
            <CellChip cell={cellsById.get(ref.id)} key={ref.id} onSelect={onSelect} ref={ref} />
          ))}
        </div>
      )}
      {proposals.map((cell) => (
        <ProposalCard
          busy={busy}
          cell={cell}
          key={cell.id}
          onAccept={onAccept}
          onDiscard={onDiscard}
        />
      ))}
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
});

export function DatasetChat({
  turns,
  live,
  cells,
  busy,
  error,
  onSend,
  onSelect,
  onAccept,
  onDiscard,
}: {
  turns: ChatTurn[];
  live: LiveTurn | null;
  cells: Cell[];
  busy: boolean;
  /** The dataset's own error, when its state is `error`. */
  error?: string;
  onSend: (message: string) => void;
  onSelect: (id: string) => void;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const cellsById = useMemo(() => new Map(cells.map((c) => [c.id, c])), [cells]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll to the newest text
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [turns.length, live?.text, live?.cells.length, live?.steps.length]);

  const send = () => {
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };
  const turnProps = { busy, cellsById, onAccept, onDiscard, onSelect };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-3" ref={scrollRef}>
        {turns.length === 0 && !live && (
          <p className="text-xs text-muted-foreground">The agent starts when the source lands.</p>
        )}
        {turns.map((turn, i) => (
          <Turn key={`${turn.at}-${i}`} turn={turn} {...turnProps} />
        ))}
        {live && <Turn live turn={live} {...turnProps} />}
        {busy && !live && (
          <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Spinner className="size-3" />
            Working
          </p>
        )}
      </div>
      <div className="shrink-0 border-t border-border/70 px-3 py-3">
        {error && (
          <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-xs text-destructive">
            {error}
          </pre>
        )}
        <div className="rounded-md border border-border bg-card p-2">
          <textarea
            aria-label="Message the agent"
            className="block max-h-40 min-h-12 w-full resize-none bg-transparent text-sm outline-none field-sizing-content placeholder:text-muted-foreground"
            disabled={busy}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={busy ? "Working…" : "Ask a data question or change something…"}
            rows={2}
            value={draft}
          />
          <div className="mt-1 flex justify-end">
            <Button
              aria-label="Send"
              disabled={busy || !draft.trim()}
              onClick={send}
              size="icon-sm"
              variant="secondary"
            >
              {busy ? <Spinner className="size-3.5" /> : <Icon.arrowUp className="size-3.5" />}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
