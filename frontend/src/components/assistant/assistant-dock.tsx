import { type CSSProperties, useCallback, useId, useMemo, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { useNavigate } from "@tanstack/react-router";

import apiClient from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { useAssistantThread } from "@/hooks/use-assistant-thread";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { usePersistedState } from "@/hooks/use-persisted-state";
import { errorMessage } from "@/lib/notify";
import type { Cell } from "@/openapi";
import { AssistantQueue } from "./assistant-queue";
import { answerKey, type ResultActions } from "./assistant-result";
import { AssistantTranscript } from "./assistant-transcript";
import { PANEL_WIDTH, PanelResize } from "./panel-resize";
import type { AssistantEvent, Interaction, QuestionNode, ResultNode } from "./types";

const PHONE = "(max-width: 767px)";

function* nodesOf(node: ResultNode): Generator<ResultNode> {
  yield node;
  if ("children" in node) for (const child of node.children) yield* nodesOf(child);
  if (node.type === "Tabs")
    for (const tab of node.tabs) for (const child of tab.children) yield* nodesOf(child);
}

/** The newest unanswered question with one text field: the composer can answer it. */
function openQuestion(events: AssistantEvent[], answered: ReadonlySet<string>) {
  for (const event of [...events].reverse()) {
    if (event.kind !== "tool_result" || !event.presentation) continue;
    const call = event.payload.call;
    for (const node of nodesOf(event.presentation.root))
      if (
        node.type === "Question" &&
        node.fields.length === 1 &&
        node.fields[0].type === "text" &&
        !answered.has(answerKey(call, node.id))
      )
        return { call, node: node as QuestionNode };
    return null;
  }
  return null;
}

function statusOf(
  assistant: ReturnType<typeof useAssistantThread>,
  active: boolean,
  stopping: boolean
) {
  if (stopping) return "Stopping";
  const last = assistant.events.at(-1);
  if (active) {
    if (assistant.thread?.waitingFor) return `Running ${assistant.thread.waitingFor}`;
    if (assistant.draft?.tools.length) return `Preparing ${assistant.draft.tools.at(-1)}`;
    return "Working";
  }
  if (last?.kind === "status")
    return { failed: "Run failed", interrupted: "Interrupted", stopped: "Stopped" }[
      last.payload.state
    ];
  return last ? "Complete" : "Ask about this data";
}

export function AssistantDock({
  assistant,
  projectId,
  datasetId,
  cells,
  selectedCell,
  onSelectCell,
}: {
  assistant: ReturnType<typeof useAssistantThread>;
  projectId: string;
  datasetId: string;
  cells: Cell[];
  selectedCell: string | null;
  onSelectCell: (id: string) => void;
}) {
  const navigate = useNavigate();
  const guard = useGuestGate();
  const panelId = useId();
  const bodyId = useId();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const [text, setText] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [panel, setPanel] = useState(false);
  const [keptWidth, keepWidth] = usePersistedState("assistant:panelWidth", PANEL_WIDTH.default);
  const [draggedWidth, setDraggedWidth] = useState<number | null>(null);
  // A retry of the same message is the same request; a changed message is a new one.
  const request = useRef<{ key: string; body: string } | null>(null);

  const { thread, events, send, stop } = assistant;
  const queue = thread?.queue ?? [];
  const active = (thread?.state ?? "idle") !== "idle" || queue.length > 0;
  const stopping = !!thread?.stopRequested || stop.isPending;
  const selected = cells.find((cell) => cell.id === selectedCell);
  const answered = useMemo(() => {
    const keys = new Set<string>();
    const add = (given: Interaction | null | undefined) =>
      given && keys.add(answerKey(given.call, given.id));
    for (const event of events) if (event.kind === "user") add(event.payload.interaction);
    for (const message of queue) add(message.interaction);
    return keys;
  }, [events, queue]);
  const question = useMemo(() => openQuestion(events, answered), [events, answered]);
  const status = statusOf(assistant, active, stopping);

  const collapse = () => {
    setPanel(false);
    setDraggedWidth(null);
    setExpanded(false);
    textarea.current?.blur();
  };
  const deliver = (message: string, interaction?: Interaction, restore = false) => {
    const body = { cells: selected ? [selected.id] : [], interaction, text: message };
    const signature = JSON.stringify(body);
    if (request.current?.body !== signature)
      request.current = { body: signature, key: crypto.randomUUID() };
    send.mutate(
      { ...body, requestKey: request.current.key },
      {
        // Only an untouched composer gets its text back.
        onError: () => restore && setText((current) => current || message),
        onSuccess: () => {
          request.current = null;
          setPanel(true);
          setExpanded(true);
        },
      }
    );
  };
  const submit = guard(() => {
    const message = text.trim();
    if (!message || send.isPending || stopping) return;
    setText("");
    const field = question?.node.fields[0].name;
    deliver(
      message,
      question && field
        ? { call: question.call, id: question.node.id, values: { [field]: message } }
        : undefined,
      true
    );
  });
  // The transcript re-renders when its actions change: these read the newest handlers
  // through a ref, so a keystroke in the composer changes nothing it sees.
  const handlers = useRef({ collapse, deliver, guard });
  handlers.current = { collapse, deliver, guard };
  const threadId = thread?.id;
  const sending = send.isPending;
  // Its own identity: an image loads again whenever this function changes.
  const loadImage = useCallback(
    (name: string) =>
      apiClient.assistantThreads.assistantThreadsImagesRetrieve({ id: threadId!, name }),
    [threadId]
  );
  const actions: ResultActions = useMemo(
    () => ({
      answered,
      loadImage,
      onAnswer: (message, interaction) =>
        handlers.current.guard(() => handlers.current.deliver(message, interaction))(),
      onCell: (cell, dataset) => {
        if (dataset === datasetId || cells.some((item) => item.id === cell)) {
          if (window.matchMedia(PHONE).matches) handlers.current.collapse();
          onSelectCell(cell);
        } else
          void navigate({
            params: { datasetId: dataset },
            search: { cell, projectId },
            to: "/datasets/$datasetId",
          });
      },
      onTrain: (dataset) =>
        void navigate({ search: { datasetId: dataset, projectId, train: true }, to: "/training" }),
      sending,
    }),
    [answered, cells, datasetId, loadImage, navigate, onSelectCell, projectId, sending]
  );
  const sendHint = stopping ? "Stopping" : active ? "Queue a follow-up" : "Send message";

  return (
    <Card
      aria-label="Assistant chat"
      className="assistant-surface assistant-dock absolute bottom-3 z-20 flex min-h-0 flex-col gap-2 overflow-hidden bg-muted p-1.5"
      data-expanded={expanded}
      data-panel={panel}
      data-resizing={draggedWidth !== null}
      id={panelId}
      onBlurCapture={(event) => {
        if (
          !panel &&
          !event.currentTarget.contains(event.relatedTarget) &&
          !event.currentTarget.matches(":hover")
        )
          setExpanded(false);
      }}
      onFocusCapture={() => setExpanded(true)}
      onKeyDown={(event) => {
        // React sends keys from portaled menus here too, and a field's own Escape is not ours.
        const target = event.target as HTMLElement;
        const field = target !== textarea.current && target.matches("input, textarea, select");
        if (event.key !== "Escape" || field || !event.currentTarget.contains(target)) return;
        event.preventDefault();
        collapse();
      }}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={(event) => {
        if (!panel && !event.currentTarget.contains(document.activeElement)) setExpanded(false);
      }}
      role="region"
      style={{ "--assistant-panel-width": `${draggedWidth ?? keptWidth}px` } as CSSProperties}
    >
      {panel && (
        <AssistantTranscript
          actions={actions}
          active={active}
          activity={active && !assistant.draft && thread?.state !== "waiting" ? status : undefined}
          cells={cells}
          draft={assistant.draft}
          events={events}
          onSelectCell={(id) => {
            if (window.matchMedia(PHONE).matches) collapse();
            onSelectCell(id);
          }}
        />
      )}
      {expanded && assistant.error && (
        <Alert className="w-auto text-xs">
          {errorMessage(assistant.error, "The conversation is not available.")}
        </Alert>
      )}
      <div className="assistant-composer-stack flex min-w-0 shrink-0 flex-col gap-1">
        {expanded && (
          <AssistantQueue
            disabled={stopping}
            messages={queue}
            // A change in flight holds the revision the next one needs.
            onChange={guard((change) => {
              if (!assistant.changeQueued.isPending) assistant.changeQueued.mutate(change);
            })}
          />
        )}
        <form
          aria-label="Message the assistant"
          className="assistant-chat-inner shrink-0 overflow-hidden border border-border bg-popover"
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <span aria-hidden="true" className="assistant-prompt-mark">
            &gt;
          </span>
          <div className="assistant-dock-body" id={bodyId} inert={!expanded}>
            <div className="min-h-0 overflow-hidden">
              <textarea
                aria-label="Message"
                className="assistant-chat-input block max-h-24 min-h-10 w-full resize-none border-0 bg-transparent text-xs leading-4 outline-none"
                maxLength={16000}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                  const plain =
                    !event.shiftKey && !event.altKey && !event.ctrlKey && !event.metaKey;
                  if (event.nativeEvent.isComposing || !plain) return;
                  const suggestion = question?.node.fields[0].placeholder;
                  if (event.key === "Tab" && suggestion && !text) {
                    event.preventDefault();
                    setText(suggestion);
                  }
                  if (event.key === "Enter") {
                    event.preventDefault();
                    submit();
                  }
                }}
                placeholder={
                  question?.node.fields[0].placeholder ||
                  (active ? "Queue a follow-up…" : "Ask about the data or describe a change…")
                }
                ref={textarea}
                value={text}
              />
            </div>
          </div>
          <div className="assistant-dock-footer flex min-w-0 items-center gap-2">
            {expanded ? (
              <>
                {panel && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        aria-label="Close full conversation"
                        onClick={collapse}
                        size="icon-xs"
                        type="button"
                        variant="ghost"
                      >
                        <Icon.collapse />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Minimize conversation</TooltipContent>
                  </Tooltip>
                )}
                <div className="min-w-0 flex-1">
                  {selected && (
                    <Button
                      className="max-w-full truncate px-0 text-xs text-muted-foreground no-underline hover:text-foreground"
                      onClick={() => onSelectCell(selected.id)}
                      size="xs"
                      type="button"
                      variant="link"
                    >
                      <Icon.dataset />
                      {selected.title}
                    </Button>
                  )}
                </div>
                {active && thread && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        aria-label={stopping ? "Stopping…" : "Stop"}
                        disabled={stopping}
                        onClick={guard(() => stop.mutate())}
                        size="icon-xs"
                        type="button"
                        variant="ghost"
                      >
                        {stopping ? <Spinner size="sm" /> : <Icon.stop />}
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Stop run</TooltipContent>
                  </Tooltip>
                )}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <Button
                        aria-label={active ? "Queue" : "Send"}
                        className="px-1 no-underline"
                        disabled={send.isPending || stopping}
                        size="xs"
                        type="submit"
                        variant="link"
                      >
                        {send.isPending ? <Spinner size="sm" /> : <Icon.send />}
                        {!active && "Send"}
                      </Button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{sendHint}</TooltipContent>
                </Tooltip>
              </>
            ) : (
              <Button
                aria-controls={bodyId}
                aria-expanded={false}
                aria-label="Expand assistant chat"
                className="min-w-0 flex-1 justify-start border-0 bg-transparent text-muted-foreground"
                onClick={() => {
                  // The field is inert until the body is expanded in the DOM.
                  flushSync(() => setExpanded(true));
                  textarea.current?.focus();
                }}
                size="xs"
                type="button"
                variant="outline"
              >
                <span className="min-w-0 flex-1 truncate text-left">{status}</span>
              </Button>
            )}
            {!panel && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label="Open full conversation"
                    onClick={() => {
                      setPanel(true);
                      setExpanded(true);
                    }}
                    size="icon-xs"
                    type="button"
                    variant="ghost"
                  >
                    <Icon.expand />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Open conversation</TooltipContent>
              </Tooltip>
            )}
          </div>
        </form>
      </div>
      {panel && (
        <PanelResize
          onCommit={keepWidth}
          onDrag={setDraggedWidth}
          panelId={panelId}
          width={draggedWidth ?? keptWidth}
        />
      )}
    </Card>
  );
}
