import { useEffect, useRef, useState } from "react";

import { ComposerPrimitive, QueueItemPrimitive, useAui } from "@assistant-ui/react";
import { useMutation } from "@tanstack/react-query";

import api from "@/client";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { errorMessage } from "@/lib/notify";
import type { WorkshopMessage, WorkshopMessageControlRequest } from "@/openapi";

export function useWorkshopQueue(taskId: string | undefined) {
  const request = useRef<{ signature: string; key: string } | null>(null);
  const change = useMutation({
    mutationFn: (input: Omit<WorkshopMessageControlRequest, "requestKey">) => {
      if (!taskId) throw new Error("No active Workshop task.");
      const signature = JSON.stringify(input);
      if (request.current?.signature !== signature)
        request.current = { key: crypto.randomUUID(), signature };
      return api.workshopTasks.workshopTasksMessagesControlCreate({
        id: taskId,
        workshopMessageControlRequest: { ...input, requestKey: request.current.key },
      });
    },
    onSuccess: () => {
      request.current = null;
    },
  });
  return change;
}

export function WorkshopQueue({
  messages,
  taskId,
  disabled,
  change,
}: {
  messages: WorkshopMessage[];
  taskId: string;
  disabled: boolean;
  change: ReturnType<typeof useWorkshopQueue>;
}) {
  const guard = useGuestGate();
  const aui = useAui();
  const dragged = useRef<WorkshopMessage | null>(null);
  const ordered = [...messages].sort(
    (a, b) => (a.queuePosition ?? Infinity) - (b.queuePosition ?? Infinity)
  );
  const [editing, setEditing] = useState<{ id: string; text: string; revision: number } | null>(
    null
  );
  const editor = useRef<HTMLTextAreaElement>(null);
  const editButtons = useRef(new Map<string, HTMLButtonElement>());
  const lastEdited = useRef<string | null>(null);
  useEffect(() => {
    if (editing?.id) editor.current?.focus();
    else if (lastEdited.current) editButtons.current.get(lastEdited.current)?.focus();
  }, [editing?.id]);
  const update = (message: WorkshopMessage) =>
    guard(() =>
      change.mutate(
        {
          command: "edit",
          message: message.id.slice(taskId.length + 1),
          revision: editing!.revision,
          text: editing!.text.trim(),
        },
        { onSuccess: () => setEditing(null) }
      )
    )();
  const move = (message: WorkshopMessage, position: number) =>
    guard(() => {
      const remaining = ordered.filter(
        (item) => item.id !== message.id && item.status === "pending"
      );
      const target = remaining[position];
      aui.composer
        .queueItem({ id: message.id })
        .move(target ? { insertBefore: target.id } : { insertAfter: remaining.at(-1)?.id ?? null });
    })();
  if (!messages.length) return null;
  return (
    <section
      aria-label="Queued messages"
      className="max-h-32 w-full shrink-0 space-y-1 overflow-y-auto text-xs"
    >
      {
        <ComposerPrimitive.Queue>
          {({ queueItem }) => {
            const message = ordered.find((item) => item.id === queueItem.id);
            if (!message) return null;
            const index = ordered.indexOf(message);
            const editable = message.status === "pending" && !disabled && !change.isPending;
            const pendingIndex = ordered
              .filter((item) => item.status === "pending")
              .indexOf(message);
            const pendingCount = ordered.filter((item) => item.status === "pending").length;
            return (
              <div
                className="w-full rounded-md border border-border/70 bg-wash-raised px-2 py-1"
                key={message.id}
                onDragOver={(event) => {
                  if (editable) event.preventDefault();
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (editable && dragged.current) move(dragged.current, pendingIndex);
                  dragged.current = null;
                }}
              >
                {editing?.id === message.id && message.status === "pending" && !disabled ? (
                  <div className="space-y-2">
                    <Textarea
                      aria-label="Edit queued message"
                      className="min-h-16 text-xs md:text-xs"
                      disabled={change.isPending}
                      maxLength={16000}
                      onChange={(event) => setEditing({ ...editing, text: event.target.value })}
                      onKeyDown={(event) => {
                        event.stopPropagation();
                        if (event.key === "Escape") {
                          event.preventDefault();
                          setEditing(null);
                        }
                        if (
                          event.key === "Enter" &&
                          !event.shiftKey &&
                          !event.nativeEvent.isComposing
                        ) {
                          event.preventDefault();
                          if (editing.text.trim() && !change.isPending) update(message);
                        }
                      }}
                      ref={editor}
                      value={editing.text}
                    />
                    <div className="flex justify-end gap-2">
                      <Button
                        onClick={() => setEditing(null)}
                        size="xs"
                        type="button"
                        variant="secondary"
                      >
                        Cancel edit
                      </Button>
                      <Button
                        disabled={!editing.text.trim() || change.isPending}
                        onClick={() => update(message)}
                        size="xs"
                        type="button"
                        variant="secondary"
                      >
                        Save
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          aria-label={`Move queued message ${index + 1}`}
                          className="cursor-grab active:cursor-grabbing"
                          disabled={!editable}
                          draggable={editable}
                          onDragEnd={() => {
                            dragged.current = null;
                          }}
                          onDragStart={(event) => {
                            event.dataTransfer.setData("text/plain", message.id);
                            event.dataTransfer.effectAllowed = "move";
                            dragged.current = message;
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "ArrowUp" && pendingIndex > 0) {
                              event.preventDefault();
                              move(message, pendingIndex - 1);
                            }
                            if (event.key === "ArrowDown" && pendingIndex < pendingCount - 1) {
                              event.preventDefault();
                              move(message, pendingIndex + 1);
                            }
                          }}
                          size="icon-xs"
                          type="button"
                          variant="ghost"
                        >
                          <Icon.menu />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Drag or use arrow keys to reorder</TooltipContent>
                    </Tooltip>
                    <p className="min-w-0 flex-1 truncate">
                      <QueueItemPrimitive.Text />
                    </p>
                    {message.status === "delivering" ? (
                      <span className="shrink-0 text-muted-foreground">Sending</span>
                    ) : (
                      <>
                        <Button
                          className="px-1 font-normal text-muted-foreground no-underline"
                          disabled={!editable}
                          onClick={() => move(message, 0)}
                          size="xs"
                          variant="link"
                        >
                          <Icon.send />
                          Send
                        </Button>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <QueueItemPrimitive.Remove asChild>
                              <Button
                                aria-label={`Remove queued message ${index + 1}`}
                                disabled={!editable}
                                onClick={guard()}
                                size="icon-xs"
                                type="button"
                                variant="ghost"
                              >
                                <Icon.delete />
                              </Button>
                            </QueueItemPrimitive.Remove>
                          </TooltipTrigger>
                          <TooltipContent>Delete</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              aria-label={`Edit queued message ${index + 1}`}
                              disabled={!editable || !!message.interaction}
                              onClick={() => {
                                lastEdited.current = message.id;
                                setEditing({
                                  id: message.id,
                                  revision: message.revision ?? 0,
                                  text: message.text,
                                });
                              }}
                              ref={(node) => {
                                if (node) editButtons.current.set(message.id, node);
                                else editButtons.current.delete(message.id);
                              }}
                              size="icon-xs"
                              type="button"
                              variant="ghost"
                            >
                              <Icon.edit />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Edit</TooltipContent>
                        </Tooltip>
                      </>
                    )}
                  </div>
                )}
              </div>
            );
          }}
        </ComposerPrimitive.Queue>
      }
      {change.error && (
        <Alert className="m-2 w-auto">
          {errorMessage(change.error, "Could not update the queued message.")}
        </Alert>
      )}
    </section>
  );
}
