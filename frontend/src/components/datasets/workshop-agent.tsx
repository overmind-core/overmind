import { type CSSProperties, useEffect, useId, useRef, useState } from "react";

import {
  type AppendMessage,
  AssistantRuntimeProvider,
  ComposerPrimitive,
  ThreadPrimitive,
} from "@assistant-ui/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import api from "@/client";
import {
  useWorkshopRuntime,
  WorkshopConversation,
} from "@/components/datasets/workshop-conversation";
import {
  WORKSHOP_PANEL_WIDTH,
  WorkshopPanelResize,
} from "@/components/datasets/workshop-panel-resize";
import { useWorkshopQueue, WorkshopQueue } from "@/components/datasets/workshop-queue";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { invalidateDataset } from "@/hooks/use-datasets";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { useWorkshopConversation } from "@/hooks/use-workshop-conversation";
import { errorMessage } from "@/lib/notify";
import type { Cell, WorkshopControlRequest, WorkshopInteraction } from "@/openapi";
import { workshopSuggestion } from "./workshop-suggestion";

const terminal = new Set(["completed", "cancelled", "failed"]);
const emptyCells: Cell[] = [];
const noop = () => {};

export function WorkshopAgent({
  datasetId,
  projectId,
  cells = emptyCells,
  selectedCell,
  onSelectCell = noop,
}: {
  datasetId: string;
  projectId: string;
  cells?: Cell[];
  selectedCell?: string | null;
  onSelectCell?: (id: string) => void;
}) {
  const qc = useQueryClient();
  const guard = useGuestGate();
  const [expanded, setExpanded] = useState(false);
  const [panel, setPanel] = useState(false);
  const [panelWidth, setPanelWidth] = useState(WORKSHOP_PANEL_WIDTH);
  const [resizing, setResizing] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const bodyId = useId();
  const panelId = useId();
  const focusOnExpansion = useRef(false);
  const conversation = useWorkshopConversation(projectId, datasetId);
  useEffect(() => {
    if (expanded && focusOnExpansion.current) {
      focusOnExpansion.current = false;
      textarea.current?.focus();
    }
  }, [expanded]);
  const submission = useRef<{
    text: string;
    cell?: string;
    interaction?: WorkshopInteraction;
    requestKey: string;
  } | null>(null);
  const queryKey = ["workshop-agent", projectId, datasetId];
  const query = useQuery({
    queryFn: () => api.workshopTasks.workshopTasksList({ dataset: datasetId, project: projectId }),
    queryKey,
    refetchInterval: 3000,
  });
  const task = query.data?.tasks[0];
  const active = !!task && !terminal.has(task.state);
  const updated = task?.updatedAt?.getTime();
  useEffect(() => {
    if (updated) invalidateDataset(qc, datasetId);
  }, [qc, datasetId, updated]);

  const start = useMutation({
    mutationFn: ({ text, interaction }: { text: string; interaction?: WorkshopInteraction }) => {
      const cell = selectedCell ?? undefined;
      if (
        submission.current?.text !== text ||
        submission.current?.cell !== cell ||
        JSON.stringify(submission.current?.interaction) !== JSON.stringify(interaction)
      ) {
        submission.current = { cell, interaction, requestKey: crypto.randomUUID(), text };
      }
      return api.workshopTasks.workshopTasksMessagesCreate({
        workshopMessageRequestRequest: {
          dataset: datasetId,
          project: projectId,
          ...submission.current,
        },
      });
    },
    onError: () => setExpanded(true),
    onSuccess: (created) => {
      submission.current = null;
      qc.setQueryData(queryKey, {
        enabled: true,
        tasks: [created, ...(query.data?.tasks ?? []).filter((item) => item.id !== created.id)],
      });
      void qc.invalidateQueries({ queryKey });
    },
  });
  const control = useMutation({
    mutationFn: (command: WorkshopControlRequest["command"]) =>
      api.workshopTasks.workshopTasksControlCreate({
        id: task!.id,
        workshopControlRequest: { command },
      }),
    onSuccess: (updatedTask) => {
      qc.setQueryData(queryKey, {
        enabled: query.data?.enabled,
        tasks: (query.data?.tasks ?? []).map((item) =>
          item.id === updatedTask.id ? updatedTask : item
        ),
      });
      void qc.invalidateQueries({ queryKey });
    },
  });
  const stopping =
    active &&
    (task.desiredState === "cancelled" || (control.isPending && control.variables === "cancel"));
  const queueing = active && task.state !== "blocked";
  const queued = conversation.messages.filter(
    (message) =>
      message.id.startsWith(`${task?.id}:`) &&
      message.role === "user" &&
      (message.status === "pending" || message.status === "delivering")
  );
  const send = async (text: string, interaction?: WorkshopInteraction) => {
    if (!query.data?.enabled || !text || start.isPending || stopping)
      throw new Error("Workshop is not accepting messages.");
    setPanel(true);
    setExpanded(true);
    await start.mutateAsync({ interaction, text });
  };
  const latestActivity = [...conversation.messages]
    .reverse()
    .find(
      (message) =>
        message.id.startsWith(`${task?.id}:`) &&
        message.role === "activity" &&
        (message.status === "running" || message.status === "preparing")
    );
  const current = typeof task?.result?.activity === "string" ? task.result.activity : null;
  const status = stopping
    ? "Stopping"
    : task?.desiredState === "paused"
      ? "Paused"
      : task?.state === "completed"
        ? "Complete"
        : task?.state === "blocked"
          ? "Needs your attention"
          : task?.state === "failed"
            ? "Task failed"
            : task?.state === "cancelled"
              ? "Cancelled"
              : active
                ? latestActivity?.text ||
                  current ||
                  (task.state === "queued"
                    ? "Waiting for the workspace"
                    : "Working on your request")
                : "Ask about this data";
  const failure = query.error || control.error || start.error;
  const selected = cells.find((cell) => cell.id === selectedCell);
  const sendHint = !query.data
    ? query.isError
      ? "Could not check the project workspace. Retrying…"
      : "Checking the project workspace…"
    : !query.data.enabled
      ? "No enabled agent workspace is configured for this project."
      : stopping
        ? "Wait for the run to stop."
        : queueing
          ? "Queue a follow-up"
          : "Send message";
  const queueChange = useWorkshopQueue(task?.id);
  const orderedQueue = [...queued].sort(
    (a, b) => (a.queuePosition ?? Infinity) - (b.queuePosition ?? Infinity)
  );
  const messageText = (message: AppendMessage) =>
    message.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n");
  const suggestion = workshopSuggestion(conversation.messages);
  const submit = async (message: AppendMessage) => {
    const text = messageText(message).trim();
    try {
      await send(
        text,
        suggestion
          ? {
              component: suggestion.id,
              message: suggestion.message,
              values: { [suggestion.field.name]: text },
            }
          : undefined
      );
    } catch {
      // Queue callbacks are fire-and-forget; restore only an untouched composer after rejection.
      if (!runtime.thread.composer.getState().text) runtime.thread.composer.setText(text);
    }
  };
  const changeQueued = (
    id: string,
    command: "move" | "edit" | "remove",
    extra: { position?: number; text?: string } = {}
  ) => {
    const message = queued.find((item) => item.id === id);
    if (!task || !message || message.status !== "pending" || stopping || queueChange.isPending)
      return;
    queueChange.mutate({
      command,
      message: id.slice(task.id.length + 1),
      revision: message.revision ?? 0,
      ...extra,
    });
  };
  const runtime = useWorkshopRuntime(conversation.messages, {
    isRunning: active,
    isSendDisabled: !query.data?.enabled || start.isPending || stopping,
    onCancel: async () => {
      await control.mutateAsync("cancel");
    },
    onNew: submit,
    queue: {
      edit: (id, message) => changeQueued(id, "edit", { text: messageText(message) }),
      enqueue: submit,
      items: orderedQueue.map((message) => ({
        id: message.id,
        parts: [{ text: message.text, type: "text" as const }],
        prompt: message.text,
      })),
      move: (id, placement) => {
        const remaining = orderedQueue.filter(
          (item) => item.id !== id && item.status === "pending"
        );
        const before = remaining.findIndex((item) => item.id === placement.insertBefore);
        const after = remaining.findIndex((item) => item.id === placement.insertAfter);
        const position = before >= 0 ? before : after >= 0 ? after + 1 : 0;
        changeQueued(id, "move", { position });
      },
      remove: (id) => changeQueued(id, "remove"),
      steer: submit,
      steerItems: [],
    },
  });
  const collapse = () => {
    setPanel(false);
    setResizing(false);
    setExpanded(false);
    textarea.current?.blur();
  };

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ThreadPrimitive.Root asChild>
        <Card
          aria-label="Workshop chat"
          className="workshop-chat-surface workshop-dock absolute bottom-3 z-20 flex min-h-0 flex-col gap-2 overflow-hidden bg-muted p-1.5"
          data-expanded={expanded}
          data-panel={panel}
          data-resizing={resizing}
          id={panelId}
          onBlurCapture={(event) => {
            if (
              !panel &&
              !focusOnExpansion.current &&
              !event.currentTarget.contains(event.relatedTarget) &&
              !event.currentTarget.matches(":hover")
            )
              setExpanded(false);
          }}
          onFocusCapture={(event) => {
            if (event.target.closest("[data-workshop-view-toggle]")) return;
            if (!expanded && event.target.closest(".workshop-dock-footer"))
              focusOnExpansion.current = true;
            setExpanded(true);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              collapse();
            }
          }}
          onMouseEnter={() => setExpanded(true)}
          onMouseLeave={(event) => {
            if (!panel && !event.currentTarget.contains(document.activeElement)) setExpanded(false);
          }}
          role="region"
          style={{ "--workshop-panel-width": `${panelWidth}px` } as CSSProperties}
        >
          {panel && (
            <WorkshopConversation
              activeTaskId={active ? task?.id : undefined}
              activity={active && !latestActivity ? status : undefined}
              cells={cells}
              messages={conversation.messages}
              onCell={(cell) => {
                if (cell.dataset === datasetId || cells.some((item) => item.id === cell.id)) {
                  if (window.matchMedia("(max-width: 767px)").matches) collapse();
                  onSelectCell(cell.id);
                } else
                  window.location.assign(
                    `/datasets/${encodeURIComponent(cell.dataset)}?projectId=${encodeURIComponent(projectId)}&cell=${encodeURIComponent(cell.id)}`
                  );
              }}
              onSelect={(id) => {
                if (window.matchMedia("(max-width: 767px)").matches) collapse();
                onSelectCell(id);
              }}
              onSend={send}
            />
          )}
          {panel && failure && (
            <Alert className="w-auto text-xs">
              {errorMessage(failure, "Could not load the Workshop conversation.")}
            </Alert>
          )}
          <div className="workshop-composer-stack flex min-w-0 shrink-0 flex-col gap-1">
            {task && expanded && (
              <WorkshopQueue
                change={queueChange}
                disabled={stopping}
                messages={queued}
                taskId={task.id}
              />
            )}
            <ComposerPrimitive.Root
              aria-label="Transform dataset"
              className="workshop-chat-inner shrink-0 overflow-hidden rounded-md border border-border bg-popover"
              onSubmit={guard()}
            >
              <span aria-hidden="true" className="workshop-prompt-mark">
                &gt;
              </span>
              <div className="workshop-dock-body" id={bodyId} inert={!expanded}>
                <div className="min-h-0 overflow-hidden">
                  <ComposerPrimitive.Input
                    aria-label="Transformation request"
                    cancelOnEscape={false}
                    className="workshop-chat-input block w-full outline-none min-h-10 max-h-24 resize-none border-0 bg-transparent px-2 py-1 text-xs leading-4 focus-visible:ring-0 md:text-xs"
                    disabled={start.isPending}
                    maxLength={16000}
                    onKeyDown={(event) => {
                      if (
                        event.key === "Tab" &&
                        !event.shiftKey &&
                        !event.altKey &&
                        !event.ctrlKey &&
                        !event.metaKey &&
                        !event.nativeEvent.isComposing &&
                        suggestion &&
                        !event.currentTarget.value
                      ) {
                        event.preventDefault();
                        runtime.thread.composer.setText(suggestion.suggestion);
                      }
                      if (event.key === "Enter") guard()(event);
                    }}
                    placeholder={
                      suggestion?.suggestion ||
                      (queueing ? "Queue a follow-up…" : "Ask about the data or describe a change…")
                    }
                    ref={textarea}
                  />
                </div>
              </div>
              <div className="workshop-dock-footer flex min-w-0 items-center gap-2">
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
                    {active && (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <ComposerPrimitive.Cancel asChild>
                            <Button
                              aria-label={stopping ? "Stopping…" : "Stop"}
                              disabled={control.isPending || stopping}
                              onClick={guard()}
                              size="icon-xs"
                              type="button"
                              variant="ghost"
                            >
                              {stopping ? <Spinner size="sm" /> : <Icon.stop />}
                            </Button>
                          </ComposerPrimitive.Cancel>
                        </TooltipTrigger>
                        <TooltipContent>Stop run</TooltipContent>
                      </Tooltip>
                    )}
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span>
                          <ComposerPrimitive.Send asChild>
                            <Button
                              aria-label={queueing ? "Queue" : "Send"}
                              className="px-1 no-underline"
                              disabled={!query.data?.enabled || start.isPending || stopping}
                              onClick={guard()}
                              size="xs"
                              type="button"
                              variant="link"
                            >
                              {start.isPending ? <Spinner size="sm" /> : <Icon.send />}
                              {!queueing && "Send"}
                            </Button>
                          </ComposerPrimitive.Send>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>{sendHint}</TooltipContent>
                    </Tooltip>
                  </>
                ) : (
                  <Button
                    aria-controls={bodyId}
                    aria-expanded={false}
                    aria-label="Expand Workshop chat"
                    className="min-w-0 flex-1 justify-start border-0 bg-transparent text-muted-foreground"
                    onClick={() => {
                      focusOnExpansion.current = true;
                      setExpanded(true);
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
                        data-workshop-view-toggle
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
            </ComposerPrimitive.Root>
          </div>
          {panel && (
            <WorkshopPanelResize
              onResizingChange={setResizing}
              onWidthChange={setPanelWidth}
              panelId={panelId}
              width={panelWidth}
            />
          )}
        </Card>
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}
