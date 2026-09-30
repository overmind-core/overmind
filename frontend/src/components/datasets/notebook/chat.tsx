import {
  type KeyboardEvent,
  memo,
  type ReactNode,
  type RefObject,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { AgentActivityPart } from "@/components/agent-activity/activity-timeline";
import { Attachment } from "@/components/datasets/attachment";
import { WorkshopActivity, WorkshopThinking } from "@/components/datasets/notebook/activity";
import { chatSections, notebookFlow } from "@/components/datasets/notebook/chat-flow";
import { ProposalImpact } from "@/components/datasets/notebook/preparation";
import { WorkshopStatusIcon } from "@/components/datasets/workshop-status";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Icon, type IconName } from "@/components/ui/icons";
import { MarkdownContent } from "@/components/ui/markdown";
import type { ChatCellRef, ChatTurn, WorkshopProgress } from "@/hooks/use-datasets";
import { useDatasetUploads } from "@/hooks/use-uploads";
import { errorMessage } from "@/lib/notify";
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
        "h-auto min-h-7 w-fit max-w-full justify-start gap-2 px-2 py-1 text-left text-xs",
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

function ProposalRow({
  cell,
  busy,
  onAccept,
  onDiscard,
  open,
  onOpenChange,
}: {
  cell: Cell;
  busy: boolean;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Collapsible asChild onOpenChange={onOpenChange} open={open}>
      <li>
        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-x-3 gap-y-1 bg-popover px-3 py-2">
          <CollapsibleTrigger asChild>
            <button
              aria-label={`Review ${cell.title}`}
              className="flex min-w-0 flex-1 basis-40 items-center gap-2 rounded-sm py-1 text-left outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
              type="button"
            >
              <WorkshopStatusIcon className="size-3.5 shrink-0" state="review" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm">{cell.title}</span>
                {cell.note && cell.note !== cell.title && (
                  <span className="block truncate text-xs text-muted-foreground">{cell.note}</span>
                )}
              </span>
              <Icon.chevronUp
                className={cn(
                  "size-3 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none",
                  open && "rotate-180"
                )}
              />
            </button>
          </CollapsibleTrigger>
          <div className="ml-auto flex shrink-0 items-center gap-1.5">
            <Button disabled={busy} onClick={() => onAccept(cell.id)} size="xs">
              <Icon.success />
              Approve
            </Button>
            <Button
              disabled={busy}
              onClick={() => onDiscard(cell.id)}
              size="xs"
              variant="secondary"
            >
              Reject
            </Button>
          </div>
        </div>
        <CollapsibleContent className="px-3 pb-3">
          <div className="space-y-3 border-t border-border/70 pt-3">
            {cell.note && <p className="whitespace-pre-wrap break-words text-sm">{cell.note}</p>}
            <ProposalImpact cell={cell} />
            <p className="text-xs text-muted-foreground">Not applied.</p>
          </div>
        </CollapsibleContent>
      </li>
    </Collapsible>
  );
}

const Turn = memo(function Turn({
  turn,
  cellsById,
  busy,
  onSelect,
  live,
  expanded,
}: {
  turn: ChatTurn | LiveTurn;
  cellsById: Map<string, Cell>;
  busy: boolean;
  onSelect: (id: string) => void;
  live?: boolean;
  expanded: boolean;
}) {
  const [override, setOverride] = useState<{ basis: boolean; value: boolean } | null>(null);
  const open = override?.basis === expanded ? override.value : expanded;
  const isUser = "role" in turn && turn.role === "user";
  const error = "error" in turn ? turn.error : undefined;
  const steps = turn.steps ?? [];
  if (isUser) {
    return (
      <div className="mx-auto flex w-full max-w-4xl justify-end px-3 py-2">
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
  const summaryIndex = sections.reduce(
    (last, section, index) => (section.text.trim() ? index : last),
    0
  );
  const interrupted = "status" in turn && turn.status === "running" && !busy;
  const awaitingApproval = "status" in turn && turn.status === "awaiting_approval";
  const label = open
    ? "Agent response"
    : sections[summaryIndex].text
        .split("\n")
        .map((line) => line.trim())
        .find((line) => line && !/^#+\s/.test(line))
        ?.replace(/[*`]/g, "") || "Agent activity";
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
  const renderSection = (section: (typeof sections)[number], index: number) => (
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
  );
  return (
    <section aria-label="Agent response" className="mx-auto w-full max-w-4xl px-3 py-2">
      <button
        aria-expanded={open}
        aria-label={label}
        className="flex w-full items-center gap-2 py-2 text-left text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
        onClick={() => setOverride({ basis: expanded, value: !open })}
        type="button"
      >
        <WorkshopStatusIcon
          className="size-3.5"
          state={
            live
              ? "working"
              : awaitingApproval
                ? "review"
                : error || interrupted
                  ? "error"
                  : "complete"
          }
        />
        <span className="min-w-0 flex-1 truncate">{label}</span>
        <Icon.chevronRight
          className={cn(
            "size-3 transition-transform motion-reduce:transition-none",
            open && "rotate-90"
          )}
        />
      </button>
      <div className="flex flex-col gap-4 pt-2 pb-4" hidden={!open}>
        {(error || interrupted) && <span className="text-xs text-warning">Incomplete</span>}
        {awaitingApproval && (
          <span className="text-xs text-warning" role="status">
            Awaiting approval
          </span>
        )}
        {summaryIndex > 0 && (
          <details className="space-y-4" open={live ? true : undefined}>
            <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
              Preparation activity
            </summary>
            {sections.slice(0, summaryIndex).map(renderSection)}
          </details>
        )}
        {sections
          .slice(summaryIndex)
          .map((section, index) => renderSection(section, summaryIndex + index))}
        {live && <WorkshopActivity progress={turn.progress} />}
        {(error || interrupted) && (
          <Alert variant="destructive">
            <p>{error || "The request stopped before completion. Saved changes are retained."}</p>
          </Alert>
        )}
      </div>
    </section>
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
  landingStatus,
  error,
  onSend,
  onSelect,
  onAccept,
  onDiscard,
  initialRequest = "",
  focusedCell,
  onClearFocus,
  renderCell,
  before,
  scrollRef: externalScrollRef,
}: {
  turns: ChatTurn[];
  live: LiveTurn | null;
  cells: Cell[];
  busy: boolean;
  state: string;
  landingStatus?: string;
  /** The dataset's own error, when its state is `error`. */
  error?: string;
  onSend: (message: string, uploads?: string[]) => Promise<boolean | undefined> | undefined;
  onSelect: (id: string) => void;
  onAccept: (id: string) => void;
  onDiscard: (id: string) => void;
  initialRequest?: string;
  focusedCell?: Cell;
  onClearFocus?: () => void;
  renderCell: (cell: Cell) => ReactNode;
  before?: ReactNode;
  scrollRef?: RefObject<HTMLDivElement | null>;
}) {
  const [draft, setDraft] = useState(initialRequest);
  const uploads = useDatasetUploads();
  const fileInput = useRef<HTMLInputElement>(null);
  const sending = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [attachmentError, setAttachmentError] = useState("");
  const [dragging, setDragging] = useState(false);
  const blocked = busy || submitting;
  const busyLabel = (state === "landing" && landingStatus) || WAITING[state] || "Working";
  const unfinished = uploads.files.some((file) => file.status !== "ready");
  const canSend = !blocked && !unfinished && (!!draft.trim() || uploads.files.length > 0);
  const addFiles = (files: File[]) => {
    if (blocked || !files.length) return;
    if (uploads.files.length + files.length > 100) {
      setAttachmentError("Choose up to 100 files.");
      return;
    }
    setAttachmentError("");
    uploads.add(files);
  };
  const localScrollRef = useRef<HTMLDivElement>(null);
  const scrollRef = externalScrollRef ?? localScrollRef;
  const [submittedAt, setSubmittedAt] = useState(-1);
  const [expandedProposal, setExpandedProposal] = useState<string | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const cellsById = useMemo(() => new Map(cells.map((c) => [c.id, c])), [cells]);
  const tail = cells.filter((cell) => cell.state !== "proposed").at(-1);
  const proposals = cells.filter((cell) => {
    const review = cell.review as { input_fingerprint?: string } | null;
    return (
      cell.state === "proposed" &&
      tail?.state === "ok" &&
      !!tail.fingerprint &&
      review?.input_fingerprint === tail.fingerprint
    );
  });
  const lastTurn = turns.at(-1);
  const runningIndex =
    lastTurn?.role === "agent" && lastTurn.status === "running" ? turns.length - 1 : -1;

  const contentRef = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const focusedId = focusedCell?.id;
  useLayoutEffect(() => {
    // Cell navigation wins over the resize caused by adding composer context.
    if (focusedId) pinned.current = false;
  }, [focusedId]);

  // The list follows its newest line until the reader scrolls away. Content
  // grows after mount (markdown, cell results), so size drives it, not turns.
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
  }, [scrollRef]);

  const send = async () => {
    const text = draft.trim();
    if (!canSend || sending.current) return;
    sending.current = true;
    setSubmitting(true);
    setAttachmentError("");
    const previousSubmittedAt = submittedAt;
    setSubmittedAt(turns.length);
    try {
      const accepted = uploads.files.length
        ? await onSend(
            text,
            uploads.files.map((file) => file.uploadId!)
          )
        : await onSend(text);
      if (accepted === false) {
        setSubmittedAt(previousSubmittedAt);
        return;
      }
      pinned.current = true;
      setSubmittedAt(turns.length);
      setDraft("");
      uploads.reset();
    } catch (err) {
      setSubmittedAt(previousSubmittedAt);
      setAttachmentError(errorMessage(err, "Couldn't send. Your draft and files are retained."));
    } finally {
      sending.current = false;
      setSubmitting(false);
    }
  };
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send();
    }
  };
  const turnProps = { busy, cellsById, onSelect };
  const visibleTurns: (ChatTurn | LiveTurn)[] = turns.map((turn, index) =>
    index === runningIndex && live
      ? {
          ...turn,
          ...live,
          cells: live.cells.length ? live.cells : turn.cells,
          progress: live.progress ?? turn.progress,
          steps: live.steps.length ? live.steps : turn.steps,
        }
      : turn
  );
  if (live && runningIndex < 0 && busy) visibleTurns.push(live);
  const flow = notebookFlow(cells, visibleTurns);
  const lastIndex = visibleTurns.length - 1;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div
        aria-label="Notebook flow"
        className="workshop-flow min-h-0 flex-1 overflow-y-auto"
        ref={scrollRef}
      >
        <div
          className="mx-auto flex max-w-7xl flex-col gap-2 pt-3 pr-2 pb-8 pl-10 sm:pr-4"
          ref={contentRef}
        >
          {before}
          {flow.map((entry) => {
            if (entry.kind === "cell")
              return <div key={entry.cell.id}>{renderCell(entry.cell)}</div>;
            const turn = visibleTurns[entry.index];
            const isLive = busy && (entry.index === runningIndex || !("role" in turn));
            return (
              <Turn
                expanded={
                  isLive ||
                  (entry.index === lastIndex && (submittedAt < 0 || entry.index >= submittedAt))
                }
                key={"id" in turn ? (turn.id ?? `${turn.at}-${entry.index}`) : "live"}
                live={isLive}
                turn={turn}
                {...turnProps}
              />
            );
          })}
          {busy && !live && runningIndex < 0 && (
            <p className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <WorkshopStatusIcon className="size-3" state="working" />
              {busyLabel}
            </p>
          )}
        </div>
      </div>
      <div className="shrink-0 bg-card px-3 pt-2 pb-3 sm:px-6">
        <div className="mx-auto max-w-4xl">
          {proposals.length > 0 && (
            <section aria-label="Proposed changes" className="mx-2 mb-2">
              <Card className="max-h-[40dvh] overflow-y-auto bg-popover">
                <ul className="divide-y divide-border/70">
                  {proposals.map((cell) => (
                    <ProposalRow
                      busy={busy}
                      cell={cell}
                      key={cell.id}
                      onAccept={onAccept}
                      onDiscard={onDiscard}
                      onOpenChange={(open) => setExpandedProposal(open ? cell.id : null)}
                      open={expandedProposal === cell.id}
                    />
                  ))}
                </ul>
              </Card>
            </section>
          )}
          {error && (
            <pre className="mb-2 max-h-32 overflow-auto whitespace-pre-wrap break-words rounded-sm border border-destructive/40 bg-destructive/10 px-2 py-1 font-mono text-xs text-destructive">
              {error}
            </pre>
          )}
          <Card
            className={cn("bg-wash-raised p-1", dragging && "border-primary")}
            onDragLeave={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null))
                setDragging(false);
            }}
            onDragOver={(event) => {
              if (event.dataTransfer.types.includes("Files")) {
                event.preventDefault();
                setDragging(true);
              }
            }}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              addFiles(Array.from(event.dataTransfer.files));
            }}
          >
            <div className="rounded-md border border-border bg-card px-4 pt-3 pb-2">
              {focusedCell && (
                <div className="mb-2 flex items-center gap-1.5 border-b border-border/70 pb-2 text-xs text-muted-foreground">
                  <button
                    className="min-w-0 flex-1 truncate text-left hover:text-foreground"
                    onClick={() => onSelect(focusedCell.id)}
                    type="button"
                  >
                    {focusedCell.version} · {focusedCell.title}
                  </button>
                  <Button
                    aria-label="Clear cell focus"
                    onClick={onClearFocus}
                    size="icon-xs"
                    variant="ghost"
                  >
                    <Icon.close />
                  </Button>
                </div>
              )}
              <textarea
                aria-label="Message the agent"
                className="block max-h-40 min-h-12 w-full resize-none bg-transparent text-sm outline-none field-sizing-content placeholder:text-muted-foreground"
                disabled={submitting}
                maxLength={8000}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onKeyDown}
                onPaste={(event) => {
                  if (event.clipboardData.files.length) {
                    event.preventDefault();
                    addFiles(Array.from(event.clipboardData.files));
                  }
                }}
                placeholder={
                  busy
                    ? "Draft your next message…"
                    : "Ask about the data, make a change or generate examples…"
                }
                ref={composerRef}
                rows={2}
                value={draft}
              />
              <input
                accept=".csv,.tsv,.json,.jsonl,.ndjson,.parquet,.gz,.pdf,.docx,.md,.txt,.png,.jpg,.jpeg,.webp"
                aria-label="Attach files to this dataset"
                className="hidden"
                multiple
                onChange={(event) => {
                  addFiles(Array.from(event.target.files ?? []));
                  event.target.value = "";
                }}
                ref={fileInput}
                type="file"
              />
              <div className="mt-2 flex items-end gap-2">
                <Button
                  aria-label="Add files"
                  disabled={blocked}
                  onClick={() => fileInput.current?.click()}
                  size="icon-sm"
                  title="Add files to this dataset"
                  variant="ghost"
                >
                  <Icon.add />
                </Button>
                <ul
                  aria-label="Attached files"
                  className="flex max-h-32 min-w-0 flex-1 flex-wrap gap-1.5 overflow-y-auto"
                >
                  {uploads.files.map((entry) => (
                    <Attachment
                      disabled={submitting}
                      entry={entry}
                      key={entry.id}
                      onRemove={() => uploads.remove(entry.id)}
                      onRetry={() => uploads.retry(entry)}
                    />
                  ))}
                </ul>
                <Button
                  aria-label="Send"
                  disabled={!canSend}
                  onClick={() => void send()}
                  size="icon-sm"
                  variant="secondary"
                >
                  <Icon.arrowUp className="size-3.5" />
                </Button>
              </div>
              {attachmentError && (
                <p className="mt-2 text-xs text-destructive" role="alert">
                  {attachmentError}
                </p>
              )}
            </div>
            <div
              className="flex h-8 items-center gap-2 px-3 text-xs text-muted-foreground"
              role="status"
            >
              <WorkshopStatusIcon
                className="size-3.5"
                state={
                  busy
                    ? "working"
                    : error
                      ? "error"
                      : proposals.length
                        ? "review"
                        : lastTurn?.role === "agent" && lastTurn.status === "complete"
                          ? "complete"
                          : "idle"
                }
              />
              <span className="min-w-0 truncate" title={busy ? busyLabel : undefined}>
                {busy
                  ? busyLabel
                  : error
                    ? "Request failed"
                    : proposals.length
                      ? "Review changes"
                      : "Data workshop"}
              </span>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
