import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Loader as Loader2, Cancel as X } from "pixelarticons/react";

import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export function AgentNameEditor({
  initialName,
  onSave,
  isSaving,
}: {
  initialName: string;
  onSave: (name: string) => void;
  isSaving: boolean;
}) {
  const [editing, setEditing] = useState(false);
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
    return (
      <div
        ref={containerRef}
        className="inline-flex items-center gap-0 rounded-lg border border-border bg-muted/50 transition-colors"
      >
        <input
          ref={inputRef}
          className="h-10 min-w-[200px] rounded-lg border-none bg-transparent px-3 font-display text-2xl font-bold capitalize tracking-tight focus:outline-none"
          disabled={isSaving}
          maxLength={255}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          value={value}
        />
        <div className="flex items-center gap-1 pr-2">
          <button
            className="flex size-7 items-center justify-center rounded-md text-emerald-500 transition-colors hover:bg-emerald-500/10 disabled:opacity-50"
            disabled={isSaving || value.trim().length < 3}
            onClick={handleSave}
            title="Save"
            type="button"
          >
            {isSaving ? <Loader2 className="size-4 animate-spin" /> : <Check className="size-4" />}
          </button>
          <button
            className="flex size-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            onClick={cancel}
            title="Cancel"
            type="button"
          >
            <X className="size-4" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            className="rounded-lg px-3 py-1 text-left font-display text-2xl font-bold capitalize tracking-tight transition-colors hover:bg-muted/50"
            onClick={() => setEditing(true)}
            type="button"
          >
            {initialName}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom">Click to edit</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
