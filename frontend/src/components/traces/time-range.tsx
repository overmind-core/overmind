import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { NATIVE_DATE_TRIGGER } from "@/lib/colors";
import { cn } from "@/lib/utils";

export type TimeRangePreset =
  | "all"
  | "past15m"
  | "past1h"
  | "past24h"
  | "past7d"
  | "past30d"
  | "custom";

const PRESET_LABELS: Record<TimeRangePreset, string> = {
  all: "All time",
  custom: "Custom",
  past1h: "Last 1 hour",
  past7d: "Last 7 days",
  past15m: "Last 15 min",
  past24h: "Last 24 hours",
  past30d: "Last 30 days",
};

const PRESET_MS: Record<Exclude<TimeRangePreset, "all" | "custom">, number> = {
  past1h: 60 * 60 * 1000,
  past7d: 7 * 24 * 60 * 60 * 1000,
  past15m: 15 * 60 * 1000,
  past24h: 24 * 60 * 60 * 1000,
  past30d: 30 * 24 * 60 * 60 * 1000,
};

/** Preset → ISO 8601 `gte`/`lte`, ready for `?created_at__gte=` / `?created_at__lte=`. */
export function resolveTimeRange(
  preset: TimeRangePreset,
  custom?: { gte?: string; lte?: string }
): { gte?: string; lte?: string } {
  if (preset === "all") return {};
  if (preset === "custom") return custom ?? {};
  const now = Date.now();
  const gte = new Date(now - PRESET_MS[preset]).toISOString();
  return { gte };
}

function ToolbarTooltip({ label, children }: { label: string; children: React.ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="top" sideOffset={6}>
        {label}
      </TooltipContent>
    </Tooltip>
  );
}

export function TimeRangeButton({
  value,
  customTimeRange,
  onChange,
  className,
}: {
  value: TimeRangePreset;
  customTimeRange: { gte?: string; lte?: string };
  onChange: (preset: TimeRangePreset, custom?: { gte?: string; lte?: string }) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [draftGte, setDraftGte] = useState(toDatetimeLocalValue(customTimeRange.gte));
  const [draftLte, setDraftLte] = useState(toDatetimeLocalValue(customTimeRange.lte));

  useEffect(() => {
    setDraftGte(toDatetimeLocalValue(customTimeRange.gte));
    setDraftLte(toDatetimeLocalValue(customTimeRange.lte));
  }, [customTimeRange.gte, customTimeRange.lte]);

  const summary =
    value === "custom"
      ? `Custom: ${formatIsoShort(customTimeRange.gte) || "…"} → ${
          formatIsoShort(customTimeRange.lte) || "now"
        }`
      : PRESET_LABELS[value];

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <ToolbarTooltip label="Change trace time range">
        <PopoverTrigger asChild>
          <Button className={cn("gap-1.5", className)} size="default" variant="secondary">
            <Icon.job />
            {summary}
          </Button>
        </PopoverTrigger>
      </ToolbarTooltip>
      <PopoverContent align="start" className="w-[320px] p-3 text-xs">
        <p className="text-xs font-medium text-muted-foreground">Presets</p>
        <div className="mt-1.5 grid grid-cols-2 gap-1">
          {(["past15m", "past1h", "past24h", "past7d", "past30d", "all"] as TimeRangePreset[]).map(
            (p) => (
              <button
                className={cn(
                  "rounded-md border px-2 py-1 text-left text-xs transition-colors",
                  value === p
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border bg-wash-subtle hover:bg-wash-raised"
                )}
                key={p}
                onClick={() => {
                  onChange(p);
                  setOpen(false);
                }}
                type="button"
              >
                {PRESET_LABELS[p]}
              </button>
            )
          )}
        </div>
        <div className="-mx-3 mt-3 border-t border-border/70 px-3 pt-3">
          <p className="text-xs font-medium text-muted-foreground">Custom range</p>
          <div className="mt-1.5 grid grid-cols-2 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              From
              <Input
                aria-label="Custom range from"
                className={DATETIME_TRIGGER_CLASS}
                onChange={(e) => setDraftGte(e.target.value)}
                type="datetime-local"
                value={draftGte}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs">
              To
              <Input
                aria-label="Custom range to"
                className={DATETIME_TRIGGER_CLASS}
                onChange={(e) => setDraftLte(e.target.value)}
                type="datetime-local"
                value={draftLte}
              />
            </label>
          </div>
          <Button
            className="mt-2 w-full text-xs"
            onClick={() => {
              const gte = draftGte ? new Date(draftGte).toISOString() : undefined;
              const lte = draftLte ? new Date(draftLte).toISOString() : undefined;
              onChange("custom", { gte, lte });
              setOpen(false);
            }}
            size="default"
          >
            <Icon.success />
            Apply custom range
          </Button>
        </div>
      </PopoverContent>
    </Popover>
  );
}

const DATETIME_TRIGGER_CLASS = cn(
  "h-8 cursor-pointer rounded-sm border px-2 text-xs font-medium transition-colors",
  "focus-visible:ring-[2px] focus-visible:ring-ring/60",
  NATIVE_DATE_TRIGGER
);

/** ISO → `datetime-local` value (local wall clock). Empty/invalid → "". */
function toDatetimeLocalValue(iso: string | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatIsoShort(iso: string | undefined): string {
  if (!iso) return "";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      month: "short",
    });
  } catch {
    return iso;
  }
}
