import { useState } from "react";
import { CalendarDays } from "lucide-react";

import { cn } from "@/lib/utils";
import { type AnalyticsPreset, type AnalyticsRange, rangeLabel } from "@/lib/analytics";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

const PRESETS: { key: AnalyticsPreset; label: string }[] = [
  { key: "past24h", label: "Last 24 hours" },
  { key: "past7d", label: "Last 7 days" },
  { key: "past1m", label: "Last 30 days" },
];

function toDateStr(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function DateRangePicker({
  value,
  onChange,
}: {
  value: AnalyticsRange;
  onChange: (range: AnalyticsRange) => void;
}) {
  const [open, setOpen] = useState(false);
  const [customFrom, setCustomFrom] = useState(() =>
    toDateStr(new Date(Date.now() - 7 * 24 * 60 * 60 * 1000)),
  );
  const [customTo, setCustomTo] = useState(() => toDateStr(new Date()));

  const isCustom = typeof value !== "string";

  function applyPreset(preset: AnalyticsPreset) {
    onChange(preset);
    setOpen(false);
  }

  function applyCustom() {
    const from = new Date(customFrom);
    const to = new Date(customTo);
    if (!Number.isFinite(from.getTime()) || !Number.isFinite(to.getTime())) return;
    if (from > to) return;
    onChange({ from, to });
    setOpen(false);
  }

  return (
    <Popover onOpenChange={setOpen} open={open}>
      <PopoverTrigger asChild>
        <Button className="h-7 gap-1.5 text-xs" size="sm" variant="outline">
          <CalendarDays className="size-3.5" />
          {rangeLabel(value)}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-52 p-0">
        <div className="flex flex-col">
          {PRESETS.map((p) => (
            <button
              className={cn(
                "px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                typeof value === "string" && value === p.key && "bg-muted font-medium",
              )}
              key={p.key}
              onClick={() => applyPreset(p.key)}
              type="button"
            >
              {p.label}
            </button>
          ))}
          <div className="border-t border-border px-3 py-2.5">
            <p className={cn("mb-2 text-xs font-medium", isCustom ? "text-foreground" : "text-muted-foreground")}>
              Custom range
            </p>
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                <span className="w-8 shrink-0 text-xs text-muted-foreground">From</span>
                <input
                  className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs"
                  onChange={(e) => setCustomFrom(e.target.value)}
                  type="date"
                  value={customFrom}
                />
              </div>
              <div className="flex items-center gap-2">
                <span className="w-8 shrink-0 text-xs text-muted-foreground">To</span>
                <input
                  className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs"
                  onChange={(e) => setCustomTo(e.target.value)}
                  type="date"
                  value={customTo}
                />
              </div>
            </div>
            <Button
              className="mt-2 h-7 w-full text-xs"
              onClick={applyCustom}
              size="sm"
              variant="outline"
            >
              Apply
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
