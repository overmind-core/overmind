import { useCallback, useEffect, useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import type { Table } from "@tanstack/react-table";

import apiClient from "@/client";
import {
  ANY_VALUE,
  describeFilter,
  type FilterEntry,
  TracesFilters,
} from "@/components/traces/filters";
import { DataTableViewOptions } from "@/components/traces/table-column-toggle";
import { Button } from "@/components/ui/button";
import { Chip } from "@/components/ui/chip";
import { DateTime } from "@/components/ui/datetime";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { ListToolbar } from "@/components/ui/list-toolbar";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { SearchInput } from "@/components/ui/search-input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

interface QuickPreset {
  id: string;
  label: string;
  filters: FilterEntry[];
}

const QUICK_PRESETS: QuickPreset[] = [
  {
    // Deliberately not `span_type=llm_call` (an ingest heuristic default): this asks
    // whether any span reported a model (`?has_model=true`), and stays trace-scoped.
    filters: [{ field: "model", id: "qp-llm", lookup: "eq", value: ANY_VALUE }],
    id: "llm-calls",
    label: "LLM calls",
  },
  {
    filters: [{ field: "status_code", id: "qp-success", lookup: "eq", value: "1" }],
    id: "success",
    label: "Success",
  },
  {
    filters: [{ field: "status_code", id: "qp-error", lookup: "eq", value: "2" }],
    id: "errors",
    label: "Errors",
  },
  {
    filters: [
      {
        field: "min_duration_ms",
        id: "qp-slow",
        lookup: "eq",
        value: "5000",
      },
    ],
    id: "slow",
    label: "Slow (> 5s)",
  },
  {
    filters: [{ field: "total_cost", id: "qp-cost", lookup: "gte", value: "0.01" }],
    id: "expensive",
    label: "Expensive (> $0.01)",
  },
  {
    filters: [
      {
        field: "total_tokens",
        id: "qp-tokens",
        lookup: "gte",
        value: "5000",
      },
    ],
    id: "high-tokens",
    label: "High tokens (≥ 5k)",
  },
];

function presetIsActive(existing: FilterEntry[], needed: FilterEntry[]): boolean {
  return needed.every((n) =>
    existing.some((e) => e.field === n.field && e.lookup === n.lookup && e.value === n.value)
  );
}

/** Mirrors the `?view=` search param. */
export type TracesViewMode = "executions" | "roots" | "sessions";

const VIEW_LABELS: Record<TracesViewMode, string> = {
  executions: "Task executions",
  roots: "Root traces",
  sessions: "Sessions",
};

const VIEW_MODES: TracesViewMode[] = ["executions", "roots", "sessions"];

const ALL_CAPABILITIES = "__all__";

interface TracesTableToolbarProps<TData> {
  table: Table<TData>;
  searchValue: string;
  onSearchChange: (value: string) => void;

  timeRange: TimeRangePreset;
  customTimeRange: { gte?: string; lte?: string };
  onTimeRangeChange: (preset: TimeRangePreset, custom?: { gte?: string; lte?: string }) => void;

  status: "all" | "success" | "error";
  onStatusChange: (value: "all" | "success" | "error") => void;

  filters: FilterEntry[];
  onFiltersChange: (filters: FilterEntry[]) => void;

  view: TracesViewMode;
  onViewChange: (value: TracesViewMode) => void;

  groupByConversation?: boolean;
  onGroupByConversationChange?: (value: boolean) => void;

  projectId: string;

  onResetAll: () => void;
  onRefresh: () => void;
  isRefetching: boolean;
  isFetching: boolean;
  /** Epoch ms of the last successful fetch — undefined before the first one lands. */
  lastUpdatedAt?: number;
  /** Epoch ms this view's poll is next due; omitted once polling has backed off. */
  nextPollAt?: number;
}

export function TracesTableToolbar<TData>({
  table,
  searchValue,
  onSearchChange,
  timeRange,
  customTimeRange,
  onTimeRangeChange,
  status,
  filters,
  onFiltersChange,
  view,
  onViewChange,
  groupByConversation = true,
  onGroupByConversationChange,
  projectId,
  onResetAll,
  onRefresh,
  isRefetching,
  isFetching,
  lastUpdatedAt,
  nextPollAt,
}: TracesTableToolbarProps<TData>) {
  const [spinning, setSpinning] = useState(false);
  const isSessionsView = view === "sessions";
  const isExecutionsView = view === "executions";

  // The poll cadence is shorter than a minute, so an absolute clock time reads
  // as identical to "updated" — count down instead.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (nextPollAt == null) return;
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(tick);
  }, [nextPollAt]);
  const secondsToNextPoll =
    nextPollAt == null ? 0 : Math.max(0, Math.round((nextPollAt - now) / 1000));

  // Sessions filter by a plain select (the span filter builder doesn't apply to
  // session rows), but reuse the traces `capability` entry so the choice survives a
  // view switch.
  const capabilityFilterValue =
    filters.find((f) => f.field === "capability" && f.lookup === "eq")?.value || undefined;
  // Also fetched in trace views, so the filter chip can show a name, not a UUID.
  const { data: capabilitiesData } = useQuery({
    enabled:
      !!projectId &&
      (isSessionsView || isExecutionsView || filters.some((f) => f.field === "capability")),
    queryFn: () => apiClient.capabilities.capabilitiesList({ pageSize: 100, project: projectId }),
    queryKey: ["capabilities", projectId],
  });
  const capabilityNameById = useMemo(
    () => new Map((capabilitiesData?.results ?? []).map((a) => [a.id, a.name])),
    [capabilitiesData]
  );
  const handleCapabilitySelect = useCallback(
    (value: string) => {
      const next = filters.filter((f) => f.field !== "capability");
      if (value !== ALL_CAPABILITIES) {
        next.push({ field: "capability", id: `f-capability-${Date.now()}`, lookup: "eq", value });
      }
      onFiltersChange(next);
    },
    [filters, onFiltersChange]
  );

  function handleRefresh() {
    if (spinning) return;
    setSpinning(true);
    onRefresh();
  }

  const removeFilter = useCallback(
    (id: string) => onFiltersChange(filters.filter((f) => f.id !== id)),
    [filters, onFiltersChange]
  );

  const applyPreset = useCallback(
    (preset: QuickPreset) => {
      const isActive = presetIsActive(filters, preset.filters);
      if (isActive) {
        const next = filters.filter(
          (f) =>
            !preset.filters.some(
              (p) => p.field === f.field && p.lookup === f.lookup && p.value === f.value
            )
        );
        onFiltersChange(next);
        return;
      }
      const stripped = filters.filter(
        (f) => !preset.filters.some((p) => p.field === f.field && p.lookup === f.lookup)
      );
      onFiltersChange([
        ...stripped,
        ...preset.filters.map((p) => ({ ...p, id: `${p.id}-${Date.now()}` })),
      ]);
    },
    [filters, onFiltersChange]
  );

  const hasAnyState = useMemo(
    () => filters.length > 0 || !!searchValue || timeRange !== "all" || status !== "all",
    [filters.length, searchValue, status, timeRange]
  );

  return (
    <div className="flex w-full min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Tabs
          className="w-auto shrink-0"
          onValueChange={(value) => onViewChange(value as TracesViewMode)}
          value={view}
        >
          <TabsList>
            {VIEW_MODES.map((mode) => (
              <TabsTrigger key={mode} value={mode}>
                {VIEW_LABELS[mode]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>

        {lastUpdatedAt != null && (
          <span className="ml-auto flex items-center gap-1.5 whitespace-nowrap text-xs text-muted-foreground">
            <span className={cn("size-1.5 rounded-xs bg-success", isFetching && "animate-pulse")} />
            {isFetching ? (
              "Fetching…"
            ) : (
              <>
                Updated <DateTime value={lastUpdatedAt} />
                {secondsToNextPoll > 0 && ` · next in ${secondsToNextPoll}s`}
              </>
            )}
          </span>
        )}
      </div>

      <ListToolbar
        actions={
          <ToolbarTooltip label="Refresh traces">
            <Button
              aria-label="Refresh traces"
              disabled={spinning}
              onClick={handleRefresh}
              size="icon"
              variant="secondary"
            >
              <Icon.refresh
                className={spinning ? "animate-spin" : ""}
                onAnimationIteration={() => {
                  if (!isRefetching) setSpinning(false);
                }}
              />
            </Button>
          </ToolbarTooltip>
        }
        filters={
          isSessionsView ? (
            <>
              <Select
                onValueChange={handleCapabilitySelect}
                value={capabilityFilterValue ?? ALL_CAPABILITIES}
              >
                <SelectTrigger
                  aria-label="Filter sessions by capability"
                  className="w-[200px]"
                  size="default"
                >
                  <SelectValue placeholder="All capabilities" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_CAPABILITIES}>All capabilities</SelectItem>
                  {(capabilitiesData?.results ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <DataTableViewOptions table={table} />
            </>
          ) : (
            <>
              <TracesFilters
                filters={filters}
                onFiltersChange={onFiltersChange}
                projectId={projectId}
              />
              {isExecutionsView && onGroupByConversationChange && (
                <Chip
                  onClick={() => onGroupByConversationChange(!groupByConversation)}
                  selected={groupByConversation}
                >
                  Group by conversation
                </Chip>
              )}
              <DataTableViewOptions table={table} />
            </>
          )
        }
        hasActiveFilters={hasAnyState}
        onClearFilters={onResetAll}
        primary={
          !isSessionsView && (
            <TimeRangeButton
              customTimeRange={customTimeRange}
              onChange={onTimeRangeChange}
              value={timeRange}
            />
          )
        }
        search={
          <SearchInput
            className="min-w-[220px] flex-1"
            label={
              isSessionsView
                ? "Search sessions"
                : isExecutionsView
                  ? "Search executions"
                  : "Search traces"
            }
            onChange={(e) => onSearchChange(e.target.value)}
            onClear={() => onSearchChange("")}
            placeholder={
              isSessionsView
                ? "Search sessions…"
                : isExecutionsView
                  ? "Search executions…"
                  : "Search traces…"
            }
            value={searchValue}
          />
        }
        shareable
      >
        {!isSessionsView && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-0.5 text-xs font-medium text-muted-foreground">Quick filters</span>
            {QUICK_PRESETS.map((p) => {
              const active = presetIsActive(filters, p.filters);
              return (
                <Chip key={p.id} onClick={() => applyPreset(p)} selected={active}>
                  {p.label}
                </Chip>
              );
            })}
          </div>
        )}

        {!isSessionsView && filters.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            {filters.map((f) => (
              <span
                className="chip-label inline-flex h-7 items-center gap-1.5 rounded-sm border border-primary/40 bg-primary/5 px-2.5 text-xs text-primary"
                key={f.id}
              >
                {describeFilter(
                  f,
                  f.field === "capability" ? capabilityNameById.get(f.value) : undefined
                )}
                <ToolbarTooltip label="Remove filter">
                  <button
                    aria-label={`Remove filter ${f.field}`}
                    className="-mr-1 rounded-sm p-0.5 hover:bg-primary/20"
                    onClick={() => removeFilter(f.id)}
                    type="button"
                  >
                    <Icon.close className="size-3" />
                  </button>
                </ToolbarTooltip>
              </span>
            ))}
          </div>
        )}
      </ListToolbar>
    </div>
  );
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
        <div className="mt-3 border-t border-border/70 pt-3">
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

/** From/To datetime triggers: clear button affordance; white surface in dark mode. */
const DATETIME_TRIGGER_CLASS = cn(
  "h-8 cursor-pointer rounded-sm border border-border bg-background px-2 text-xs font-medium transition-colors",
  "hover:bg-accent/60 focus-visible:ring-[2px] focus-visible:ring-ring/60",
  NATIVE_DATE_TRIGGER,
  "dark:[color-scheme:light]"
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
