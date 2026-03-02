import { useCallback, useId, useState } from "react";

import {
  Braces,
  ChevronDown,
  Clock,
  Delete as Trash2,
  MessageText as MessageSquare,
  PenSquare as Pencil,
  Robot as BotIcon,
  SortVertical as Filter,
  Sparkle as Sun,
  Target,
  WarningDiamond as AlertCircle,
} from "pixelarticons/react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { usePromptsList } from "@/hooks/use-prompts";
import { cn } from "@/lib/utils";

export type FilterField =
  | "fullTextSearch"
  | "input"
  | "output"
  | "inputKey"
  | "outputKey"
  | "isTrace"
  | "runName"
  | "runType"
  | "latency"
  | "model"
  | "status"
  | "errorMessage"
  | "tag"
  | "metadata"
  | "feedback"
  | "feedbackSource"
  | "runId"
  | "traceId"
  | "threadId"
  | "promptId";

export type FilterOperator =
  | "contains"
  | "equals"
  | "not_equals"
  | "gt"
  | "gte"
  | "lt"
  | "lte"
  | "is";

export interface FilterEntry {
  id: string;
  field: FilterField;
  operator: FilterOperator;
  value: string | number | boolean;
}

const FILTER_FIELDS: {
  id: FilterField;
  label: string;
  icon: React.ReactNode;
  valueType: "text" | "number" | "boolean" | "status" | "prompt";
  defaultOperator: FilterOperator;
}[] = [
  // {
  //   defaultOperator: "contains",
  //   icon: <Search className="size-3.5" />,
  //   id: "fullTextSearch",
  //   label: "Full-Text Search",
  //   valueType: "text",
  // },

  {
    defaultOperator: "equals",
    icon: <Braces className="size-3.5" />,
    id: "model",
    label: "Model",
    valueType: "text",
  },
  {
    defaultOperator: "contains",
    icon: <Braces className="size-3.5" />,
    id: "input",
    label: "Input",
    valueType: "text",
  },
  {
    defaultOperator: "contains",
    icon: <Braces className="size-3.5" />,
    id: "output",
    label: "Output",
    valueType: "text",
  },
  // {
  //   defaultOperator: "contains",
  //   icon: <Braces className="size-3.5" />,
  //   id: "inputKey",
  //   label: "Input Key",
  //   valueType: "text",
  // },
  // {
  //   defaultOperator: "contains",
  //   icon: <Braces className="size-3.5" />,
  //   id: "outputKey",
  //   label: "Output Key",
  //   valueType: "text",
  // },
  // {
  //   defaultOperator: "is",
  //   icon: <Link2 className="size-3.5" />,
  //   id: "isTrace",
  //   label: "Is Trace",
  //   valueType: "boolean",
  // },
  {
    defaultOperator: "contains",
    icon: <Pencil className="size-3.5" />,
    id: "runName",
    label: "Name",
    valueType: "text",
  },
  {
    defaultOperator: "equals",
    icon: <Clock className="size-3.5" />,
    id: "runType",
    label: "Type",
    valueType: "text",
  },
  {
    defaultOperator: "gte",
    icon: <Sun className="size-3.5" />,
    id: "latency",
    label: "Latency",
    valueType: "number",
  },
  // {
  //   defaultOperator: "is",
  //   icon: <AlertCircle className="size-3.5" />,
  //   id: "status",
  //   label: "Status",
  //   valueType: "status",
  // },
  {
    defaultOperator: "contains",
    icon: <AlertCircle className="size-3.5" />,
    id: "errorMessage",
    label: "Error Message",
    valueType: "text",
  },
  // {
  //   defaultOperator: "contains",
  //   icon: <Tag className="size-3.5" />,
  //   id: "tag",
  //   label: "Tag",
  //   valueType: "text",
  // },
  {
    defaultOperator: "contains",
    icon: <Braces className="size-3.5" />,
    id: "metadata",
    label: "Metadata",
    valueType: "text",
  },
  {
    defaultOperator: "gte",
    icon: <MessageSquare className="size-3.5" />,
    id: "feedback",
    label: "Feedback",
    valueType: "number",
  },
  {
    defaultOperator: "equals",
    icon: <Target className="size-3.5" />,
    id: "feedbackSource",
    label: "Feedback Source",
    valueType: "text",
  },
  // {
  //   defaultOperator: "equals",
  //   icon: <Link2 className="size-3.5" />,
  //   id: "runId",
  //   label: "Run ID",
  //   valueType: "text",
  // },
  // {
  //   defaultOperator: "equals",
  //   icon: <Link2 className="size-3.5" />,
  //   id: "traceId",
  //   label: "Trace ID",
  //   valueType: "text",
  // },
  // {
  //   defaultOperator: "equals",
  //   icon: <User className="size-3.5" />,
  //   id: "threadId",
  //   label: "Thread ID",
  //   valueType: "text",
  // },
  {
    defaultOperator: "equals",
    icon: <BotIcon className="size-3.5" />,
    id: "promptId",
    label: "Agent",
    valueType: "prompt",
  },
];

const TEXT_OPERATORS: { value: FilterOperator; label: string }[] = [
  { label: "contains", value: "contains" },
  { label: "equals", value: "equals" },
  { label: "does not equal", value: "not_equals" },
];

const NUMBER_OPERATORS: { value: FilterOperator; label: string }[] = [
  { label: "≥", value: "gte" },
  { label: ">", value: "gt" },
  { label: "≤", value: "lte" },
  { label: "<", value: "lt" },
  { label: "equals", value: "equals" },
  { label: "≠", value: "not_equals" },
];

const STATUS_OPTIONS = [
  { label: "All", value: "all" },
  { label: "Completed", value: "completed" },
  { label: "Error / Failed", value: "error" },
];

function generateId() {
  return `filter-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

const FILTER_OPERATORS_SET = new Set<string>([
  "contains",
  "equals",
  "not_equals",
  "gt",
  "gte",
  "lt",
  "lte",
  "is",
]);

/** Map FilterField to URL param name (e.g. promptId -> agent, status -> trace_status to avoid toolbar conflict) */
const FIELD_TO_PARAM: Record<FilterField, string> = {
  errorMessage: "error_message",
  feedback: "feedback",
  feedbackSource: "feedback_source",
  fullTextSearch: "full_text_search",
  input: "input",
  inputKey: "input_key",
  isTrace: "is_trace",
  latency: "latency",
  metadata: "metadata",
  output: "output",
  outputKey: "output_key",
  promptId: "agent",
  runId: "run_id",
  runName: "run_name",
  runType: "run_type",
  status: "trace_status",
  tag: "tag",
  threadId: "thread_id",
  traceId: "trace_id",
  model: "model",
};

const PARAM_TO_FIELD: Record<string, FilterField> = Object.fromEntries(
  (Object.entries(FIELD_TO_PARAM) as [FilterField, string][]).map(([k, v]) => [v, k])
);

type FilterSearchParams = Record<string, string | undefined>;

/** Parse filters from URL search params (agent=, run_type=, latency=, etc. with _op suffix for operator) */
export function parseFiltersFromSearchParams(params: FilterSearchParams): FilterEntry[] {
  const result: FilterEntry[] = [];
  const defByField = Object.fromEntries(
    (FILTER_FIELDS as { id: FilterField; defaultOperator: FilterOperator }[]).map((f) => [
      f.id,
      f.defaultOperator,
    ])
  );
  for (const [param, field] of Object.entries(PARAM_TO_FIELD)) {
    const value = params[param];
    if (value === undefined) continue;
    const opParam = params[`${param}_op`];
    const operator = (
      opParam && FILTER_OPERATORS_SET.has(opParam) ? opParam : defByField[field]
    ) as FilterOperator;
    const parsedValue =
      value === "true"
        ? true
        : value === "false"
          ? false
          : /^-?\d+(\.\d+)?$/.test(value)
            ? Number(value)
            : value;
    result.push({
      field,
      id: `filter-${param}-${field}`,
      operator,
      value: parsedValue,
    });
  }
  return result;
}

/**
 * Serialize filters to a URL search-params delta.
 *
 * Returns ONLY filter-related keys (cleared to undefined, or set to a value).
 * The caller is responsible for merging this with the rest of the search state
 * (e.g. via `setSearch({ ...filterDelta, page: 1 })`).
 */
export function serializeFiltersToSearchParams(filters: FilterEntry[]): FilterSearchParams {
  // Start by clearing every possible filter param so stale values are removed
  const result: FilterSearchParams = {};
  for (const param of Object.values(FIELD_TO_PARAM)) {
    result[param] = undefined;
    result[`${param}_op`] = undefined;
  }
  for (const f of filters) {
    const param = FIELD_TO_PARAM[f.field];
    if (!param) continue;
    result[param] = String(f.value);
    const def = FILTER_FIELDS.find((x) => x.id === f.field);
    if (def && f.operator !== def.defaultOperator) {
      result[`${param}_op`] = f.operator;
    }
  }
  return result;
}

// ---------------------------------------------------------------------------
// Backend query builder
// ---------------------------------------------------------------------------

/** Map a frontend FilterOperator to a backend operator token. */
function _backendOp(op: FilterOperator): string {
  switch (op) {
    case "contains":
      return "ilike";
    case "equals":
      return "eq";
    case "not_equals":
      return "neq";
    case "gt":
      return "gt";
    case "gte":
      return "gte";
    case "lt":
      return "lt";
    case "lte":
      return "lte";
    case "is":
      return "eq";
    default:
      return "eq";
  }
}

/** Wrap *val* in `%` wildcards when the operator is `contains` (ilike). */
function _wildcard(op: FilterOperator, val: string): string {
  return op === "contains" ? `%${val}%` : val;
}

/**
 * Convert frontend filter state into backend `filter` query strings.
 *
 * The backend accepts repeated `?filter=field;operator;value` params
 * (format defined in `trace_filter_backend.py`).
 *
 * @param filters  Active filter entries from the filter panel.
 * @param opts.q   The free-text search bar value — mapped to `operation;ilike;%val%`.
 * @param opts.status  The status dropdown value ("all" | "completed" | "error").
 */
export function filtersToBackendQuery(
  filters: FilterEntry[],
  opts?: { q?: string; status?: string }
): string[] {
  const result: string[] = [];

  // Free-text search bar → operation name ilike search
  if (opts?.q) {
    result.push(`operation;ilike;%${opts.q}%`);
  }

  // Status dropdown
  if (opts?.status && opts.status !== "all") {
    result.push(`status;eq;${opts.status === "completed" ? "ok" : "error"}`);
  }

  for (const f of filters) {
    const val = String(f.value);
    // Skip empty values (except explicit 0 or false)
    if (!val && f.value !== 0 && f.value !== false) continue;

    switch (f.field) {
      // ── Name / text ───────────────────────────────────────────────────────
      case "fullTextSearch":
      case "runName": {
        const op = _backendOp(f.operator);
        result.push(`operation;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Status ────────────────────────────────────────────────────────────
      case "status": {
        if (val === "all") break;
        result.push(`status;eq;${val === "completed" ? "ok" : "error"}`);
        break;
      }

      // ── Latency (frontend: seconds → backend: milliseconds) ───────────────
      case "latency": {
        const ms = Math.round(Number(f.value) * 1000);
        result.push(`duration_ms;${_backendOp(f.operator)};${ms}`);
        break;
      }

      // ── Trace / run ID ────────────────────────────────────────────────────
      case "traceId":
      case "runId": {
        const op = _backendOp(f.operator);
        result.push(`trace_id;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Error message ─────────────────────────────────────────────────────
      case "errorMessage": {
        const op = _backendOp(f.operator);
        result.push(`span_attr.status_message;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Is root span ──────────────────────────────────────────────────────
      case "isTrace":
        result.push(`parent_span_id;isnull;${f.value ? "true" : "false"}`);
        break;

      // ── Run type → TraceModel.source ──────────────────────────────────────
      case "runType": {
        const op = _backendOp(f.operator);
        result.push(`source;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Thread ID → span_attr.thread_id ──────────────────────────────────
      case "threadId": {
        const op = _backendOp(f.operator);
        result.push(`span_attr.thread_id;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Prompt / agent ID ─────────────────────────────────────────────────
      case "promptId": {
        const op = f.operator === "not_equals" ? "neq" : "eq";
        result.push(`prompt_id;${op};${val}`);
        break;
      }

      // ── Feedback source ───────────────────────────────────────────────────
      case "feedbackSource": {
        const op = _backendOp(f.operator);
        result.push(`span_attr.feedback_source;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Input / output content ────────────────────────────────────────────
      case "input": {
        const op = _backendOp(f.operator);
        result.push(`input_text;${op};${_wildcard(f.operator, val)}`);
        break;
      }
      case "output": {
        const op = _backendOp(f.operator);
        result.push(`output_text;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // ── Input / output key presence ───────────────────────────────────────
      case "inputKey":
        // Search for a quoted JSON key in the serialised input text
        result.push(`input_text;ilike;%"${val}"%`);
        break;
      case "outputKey":
        result.push(`output_text;ilike;%"${val}"%`);
        break;

      // ── Metadata / tag → full span-attribute text ─────────────────────────
      case "metadata":
      case "tag": {
        const op = _backendOp(f.operator);
        result.push(`metadata_text;${op};${_wildcard(f.operator, val)}`);
        break;
      }

      // feedback score is a complex nested JSONB — skip backend mapping
      case "feedback":
        break;
    }
  }

  return result;
}

export interface TracesFiltersProps {
  filters: FilterEntry[];
  onFiltersChange: (filters: FilterEntry[]) => void;
  className?: string;
  projectId?: string;
}

export function TracesFilters({ filters, onFiltersChange, projectId }: TracesFiltersProps) {
  const addFilterId = useId();
  const [addFilterOpen, setAddFilterOpen] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const { data: prompts = [] } = usePromptsList(projectId);

  const addFilter = useCallback(
    (field: FilterField) => {
      const def = FILTER_FIELDS.find((f) => f.id === field);
      if (!def) return;
      const entry: FilterEntry = {
        field,
        id: generateId(),
        operator: def.defaultOperator,
        value:
          def.valueType === "boolean"
            ? true
            : def.valueType === "number"
              ? 0
              : def.valueType === "status"
                ? "all"
                : def.valueType === "prompt"
                  ? ""
                  : "",
      };
      onFiltersChange([...filters, entry]);
      setAddFilterOpen(false);
    },
    [filters, onFiltersChange]
  );

  const updateFilter = useCallback(
    (id: string, updates: Partial<FilterEntry>) => {
      onFiltersChange(filters.map((f) => (f.id === id ? { ...f, ...updates } : f)));
    },
    [filters, onFiltersChange]
  );

  const removeFilter = useCallback(
    (id: string) => {
      onFiltersChange(filters.filter((f) => f.id !== id));
    },
    [filters, onFiltersChange]
  );

  const handleAddFilter = useCallback(
    (field: FilterField) => {
      addFilter(field);
      setAddFilterOpen(false);
    },
    [addFilter]
  );

  return (
    <Popover onOpenChange={setDropdownOpen} open={dropdownOpen}>
      <PopoverTrigger asChild>
        <Button
          className={cn(
            "gap-1.5 h-9 text-xs",
            filters.length > 0 && "border-primary/50 text-primary"
          )}
          size="sm"
          variant="outline"
        >
          <Filter className="size-3.5" />
          {filters.length === 0
            ? "Filters"
            : `${filters.length} filter${filters.length === 1 ? "" : "s"}`}
          <ChevronDown className="size-3.5 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-auto min-w-[360px] max-w-[90vw] p-0 text-xs">
        <div className="max-h-[min(60vh,360px)] overflow-y-auto overflow-x-hidden p-3">
          <p className="text-sm font-medium text-muted-foreground">Filters</p>
          <div className="mt-1.5 space-y-1.5">
            {filters.map((entry, idx) => (
              <div className="space-y-1.5" key={entry.id}>
                {idx > 0 && <p className="text-[11px] font-medium text-muted-foreground">And</p>}
                <FilterUI
                  entry={entry}
                  onRemove={() => removeFilter(entry.id)}
                  onUpdate={(u) => updateFilter(entry.id, u)}
                  prompts={prompts}
                />
              </div>
            ))}

            <div className="pt-1.5">
              {filters.length > 0 && (
                <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">And</p>
              )}
              <DropdownMenu onOpenChange={setAddFilterOpen} open={addFilterOpen}>
                <DropdownMenuTrigger asChild>
                  <button
                    className="flex w-fit items-center gap-1.5 rounded-md border border-dashed border-border bg-muted/30 px-2.5 py-1.5 text-left text-xs text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
                    id={addFilterId}
                    type="button"
                  >
                    {filters.length > 0 ? "+ And" : "Field"}
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="w-56 p-0 text-xs">
                  <div className="max-h-[200px] overflow-y-auto p-1">
                    {FILTER_FIELDS.map((f) => (
                      <DropdownMenuItem
                        className="gap-1.5 cursor-pointer text-xs"
                        key={f.id}
                        onClick={() => handleAddFilter(f.id)}
                      >
                        {f.icon}
                        {f.label}
                      </DropdownMenuItem>
                    ))}
                  </div>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}

interface PromptOption {
  promptId: string;
  slug: string;
  version: number;
}

/** Flatten metadata attributes to a searchable string (keys and values) */
function metadataToSearchableString(attrs: Record<string, unknown>): string {
  if (!attrs || typeof attrs !== "object") return "";
  return Object.entries(attrs)
    .flatMap(([k, v]) => [k, String(v ?? "")])
    .join(" ")
    .toLowerCase();
}

/**
 * Flatten any input/output value to a plain searchable string.
 *
 * Handles:
 *  - null / undefined → ""
 *  - plain string → returned as-is
 *  - array of chat messages [{role, content}] → joined content strings
 *  - any other object / array → JSON.stringify fallback
 */
function ioToSearchableString(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    if (Array.isArray(parsed)) {
      return parsed
        .map((msg) => {
          if (msg && typeof msg === "object") {
            const content = (msg as Record<string, unknown>).content;
            return content != null ? String(content) : JSON.stringify(msg);
          }
          return String(msg);
        })
        .join(" ");
    }
    if (typeof parsed === "object" && parsed !== null) {
      return JSON.stringify(parsed);
    }
    return String(parsed);
  } catch {
    return String(value);
  }
}

/** Apply filter logic to a list of traces (client-side) */
export function applyTracesFilters<T>(
  traces: T[],
  filters: FilterEntry[],
  options?: {
    getInput?: (t: T) => unknown;
    getOutput?: (t: T) => unknown;
    getNodeName?: (t: T) => string;
    getDurationNano?: (t: T) => number;
    getStatus?: (t: T) => string;
    getTraceId?: (t: T) => string;
    getAttributes?: (t: T) => Record<string, unknown>;
    getStatusMessage?: (t: T) => string;
    getPromptId?: (t: T) => string;
    getIsRoot?: (t: T) => boolean;
  }
): T[] {
  if (filters.length === 0) return traces;

  const getInput = options?.getInput ?? (() => null);
  const getOutput = options?.getOutput ?? (() => null);
  const getNodeName =
    options?.getNodeName ?? ((t: T) => String((t as Record<string, unknown>).nodeName ?? ""));
  const getDurationNano =
    options?.getDurationNano ??
    ((t: T) => Number((t as Record<string, unknown>).duration_nano ?? 0));
  const getStatus =
    options?.getStatus ?? ((t: T) => String((t as Record<string, unknown>).status ?? ""));
  const getTraceId =
    options?.getTraceId ?? ((t: T) => String((t as Record<string, unknown>).trace_id ?? ""));
  const getAttributes =
    options?.getAttributes ??
    ((t: T) => ((t as Record<string, unknown>).attributes ?? {}) as Record<string, unknown>);
  const getStatusMessage =
    options?.getStatusMessage ??
    ((t: T) => String((t as Record<string, unknown>).status_message ?? ""));
  const getPromptId =
    options?.getPromptId ??
    ((t: T) => {
      const attrs = getAttributes(t);
      return String(attrs?.prompt_id ?? attrs?.promptId ?? "");
    });
  const getIsRoot = options?.getIsRoot ?? ((t: T) => !(t as Record<string, unknown>).parentSpanId);

  return traces.filter((trace) => {
    return filters.every((f) => {
      const val = f.value;
      const strVal = String(val).toLowerCase();
      switch (f.field) {
        case "fullTextSearch": {
          if (!strVal) return true;
          const inputStr = ioToSearchableString(getInput(trace));
          const outputStr = ioToSearchableString(getOutput(trace));
          const full = [getNodeName(trace), getTraceId(trace), inputStr, outputStr]
            .join(" ")
            .toLowerCase();
          return f.operator === "contains" ? full.includes(strVal) : full === strVal;
        }
        case "runName":
          return applyTextOp(getNodeName(trace), val, f.operator);
        case "status": {
          if (val === "all") return true;
          const s = getStatus(trace).toLowerCase();
          if (val === "completed") return s === "completed" || s === "ok";
          if (val === "error") return /error|failed|exception/i.test(s);
          return true;
        }
        case "latency": {
          const durNano = getDurationNano(trace);
          const thresholdNano = Number(val) * 1_000_000_000;
          if (f.operator === "gte") return durNano >= thresholdNano;
          if (f.operator === "gt") return durNano > thresholdNano;
          if (f.operator === "lte") return durNano <= thresholdNano;
          if (f.operator === "lt") return durNano < thresholdNano;
          return durNano === thresholdNano;
        }
        case "traceId":
        case "runId":
          return applyTextOp(getTraceId(trace), val, f.operator);
        case "threadId": {
          const attrs = getAttributes(trace);
          const threadId = String(
            attrs?.thread_id ?? attrs?.threadId ?? attrs?.conversation_id ?? ""
          );
          return applyTextOp(threadId, val, f.operator);
        }
        case "isTrace":
          return val === true ? getIsRoot(trace) : !getIsRoot(trace);
        case "promptId": {
          const tracePromptId = getPromptId(trace);
          if (!val) return true;
          if (f.operator === "equals") return tracePromptId === val;
          if (f.operator === "not_equals") return tracePromptId !== val;
          return tracePromptId === val;
        }
        case "input": {
          const contentStr = ioToSearchableString(getInput(trace));
          return applyTextOp(contentStr, val, f.operator);
        }
        case "output": {
          const contentStr = ioToSearchableString(getOutput(trace));
          return applyTextOp(contentStr, val, f.operator);
        }
        case "inputKey": {
          const inputVal = getInput(trace);
          if (inputVal == null) return false;
          try {
            const obj = typeof inputVal === "string" ? JSON.parse(inputVal) : inputVal;
            if (obj && typeof obj === "object" && !Array.isArray(obj)) {
              return f.operator === "not_equals"
                ? !(strVal in (obj as object))
                : strVal in (obj as object) ||
                    Object.keys(obj as object).some((k) => k.toLowerCase().includes(strVal));
            }
          } catch {
            /* fall through */
          }
          return false;
        }
        case "outputKey": {
          const outputVal = getOutput(trace);
          if (outputVal == null) return false;
          try {
            const obj = typeof outputVal === "string" ? JSON.parse(outputVal) : outputVal;
            if (obj && typeof obj === "object" && !Array.isArray(obj)) {
              return f.operator === "not_equals"
                ? !(strVal in (obj as object))
                : strVal in (obj as object) ||
                    Object.keys(obj as object).some((k) => k.toLowerCase().includes(strVal));
            }
          } catch {
            /* fall through */
          }
          return false;
        }
        case "metadata":
        case "tag": {
          const attrs = getAttributes(trace);
          const searchable = metadataToSearchableString(attrs);
          return applyTextOp(searchable, val, f.operator);
        }
        case "errorMessage": {
          const msg = getStatusMessage(trace);
          return applyTextOp(msg, val, f.operator);
        }
        default:
          return true;
      }
    });
  });
}

function applyTextOp(
  source: string,
  value: string | number | boolean,
  op: FilterOperator
): boolean {
  const s = source.toLowerCase();
  const v = String(value).toLowerCase();
  if (!v) return true;
  switch (op) {
    case "contains":
      return s.includes(v);
    case "equals":
      return s === v;
    case "not_equals":
      return s !== v;
    default:
      return s.includes(v);
  }
}

function FilterUI({
  entry,
  prompts,
  onUpdate,
  onRemove,
}: {
  entry: FilterEntry;
  prompts: PromptOption[];
  onUpdate: (u: Partial<FilterEntry>) => void;
  onRemove: () => void;
}) {
  const def = FILTER_FIELDS.find((f) => f.id === entry.field);
  if (!def) return null;

  const operators =
    def.valueType === "text"
      ? TEXT_OPERATORS
      : def.valueType === "number"
        ? NUMBER_OPERATORS
        : def.valueType === "prompt"
          ? [
              { label: "is", value: "equals" as FilterOperator },
              { label: "is not", value: "not_equals" as FilterOperator },
            ]
          : [{ label: "is", value: "is" as FilterOperator }];

  const handleFieldChange = (newFieldId: string) => {
    const newDef = FILTER_FIELDS.find((f) => f.id === newFieldId);
    if (!newDef) return;
    const defaultValue =
      newDef.valueType === "boolean"
        ? true
        : newDef.valueType === "number"
          ? 0
          : newDef.valueType === "status"
            ? "all"
            : "";
    onUpdate({
      field: newFieldId as FilterField,
      operator: newDef.defaultOperator,
      value: defaultValue,
    });
  };

  return (
    <div className="flex flex-nowrap items-center gap-1 bg-muted/20 p-1">
      <Select onValueChange={handleFieldChange} value={entry.field}>
        <SelectTrigger className="w-auto min-w-[72px] text-[11px]" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {FILTER_FIELDS.map((f) => (
            <SelectItem className="text-xs" key={f.id} value={f.id}>
              {f.icon}
              {f.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select
        onValueChange={(v) => onUpdate({ operator: v as FilterOperator })}
        value={entry.operator}
      >
        <SelectTrigger className="w-auto min-w-[62px] text-[11px]" size="sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {operators.map((o) => (
            <SelectItem className="text-xs" key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {def.valueType === "boolean" ? (
        <Select
          onValueChange={(v) => onUpdate({ value: v === "true" })}
          value={String(entry.value)}
        >
          <SelectTrigger className="w-[80px] text-[11px]" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="true">true</SelectItem>
            <SelectItem value="false">false</SelectItem>
          </SelectContent>
        </Select>
      ) : def.valueType === "status" ? (
        <Select onValueChange={(v) => onUpdate({ value: v })} value={String(entry.value)}>
          <SelectTrigger className="w-[120px] text-[11px]" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : def.valueType === "prompt" ? (
        <Select
          onValueChange={(v) => onUpdate({ value: v === "all_prompts" ? "" : v })}
          value={entry.value ? String(entry.value) : "all_prompts"}
        >
          <SelectTrigger className="min-w-[140px] w-auto text-[11px]" size="sm">
            <SelectValue placeholder="Select Agent..." />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all_prompts">All Agents</SelectItem>
            {prompts.map((p) => (
              <SelectItem key={p.promptId} value={p.promptId}>
                {p.slug} (v{p.version})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : def.valueType === "number" && entry.field === "latency" ? (
        <div className="flex items-center gap-1.5 px-1">
          <Slider
            className="w-20"
            max={60}
            onValueChange={([v]) => onUpdate({ value: v })}
            step={0.5}
            value={[Number(entry.value) || 0]}
          />
          <span className="text-[11px] text-muted-foreground">{Number(entry.value) || 0}s</span>
        </div>
      ) : def.valueType === "number" ? (
        <Input
          className="h-8 w-20 text-[11px]"
          onChange={(e) => onUpdate({ value: e.target.value ? Number(e.target.value) : 0 })}
          type="number"
          value={entry.value as number}
        />
      ) : (
        <Input
          className="h-8 w-[150px] shrink-0 text-[11px]"
          onChange={(e) => onUpdate({ value: e.target.value })}
          placeholder="Enter value..."
          value={String(entry.value)}
        />
      )}

      <Button
        aria-label="Remove filter"
        className="ml-auto size-6 shrink-0"
        onClick={onRemove}
        size="icon"
        variant="ghost"
      >
        <Trash2 className="size-3" />
      </Button>
    </div>
  );
}
