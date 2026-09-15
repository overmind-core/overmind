// Filters serialise to django-filter params: `eq` → ?field=value, every other
// lookup → ?field__lookup=value. Backend lookups: `overbae/api/filters.py`.

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Icon } from "@/components/ui/icons";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { UNBOUND_CAPABILITY } from "@/hooks/use-traces";
import { cn } from "@/lib/utils";

type FilterLookup = "eq" | "icontains" | "gte" | "lte" | "gt" | "lt" | "isnull";

export type FilterField =
  | "capability"
  | "model"
  | "service_name"
  | "operation"
  | "span_type"
  | "status_code"
  | "min_duration_ms"
  | "max_duration_ms"
  | "total_tokens"
  | "total_cost"
  | "trace_id";

type FilterValueType = "text" | "number" | "uuid" | "capability" | "model" | "boolean";

interface FilterOption {
  value: string;
  label: string;
}

export interface FilterEntry {
  /** Stable ID, only used for React keys / list management. */
  id: string;
  field: FilterField;
  lookup: FilterLookup;
  value: string;
}

interface FieldDef {
  id: FilterField;
  param: string;
  label: string;
  icon: React.ReactNode;
  valueType: FilterValueType;
  lookups: FilterLookup[];
  defaultLookup: FilterLookup;
  placeholder?: string;
  options?: FilterOption[];
}

/** Sentinel for "no value" — Radix `SelectItem` cannot hold an empty string. */
export const ANY_VALUE = "__any__";

/**
 * `Span.span_type` has no DB-level `choices` and OTLP ingest stores the SDK's
 * `overmind.span.type` verbatim, so this list is the known vocabulary, not a constraint.
 */
const SPAN_TYPE_OPTIONS: FilterOption[] = [
  { label: "LLM call", value: "llm_call" },
  { label: "Tool call", value: "tool_call" },
  { label: "Entry point", value: "entry_point" },
  { label: "Batch", value: "batch" },
];

/** OTel StatusCode. Spans default to UNSET; many producers never set a status. */
const STATUS_CODE_OPTIONS: FilterOption[] = [
  { label: "Unset", value: "0" },
  { label: "OK", value: "1" },
  { label: "Error", value: "2" },
];

const FIELDS: FieldDef[] = [
  {
    defaultLookup: "eq",
    icon: <Icon.capability className="size-3.5" />,
    id: "capability",
    label: "Capability",
    lookups: ["eq"],
    param: "capability",
    valueType: "capability",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.model className="size-3.5" />,
    id: "model",
    label: "Model",
    lookups: ["eq"],
    param: "model",
    valueType: "model",
  },
  {
    defaultLookup: "icontains",
    icon: <Icon.code className="size-3.5" />,
    id: "service_name",
    label: "Service name",
    lookups: ["icontains", "eq"],
    param: "service_name",
    valueType: "text",
  },
  {
    defaultLookup: "icontains",
    icon: <Icon.code className="size-3.5" />,
    id: "operation",
    label: "Operation",
    lookups: ["icontains", "eq"],
    param: "operation",
    valueType: "text",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.components className="size-3.5" />,
    id: "span_type",
    label: "Span type",
    lookups: ["eq"],
    options: SPAN_TYPE_OPTIONS,
    param: "span_type",
    valueType: "text",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.circle className="size-3.5" />,
    id: "status_code",
    label: "Status",
    lookups: ["eq"],
    options: STATUS_CODE_OPTIONS,
    param: "status_code",
    valueType: "number",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.job className="size-3.5" />,
    id: "min_duration_ms",
    label: "Min duration (ms)",
    lookups: ["eq"],
    param: "min_duration_ms",
    placeholder: "e.g. 5000",
    valueType: "number",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.job className="size-3.5" />,
    id: "max_duration_ms",
    label: "Max duration (ms)",
    lookups: ["eq"],
    param: "max_duration_ms",
    placeholder: "e.g. 5000",
    valueType: "number",
  },
  {
    defaultLookup: "gte",
    icon: <Icon.hash className="size-3.5" />,
    id: "total_tokens",
    label: "Total tokens",
    lookups: ["gte", "lte"],
    param: "total_tokens",
    placeholder: "e.g. 5000",
    valueType: "number",
  },
  {
    defaultLookup: "gte",
    icon: <Icon.cost className="size-3.5" />,
    id: "total_cost",
    label: "Total cost (USD)",
    lookups: ["gte", "lte"],
    param: "total_cost",
    placeholder: "e.g. 0.01",
    valueType: "number",
  },
  {
    defaultLookup: "eq",
    icon: <Icon.copy className="size-3.5" />,
    id: "trace_id",
    label: "Trace ID",
    lookups: ["eq"],
    param: "trace_id",
    valueType: "uuid",
  },
];

const FIELDS_BY_ID: Record<FilterField, FieldDef> = Object.fromEntries(
  FIELDS.map((f) => [f.id, f])
) as Record<FilterField, FieldDef>;

const PARAM_TO_FIELD: Record<string, FilterField> = Object.fromEntries(
  FIELDS.map((f) => [f.param, f.id])
);

const LOOKUP_LABEL: Record<FilterLookup, string> = {
  eq: "is",
  gt: ">",
  gte: "≥",
  icontains: "contains",
  isnull: "is empty",
  lt: "<",
  lte: "≤",
};

// Every key that may be written to or cleared from the URL. `has_model` is not a
// `FIELDS` entry (it is the model field's "Any" state) but must still be cleared.
const ALL_FILTER_PARAM_KEYS: string[] = [
  ...FIELDS.flatMap((f) => f.lookups.map((lk) => (lk === "eq" ? f.param : `${f.param}__${lk}`))),
  "has_model",
];

type SearchParams = Record<string, string | undefined>;

function parseFilterParamKey(key: string): { param: string; lookup: FilterLookup } {
  const suffixes: { suffix: string; lookup: FilterLookup }[] = [
    { lookup: "icontains", suffix: "__icontains" },
    { lookup: "isnull", suffix: "__isnull" },
    { lookup: "gte", suffix: "__gte" },
    { lookup: "lte", suffix: "__lte" },
    { lookup: "gt", suffix: "__gt" },
    { lookup: "lt", suffix: "__lt" },
    { lookup: "eq", suffix: "__eq" },
  ];
  for (const { suffix, lookup } of suffixes) {
    if (key.endsWith(suffix)) {
      return { lookup, param: key.slice(0, -suffix.length) };
    }
  }
  return { lookup: "eq", param: key };
}

export function parseFiltersFromSearchParams(params: SearchParams): FilterEntry[] {
  const result: FilterEntry[] = [];
  for (const [key, raw] of Object.entries(params)) {
    if (raw === undefined || raw === "") continue;
    // Nothing serialises the sentinel, so `?field=__any__` is stale or hand-edited:
    // it would show an active chip over results the API returns none of.
    if (raw === ANY_VALUE) continue;
    if (key === "has_model") {
      if (raw === "true") {
        result.push({ field: "model", id: `f-${key}`, lookup: "eq", value: ANY_VALUE });
      }
      continue;
    }
    const { param: paramName, lookup } = parseFilterParamKey(key);
    const fieldId = PARAM_TO_FIELD[paramName];
    if (!fieldId) continue;
    const def = FIELDS_BY_ID[fieldId];
    if (!def.lookups.includes(lookup) && lookup !== "eq") continue;
    result.push({
      field: fieldId,
      id: `f-${key}`,
      lookup,
      value: String(raw),
    });
  }
  return result;
}

/**
 * A search-params delta: every possible filter key reset to `undefined` (wiping
 * stale values), then each active entry set. Caller merges the rest of the search state.
 */
export function serializeFiltersToSearchParams(filters: FilterEntry[]): SearchParams {
  const out: SearchParams = {};
  for (const k of ALL_FILTER_PARAM_KEYS) out[k] = undefined;
  for (const f of filters) {
    const def = FIELDS_BY_ID[f.field];
    if (!def) continue;
    if (f.value === undefined || f.value === "") continue;
    // "Any model" is the `has_model=true` predicate (the trace reported some
    // model), not a value match.
    if (f.field === "model" && f.value === ANY_VALUE) {
      out.has_model = "true";
      continue;
    }
    const key = f.lookup === "eq" ? def.param : `${def.param}__${f.lookup}`;
    out[key] = f.value;
  }
  return out;
}

function newId(): string {
  return `f-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export interface TracesFiltersProps {
  filters: FilterEntry[];
  onFiltersChange: (filters: FilterEntry[]) => void;
  projectId?: string;
  className?: string;
}

export function TracesFilters({
  filters,
  onFiltersChange,
  projectId,
  className,
}: TracesFiltersProps) {
  const addFilterId = useId();
  const [addFieldOpen, setAddFieldOpen] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);

  // Half-filled rows (empty value) live here and only reach the URL once they
  // have a value. Order and IDs must stay stable, or the inputs re-mount on
  // every keystroke.
  const [working, setWorking] = useState<FilterEntry[]>(filters);

  // A row's unmount flush runs from a closure one render behind, so it must not
  // map over a list the user has meanwhile cleared or removed that row from.
  const workingRef = useRef(working);
  workingRef.current = working;

  // Distinguishes our own push echoing back (ignore, or the rows reorder and
  // remount) from an external change — preset chip, reset, deep link.
  const lastPushedRef = useRef<FilterEntry[]>(filters.filter((f) => f.value !== ""));

  useEffect(() => {
    const sig = (xs: FilterEntry[]) =>
      xs
        .map((x) => `${x.field}|${x.lookup}|${x.value}`)
        .sort()
        .join("&");
    if (sig(filters) === sig(lastPushedRef.current)) return;
    lastPushedRef.current = filters;
    setWorking(filters);
  }, [filters]);

  const pushToParent = useCallback(
    (next: FilterEntry[]) => {
      const committed = next.filter((f) => f.value !== "");
      lastPushedRef.current = committed;
      onFiltersChange(committed);
    },
    [onFiltersChange]
  );

  const addFilter = useCallback(
    (fieldId: FilterField) => {
      const def = FIELDS_BY_ID[fieldId];
      if (!def) return;
      // One URL key per field+lookup: a second row on the same pair would replace
      // the first rather than narrow. The next free lookup builds a ≥/≤ band.
      const taken = new Set(working.filter((f) => f.field === fieldId).map((f) => f.lookup));
      const lookup = [def.defaultLookup, ...def.lookups].find((lk) => !taken.has(lk));
      if (!lookup) {
        setAddFieldOpen(false);
        return;
      }
      const entry: FilterEntry = {
        field: fieldId,
        id: newId(),
        lookup,
        value: "",
      };
      const next = [...working, entry];
      setWorking(next);
      setAddFieldOpen(false);
    },
    [working]
  );

  const updateFilter = useCallback(
    (id: string, updates: Partial<FilterEntry>) => {
      const next = workingRef.current.map((f) => (f.id === id ? { ...f, ...updates } : f));
      setWorking(next);
      pushToParent(next);
    },
    [pushToParent]
  );

  const removeFilter = useCallback(
    (id: string) => {
      const next = working.filter((f) => f.id !== id);
      setWorking(next);
      pushToParent(next);
    },
    [working, pushToParent]
  );

  const clearAll = useCallback(() => {
    setWorking([]);
    onFiltersChange([]);
  }, [onFiltersChange]);

  return (
    <TooltipProvider>
      <Popover onOpenChange={setPopoverOpen} open={popoverOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>
              <Button
                className={cn(
                  "gap-1.5",
                  filters.length > 0 && "border-primary/50 text-primary",
                  className
                )}
                size="default"
                variant="secondary"
              >
                <Icon.filter />
                {filters.length === 0
                  ? "Filters"
                  : `${filters.length} filter${filters.length === 1 ? "" : "s"}`}
                <Icon.chevronDown className="opacity-50" />
              </Button>
            </PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="top" sideOffset={6}>
            Add trace filters
          </TooltipContent>
        </Tooltip>
        <PopoverContent align="start" className="w-auto min-w-[420px] max-w-[92vw] p-0 text-xs">
          <div className="max-h-[min(60vh,420px)] overflow-y-auto overflow-x-hidden p-3">
            <p className="text-sm font-medium text-muted-foreground">Filters</p>
            <div className="mt-1.5 space-y-1.5">
              {working.map((entry, idx) => (
                <div className="space-y-1.5" key={entry.id}>
                  {idx > 0 && <p className="text-xs font-medium text-muted-foreground">And</p>}
                  <FilterRow
                    entry={entry}
                    onRemove={() => removeFilter(entry.id)}
                    onUpdate={(u) => updateFilter(entry.id, u)}
                    projectId={projectId}
                  />
                </div>
              ))}

              <div className="pt-1.5">
                {working.length > 0 && (
                  <p className="mb-1.5 text-xs font-medium text-muted-foreground">And</p>
                )}
                <DropdownMenu onOpenChange={setAddFieldOpen} open={addFieldOpen}>
                  <DropdownMenuTrigger asChild>
                    <button
                      className="flex w-fit items-center gap-1.5 rounded-md border border-dashed border-border bg-wash-subtle px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-wash-raised hover:text-foreground"
                      id={addFilterId}
                      type="button"
                    >
                      {working.length > 0 ? "+ And" : "Add field"}
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-60 p-0 text-xs">
                    <div className="max-h-[260px] overflow-y-auto p-1">
                      {FIELDS.map((f) => (
                        <DropdownMenuItem
                          className="gap-1.5 cursor-pointer text-xs"
                          key={f.id}
                          onSelect={(e) => {
                            // `onSelect` fires before Radix tears the menu down,
                            // which keeps the click reliable inside a Popover.
                            e.preventDefault();
                            addFilter(f.id);
                          }}
                        >
                          {f.icon}
                          {f.label}
                        </DropdownMenuItem>
                      ))}
                    </div>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {working.length > 0 && (
                <div className="border-t border-border/70 pt-2">
                  <Button className="text-xs" onClick={clearAll} size="sm" variant="secondary">
                    <Icon.close />
                    Clear filters
                  </Button>
                </div>
              )}
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </TooltipProvider>
  );
}

function FilterRow({
  entry,
  onUpdate,
  onRemove,
  projectId,
}: {
  entry: FilterEntry;
  onUpdate: (u: Partial<FilterEntry>) => void;
  onRemove: () => void;
  projectId?: string;
}) {
  const def = FIELDS_BY_ID[entry.field];

  // Same query key as the sessions-view capability select, so the list is shared.
  const { data: capabilitiesData } = useQuery({
    enabled: def?.valueType === "capability" && !!projectId,
    queryFn: () => apiClient.capabilities.capabilitiesList({ pageSize: 100, project: projectId! }),
    queryKey: ["capabilities", projectId],
  });

  // Read with the same expression `?model=` matches on, so every option returns rows.
  const { data: modelsData } = useQuery({
    enabled: def?.valueType === "model" && !!projectId,
    queryFn: () => apiClient.traces.tracesModelsRetrieve({ project: projectId! }),
    queryKey: ["traces", "models", projectId],
  });

  // 400ms debounce: committing per keystroke would navigate + refetch on every
  // character and leave a history entry per partial value.
  const [draft, setDraft] = useState(entry.value);
  useEffect(() => {
    setDraft(entry.value);
  }, [entry.value]);
  // A ref, so the pending commit survives both `onUpdate` being a fresh arrow on
  // every parent render and `PopoverContent` unmounting the row on close.
  const commit = useRef(() => {});
  useEffect(() => {
    commit.current = () => {
      if (draft !== entry.value) onUpdate({ value: draft });
    };
  }, [draft, entry.value, onUpdate]);
  useEffect(() => {
    if (draft === entry.value) return;
    const timer = setTimeout(() => commit.current(), 400);
    return () => clearTimeout(timer);
  }, [draft, entry.value]);
  useEffect(() => () => commit.current(), []);

  // A value the list doesn't know (an older span type, a hand-edited URL) is
  // appended so the select shows it verbatim rather than going blank.
  const options = useMemo(() => {
    let base = def?.options;
    if (!base && def?.valueType === "capability") {
      base = [
        ...(capabilitiesData?.results ?? []).map((a) => ({ label: a.name, value: a.id })),
        { label: "Unbound", value: UNBOUND_CAPABILITY },
      ];
    }
    if (!base && def?.valueType === "model") {
      base = (modelsData ?? []).map((m) => ({ label: m, value: m }));
    }
    if (
      !base ||
      !entry.value ||
      entry.value === ANY_VALUE ||
      base.some((o) => o.value === entry.value)
    )
      return base;
    return [...base, { label: entry.value, value: entry.value }];
  }, [def, capabilitiesData, modelsData, entry.value]);

  if (!def) return null;

  const handleFieldChange = (newId: string) => {
    const newDef = FIELDS_BY_ID[newId as FilterField];
    if (!newDef) return;
    onUpdate({
      field: newDef.id,
      lookup: newDef.defaultLookup,
      value: "",
    });
  };

  return (
    <div className="flex flex-nowrap items-center gap-1 bg-wash-subtle p-1">
      <Select onValueChange={handleFieldChange} value={entry.field}>
        <SelectTrigger className="w-auto min-w-[110px] text-xs" size="default">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {FIELDS.map((f) => (
            <SelectItem className="text-xs" key={f.id} value={f.id}>
              {f.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select onValueChange={(v) => onUpdate({ lookup: v as FilterLookup })} value={entry.lookup}>
        <SelectTrigger className="w-auto min-w-[78px] text-xs" size="default">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {def.lookups.map((lk) => (
            <SelectItem className="text-xs" key={lk} value={lk}>
              {LOOKUP_LABEL[lk]}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {options ? (
        <Select
          onValueChange={(v) =>
            onUpdate({ value: v === ANY_VALUE ? (def.id === "model" ? ANY_VALUE : "") : v })
          }
          value={entry.value || ANY_VALUE}
        >
          <SelectTrigger className="min-w-[160px] w-auto text-xs" size="default">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY_VALUE}>{`Any ${def.label.toLowerCase()}`}</SelectItem>
            {options.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : def.valueType === "number" ? (
        <Input
          className="w-24 text-xs"
          onBlur={() => commit.current()}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={def.placeholder ?? "0"}
          step="any"
          type="number"
          value={draft}
        />
      ) : (
        <Input
          className="w-[180px] shrink-0 text-xs"
          onBlur={() => commit.current()}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={def.placeholder ?? "Enter value…"}
          value={draft}
        />
      )}

      <Button
        aria-label="Remove filter"
        className="ml-auto shrink-0"
        onClick={onRemove}
        size="icon-xs"
        variant="ghost"
      >
        <Icon.close />
      </Button>
    </div>
  );
}

/** `valueLabel` substitutes a human-readable value (e.g. a capability name for its ID). */
export function describeFilter(entry: FilterEntry, valueLabel?: string): string {
  const def = FIELDS_BY_ID[entry.field];
  const label = def?.label ?? entry.field;
  const op = LOOKUP_LABEL[entry.lookup] ?? entry.lookup;
  const value =
    valueLabel ??
    // Lower-case: this one lands mid-sentence ("Model is any model").
    (entry.value === ANY_VALUE ? `any ${label.toLowerCase()}` : undefined) ??
    def?.options?.find((o) => o.value === entry.value)?.label ??
    entry.value;
  return `${label} ${op} ${value}`;
}

export function useRecentFilterParams(storageKey = "overmind:traces:recentFilters") {
  const get = useCallback((): SearchParams[] => {
    if (typeof window === "undefined") return [];
    try {
      const raw = window.localStorage.getItem(storageKey);
      if (!raw) return [];
      const parsed = JSON.parse(raw) as SearchParams[];
      return Array.isArray(parsed) ? parsed.slice(0, 8) : [];
    } catch {
      return [];
    }
  }, [storageKey]);
  const push = useCallback(
    (entry: SearchParams) => {
      if (typeof window === "undefined") return;
      const meaningful = Object.entries(entry).filter(([, v]) => v !== undefined && v !== "");
      if (meaningful.length === 0) return;
      const current = get();
      const key = JSON.stringify(entry);
      const deduped = current.filter((e) => JSON.stringify(e) !== key);
      const next = [entry, ...deduped].slice(0, 8);
      window.localStorage.setItem(storageKey, JSON.stringify(next));
    },
    [get, storageKey]
  );
  return useMemo(() => ({ get, push }), [get, push]);
}
