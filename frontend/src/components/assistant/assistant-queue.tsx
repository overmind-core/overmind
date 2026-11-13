import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { AssistantInput, ChangeEnum, ChangeMessageRequest } from "@/openapi";

/** Messages waiting for the run to rest. Each stays editable until the loop takes it; a
 *  change carries the revision the user saw, so a taken message refuses it. */
export function AssistantQueue({
  messages,
  disabled,
  onChange,
}: {
  messages: AssistantInput[];
  disabled: boolean;
  onChange: (change: ChangeMessageRequest) => void;
}) {
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const dragged = useRef<AssistantInput | null>(null);
  const editor = useRef<HTMLTextAreaElement>(null);
  const editingId = editing?.id;
  useEffect(() => {
    if (editingId) editor.current?.focus();
  }, [editingId]);
  if (!messages.length) return null;

  const change = (message: AssistantInput, kind: ChangeEnum, extra = {}) =>
    onChange({ change: kind, message: message.id, revision: message.revision, ...extra });
  /** Places a message at an index of the queue without it. */
  const move = (message: AssistantInput, index: number) => {
    const others = messages.filter((item) => item.id !== message.id);
    change(message, "move", { before: others[index]?.id ?? null });
  };

  return (
    <section
      aria-label="Queued messages"
      className="max-h-32 w-full shrink-0 space-y-1 overflow-y-auto text-xs"
    >
      {messages.map((message, index) => (
        <div
          className="w-full rounded-md border border-border/70 bg-wash-raised px-2 py-1"
          key={message.id}
          onDragOver={(event) => {
            if (!disabled) event.preventDefault();
          }}
          onDrop={(event) => {
            event.preventDefault();
            if (!disabled && dragged.current) move(dragged.current, index);
            dragged.current = null;
          }}
        >
          {editing?.id === message.id ? (
            <div className="space-y-2">
              <Textarea
                aria-label="Edit queued message"
                className="min-h-16 text-xs md:text-xs"
                maxLength={16000}
                onChange={(event) => setEditing({ id: message.id, text: event.target.value })}
                onKeyDown={(event) => {
                  event.stopPropagation();
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setEditing(null);
                  }
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    if (editing.text.trim()) {
                      change(message, "edit", { text: editing.text.trim() });
                      setEditing(null);
                    }
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
                  disabled={!editing.text.trim()}
                  onClick={() => {
                    change(message, "edit", { text: editing.text.trim() });
                    setEditing(null);
                  }}
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
                    disabled={disabled}
                    draggable={!disabled}
                    onDragEnd={() => {
                      dragged.current = null;
                    }}
                    onDragStart={(event) => {
                      event.dataTransfer.setData("text/plain", message.id);
                      event.dataTransfer.effectAllowed = "move";
                      dragged.current = message;
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "ArrowUp" && index > 0) {
                        event.preventDefault();
                        move(message, index - 1);
                      }
                      if (event.key === "ArrowDown" && index < messages.length - 1) {
                        event.preventDefault();
                        move(message, index + 1);
                      }
                    }}
                    size="icon-xs"
                    type="button"
                    variant="ghost"
                  >
                    <Icon.menu />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Reorder</TooltipContent>
              </Tooltip>
              <p className="min-w-0 flex-1 truncate">{message.text}</p>
              {message.urgent ? (
                <span className="shrink-0 text-muted-foreground">Sending</span>
              ) : (
                <>
                  <Button
                    className="px-1 font-normal text-muted-foreground no-underline"
                    disabled={disabled}
                    onClick={() => change(message, "send_now")}
                    size="xs"
                    variant="link"
                  >
                    <Icon.send />
                    Send
                  </Button>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        aria-label={`Remove queued message ${index + 1}`}
                        disabled={disabled}
                        onClick={() => change(message, "remove")}
                        size="icon-xs"
                        type="button"
                        variant="ghost"
                      >
                        <Icon.delete />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Delete</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        aria-label={`Edit queued message ${index + 1}`}
                        disabled={disabled || !!message.interaction}
                        onClick={() => setEditing({ id: message.id, text: message.text })}
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
      ))}
    </section>
  );
}
