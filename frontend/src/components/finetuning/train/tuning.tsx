import { useEffect, useRef, useState } from "react";

import { formatLr } from "@/components/finetuning/train/model-config";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export function TuningField({
  id,
  label,
  value,
  onCommit,
  parse = "float",
  min,
  max,
  step,
  hint,
  recommended,
}: {
  id: string;
  label: string;
  value: string;
  onCommit: (value: string) => void;
  parse?: "int" | "float";
  min?: number;
  max?: number;
  step?: number;
  hint?: string;
  recommended?: number;
}) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);

  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);

  const commit = () => {
    // Blank means "auto" — the backend derives it — so it stays blank until typed in.
    if (draft.trim() === "" && value === "") return;
    const parser = parse === "int" ? Number.parseInt : Number.parseFloat;
    let n = parser(draft, 10);
    if (!Number.isFinite(n)) n = min ?? 0;
    if (min != null) n = Math.max(min, n);
    if (max != null) n = Math.min(max, n);
    if (parse === "int") n = Math.round(n);
    setDraft(String(n));
    onCommit(String(n));
  };

  const modified = recommended != null && Number(value) !== recommended;

  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <div className="flex h-5 items-center justify-between gap-2">
        <Label className="text-xs text-muted-foreground" htmlFor={id}>
          {label}
          {hint && <span className="text-muted-foreground/60">{hint}</span>}
        </Label>
        {modified && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                aria-label={`Reset ${label} to ${recommended}`}
                className="rounded-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                onClick={() => {
                  setDraft(String(recommended));
                  onCommit(String(recommended));
                }}
                type="button"
              >
                <Icon.undo className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="left">Recommended {formatLr(recommended)}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <Input
        className="font-mono"
        id={id}
        max={max}
        min={min}
        onBlur={() => {
          focused.current = false;
          commit();
        }}
        onChange={(e) => setDraft(e.target.value)}
        onFocus={() => {
          focused.current = true;
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") e.currentTarget.blur();
        }}
        placeholder={value === "" ? "auto" : undefined}
        size="sm"
        step={step}
        type="number"
        value={draft}
      />
    </div>
  );
}
