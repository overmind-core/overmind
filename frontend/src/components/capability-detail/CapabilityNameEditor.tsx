import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useGuestGate } from "@/hooks/use-guest-gate";
import { TITLE } from "@/lib/typography";
import { cn } from "@/lib/utils";

export function CapabilityNameEditor({
  initialName,
  onSave,
  isSaving,
}: {
  initialName: string;
  onSave: (name: string) => void;
  isSaving: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const guard = useGuestGate();
  const [value, setValue] = useState(initialName);
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      const el = inputRef.current;
      if (el) {
        el.focus();
        el.selectionStart = el.value.length;
      }
    }
  }, [editing]);

  useEffect(() => {
    setValue(initialName);
  }, [initialName]);

  const cancel = useCallback(() => {
    setValue(initialName);
    setEditing(false);
  }, [initialName]);

  useEffect(() => {
    if (!editing) return;
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        cancel();
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [editing, cancel]);

  function handleSave() {
    const trimmed = value.trim();
    if (trimmed.length < 3) return;
    onSave(trimmed);
    setEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter") handleSave();
    if (e.key === "Escape") cancel();
  }

  if (editing) {
    // `-ml-3` aligns the title with the description below. Only the left side is
    // negated, or the gap to the id copy buttons collapses.
    return (
      <div
        className="-ml-3 inline-flex items-center gap-0 rounded-md border border-border bg-wash-raised transition-colors"
        ref={containerRef}
      >
        <input
          className={cn(
            TITLE.page,
            "h-12 min-w-[200px] rounded-md border-none bg-transparent px-3 capitalize focus:outline-none"
          )}
          disabled={isSaving}
          maxLength={255}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          ref={inputRef}
          value={value}
        />
        <div className="flex items-center gap-1 pr-2">
          <Button
            aria-label="Save"
            className="text-success hover:bg-success/10"
            disabled={isSaving || value.trim().length < 3}
            onClick={handleSave}
            size="icon-sm"
            title="Save"
            variant="ghost"
          >
            {isSaving ? <Spinner className="text-success" /> : <Icon.success />}
          </Button>
          <Button
            aria-label="Cancel"
            onClick={cancel}
            size="icon-sm"
            title="Cancel"
            variant="ghost"
          >
            <Icon.close />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-label={`Edit name: ${initialName}`}
            // `max-w` compensates the negated margin: with a plain `max-w-full` the
            // 12px came off the truncating span, clipping every name.
            className="group -ml-3 inline-flex max-w-[calc(100%+0.75rem)] items-center gap-2 rounded-md px-3 py-1 text-left capitalize tracking-tight transition-colors hover:bg-wash-raised"
            onClick={guard(() => setEditing(true))}
            type="button"
          >
            {/* No title class: this renders inside PageHeader's <h1>. The input
                above needs one — it replaces the h1's text node while editing. */}
            <span className="truncate">{initialName}</span>
            <Icon.edit
              aria-hidden="true"
              className="size-4 shrink-0 text-muted-foreground opacity-50 transition-opacity group-hover:opacity-100"
            />
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">Click to edit</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
