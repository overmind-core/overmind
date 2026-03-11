import type { ColumnDef } from "@tanstack/react-table";

import { DataTableColumnHeader } from "@/components/traces/table-header";
import { Checkbox } from "@/components/ui/checkbox";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { SpanRow } from "@/hooks/use-traces";
import { spanStatusLabel } from "@/hooks/use-traces";
import { formatCost, formatDuration, formatTimestamp } from "@/lib/formatters";

/** Get attribute value, trying common key variants */
function getAttr(attrs: Record<string, unknown> | undefined, ...keys: string[]): unknown {
  if (!attrs) return undefined;
  for (const k of keys) {
    const v = attrs[k];
    if (v !== undefined && v !== null) return v;
  }
  return undefined;
}

/** Extract text preview from LLM input/output (JSON array of messages or raw) */
function extractIoPreview(value: unknown): string {
  if (value == null) return "";
  try {
    const parsed = typeof value === "string" ? JSON.parse(value) : value;
    if (Array.isArray(parsed) && parsed.length > 0) {
      for (const msg of parsed) {
        if (msg?.content) return String(msg.content).replace(/\s+/g, " ").trim();
      }
      return JSON.stringify(parsed[0]);
    }
    if (typeof parsed === "object") return JSON.stringify(parsed);
    return String(value);
  } catch {
    return String(value).replace(/\s+/g, " ").trim();
  }
}

function extractToolNamesFromOutput(outputs: unknown): string[] {
  try {
    const parsed = typeof outputs === "string" ? JSON.parse(outputs) : outputs;
    const names: string[] = [];
    if (Array.isArray(parsed)) {
      for (const msg of parsed) {
        if (Array.isArray(msg?.tool_calls)) {
          for (const tc of msg.tool_calls) {
            const name = tc?.function?.name;
            if (name) names.push(name);
          }
        }
      }
    } else if (Array.isArray((parsed as { tool_calls?: unknown[] })?.tool_calls)) {
      for (const tc of (parsed as { tool_calls: { function?: { name?: string } }[] }).tool_calls) {
        const name = tc?.function?.name;
        if (name) names.push(name);
      }
    }
    return [...new Set(names)];
  } catch {
    return [];
  }
}

function trimIo(value: unknown, maxLen = 50): string {
  const str = extractIoPreview(value);
  return str.length > maxLen ? `${str.slice(0, maxLen)}…` : str;
}

export const tracesColumns: ColumnDef<SpanRow>[] = [
  {
    cell: ({ row }) => (
      <div className="flex items-center justify-center">
        <Checkbox
          aria-label="Select row"
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          onClick={(e) => e.stopPropagation()}
        />
      </div>
    ),
    enableHiding: false,
    enableSorting: false,
    header: ({ table }) => (
      <div className="flex items-center justify-center">
        <Checkbox
          aria-label="Select all"
          checked={
            table.getIsAllPageRowsSelected() ||
            (table.getIsSomePageRowsSelected() && "indeterminate")
          }
          onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
        />
      </div>
    ),
    id: "select",
  },
  {
    accessorKey: "traceId",
    cell: ({ row }) => {
      const id = row.original.traceId ?? "";
      const display = id.length > 16 ? `${id.slice(0, 8)}…${id.slice(-8)}` : id;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-mono text-xs text-muted-foreground cursor-help truncate max-w-[120px] inline-block">
                {display}
              </span>
            </TooltipTrigger>
            <TooltipContent className="font-mono text-xs max-w-md break-all" side="top">
              {id}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Trace ID" />,
    id: "trace_id",
    meta: { label: "Trace ID" },
  },
  {
    accessorKey: "name",
    cell: ({ row }) => (
      <span className="font-mono text-sm">
        {row.original.scopeName || row.original.name || row.original.traceId.slice(0, 12)}
      </span>
    ),
    header: ({ column }) => <DataTableColumnHeader column={column} title="Name" />,
    id: "name",
    meta: { label: "Name" },
  },
  {
    accessorKey: "startTimeUnixNano",
    cell: ({ row }) => (
      <span className="text-sm text-muted-foreground">
        {formatTimestamp(row.original.startTimeUnixNano)}
      </span>
    ),
    header: ({ column }) => <DataTableColumnHeader column={column} title="Timestamp" />,
    id: "timestamp",
    meta: { label: "Timestamp" },
  },
  {
    accessorKey: "statusCode",
    cell: ({ row }) => {
      const status = spanStatusLabel(row.original.statusCode);
      return (
        <span
          className={`
            inline-flex items-center rounded-full border border-border px-2 py-0.5 text-xs font-medium
            ${
              status === "ok"
                ? "bg-green-100 text-green-800 border-green-200"
                : status === "error"
                  ? "bg-red-100 text-red-800 border-red-200"
                  : "bg-muted text-muted-foreground border-border"
            }
          `}
        >
          {status}
        </span>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Status" />,
    id: "status",
    meta: { label: "Status" },
  },
  {
    accessorKey: "durationNano",
    cell: ({ row }) => (
      <span className="text-sm">
        {formatDuration(row.original.durationNano ? row.original.durationNano / 1_000_000 : 0)}
      </span>
    ),
    header: ({ column }) => <DataTableColumnHeader column={column} title="Duration" />,
    id: "duration",
    meta: { label: "Duration" },
  },
  {
    accessorFn: (row) => {
      const spanAttrs = row.spanAttributes;
      const resourceAttrs = row.resourceAttributes;
      return (
        getAttr(spanAttrs, "gen_ai.request.model", "gen_ai.response.model") ??
        getAttr(resourceAttrs, "gen_ai.request.model", "gen_ai.response.model") ??
        getAttr(spanAttrs, "gen_ai.operation.name") ??
        ""
      );
    },
    cell: ({ row }) => {
      const spanAttrs = row.original.spanAttributes;
      const resourceAttrs = row.original.resourceAttributes;
      const model =
        getAttr(spanAttrs, "gen_ai.request.model", "gen_ai.response.model") ??
        getAttr(resourceAttrs, "gen_ai.request.model", "gen_ai.response.model") ??
        getAttr(spanAttrs, "gen_ai.operation.name");
      const value = model ? String(model) : "";
      if (!value) return <span className="text-muted-foreground">—</span>;
      const display = value.length > 24 ? `${value.slice(0, 20)}…` : value;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-mono text-xs truncate max-w-[140px] inline-block">
                {display}
              </span>
            </TooltipTrigger>
            <TooltipContent className="font-mono text-xs max-w-md" side="top">
              {value}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Model" />,
    id: "model",
    meta: { label: "Model" },
  },
  {
    accessorFn: (row) => {
      const attrs = row.spanAttributes;
      const total = getAttr(attrs, "llm.usage.total_tokens");
      if (total != null) return Number(total);
      const inTokens = Number(getAttr(attrs, "gen_ai.usage.input_tokens") ?? 0);
      const outTokens = Number(getAttr(attrs, "gen_ai.usage.output_tokens") ?? 0);
      return inTokens + outTokens || null;
    },
    cell: ({ row }) => {
      const attrs = row.original.spanAttributes;
      const total = getAttr(attrs, "llm.usage.total_tokens");
      const inT = getAttr(attrs, "gen_ai.usage.input_tokens");
      const outT = getAttr(attrs, "gen_ai.usage.output_tokens");
      let display = "—";
      if (total != null) {
        display = String(total);
      } else if (inT != null || outT != null) {
        const inVal = Number(inT) || 0;
        const outVal = Number(outT) || 0;
        display = inVal + outVal > 0 ? `${inVal}+${outVal}` : "—";
      }
      return <span className="text-sm tabular-nums">{display}</span>;
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Tokens" />,
    id: "tokens",
    meta: { label: "Tokens" },
  },
  {
    accessorFn: (row) => row.cost || null,
    cell: ({ row }) => (
      <span className="text-sm tabular-nums">{formatCost(row.original.cost)}</span>
    ),
    header: ({ column }) => <DataTableColumnHeader column={column} title="Cost" />,
    id: "cost",
    meta: { label: "Cost" },
  },
  {
    accessorFn: (row) => {
      const score = row.feedbackScores?.correctness;
      return typeof score === "number" ? score : null;
    },
    cell: ({ row }) => {
      const score = row.original.feedbackScores?.correctness;
      const reason = row.original.feedbackScores?.correctness_reason as string | undefined;
      const error = row.original.feedbackScores?.correctness_error as string | undefined;

      if (error) return <span className="text-muted-foreground text-xs">error</span>;
      if (typeof score !== "number") return <span className="text-muted-foreground">—</span>;
      const pct = Math.round(score * 100);
      const colorClass =
        score >= 0.7
          ? "bg-green-100 text-green-800 border-green-200 dark:bg-green-900/30 dark:text-green-400 dark:border-green-800"
          : score >= 0.4
            ? "bg-amber-100 text-amber-800 border-amber-200 dark:bg-amber-900/30 dark:text-amber-400 dark:border-amber-800"
            : "bg-red-100 text-red-800 border-red-200 dark:bg-red-900/30 dark:text-red-400 dark:border-red-800";
      const badge = (
        <span
          className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium tabular-nums ${colorClass} ${reason ? "cursor-help" : ""}`}
        >
          {pct}%
        </span>
      );
      if (!reason) return badge;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>{badge}</TooltipTrigger>
            <TooltipContent className="max-w-xs text-xs leading-relaxed" side="top">
              {reason}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Score" />,
    id: "eval_score",
    meta: { label: "Score" },
  },
  {
    accessorFn: (row) => trimIo(row.inputs, 100),
    cell: ({ row }) => {
      const preview = trimIo(row.original.inputs, 45);
      if (!preview) return <span className="text-muted-foreground">—</span>;
      const full = trimIo(row.original.inputs, 500);
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-xs text-muted-foreground cursor-help truncate max-w-[180px] inline-block">
                {preview}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md text-xs whitespace-pre-wrap" side="top">
              {full || preview}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    enableSorting: false,
    header: ({ column }) => <DataTableColumnHeader column={column} title="Input" />,
    id: "input",
    meta: { label: "Input" },
  },
  {
    accessorFn: (row) => trimIo(row.outputs, 100),
    cell: ({ row }) => {
      const responseType = row.original.spanAttributes?.response_type as string | undefined;
      if (responseType === "tool_calls") {
        const toolNames = extractToolNamesFromOutput(row.original.outputs);
        return (
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="inline-flex items-center rounded-full border border-violet-300 bg-violet-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-700 dark:border-violet-700 dark:bg-violet-900/30 dark:text-violet-400">
              tool call
            </span>
            {toolNames.slice(0, 3).map((name) => (
              <span
                className="font-mono text-[10px] font-semibold text-violet-600 dark:text-violet-400 truncate max-w-[100px]"
                key={name}
              >
                {name}
              </span>
            ))}
          </div>
        );
      }
      const preview = trimIo(row.original.outputs, 45);
      if (!preview) return <span className="text-muted-foreground">—</span>;
      const full = trimIo(row.original.outputs, 500);
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-xs text-muted-foreground cursor-help truncate max-w-[180px] inline-block">
                {preview}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md text-xs whitespace-pre-wrap" side="top">
              {full || preview}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    enableSorting: false,
    header: ({ column }) => <DataTableColumnHeader column={column} title="Output" />,
    id: "output",
    meta: { label: "Output" },
  },
  {
    accessorFn: (row) =>
      getAttr(row.spanAttributes, "gen_ai.system") ??
      getAttr(row.resourceAttributes, "gen_ai.system", "service.name"),
    cell: ({ row }) => {
      const system =
        getAttr(row.original.spanAttributes, "gen_ai.system", "service.name") ??
        getAttr(row.original.resourceAttributes, "gen_ai.system", "service.name");
      const value = system ? String(system) : "";
      if (!value) return <span className="text-muted-foreground">—</span>;
      return <span className="font-mono text-xs">{value}</span>;
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="System" />,
    id: "system",
    meta: { label: "System" },
  },
  {
    accessorFn: (row) =>
      String(
        (row.spanAttributes?.status_message as string | undefined) ??
          (row.spanAttributes?.StatusMessage as string | undefined) ??
          ""
      ),
    cell: ({ row }) => {
      const msg =
        (row.original.spanAttributes?.status_message as string | undefined) ??
        (row.original.spanAttributes?.StatusMessage as string | undefined);
      if (!msg) return <span className="text-muted-foreground">—</span>;
      const truncated = msg.length > 50 ? `${msg.slice(0, 50)}…` : msg;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="text-xs text-muted-foreground cursor-help truncate max-w-[160px] inline-block">
                {truncated}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-md text-xs whitespace-pre-wrap" side="top">
              {msg}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Status Message" />,
    id: "status_message",
    meta: { label: "Status Message" },
  },
  {
    accessorFn: (row) => {
      return agentIdToHumanReadable(row.agentId ?? "");
    },
    cell: ({ row }) => {
      const value = agentIdToHumanReadable(row.original.agentId ?? "");
      if (!value) return <span className="text-muted-foreground">—</span>;
      const display = value.length > 16 ? `${value.slice(0, 12)}…` : value;
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="font-mono text-xs text-muted-foreground cursor-help truncate max-w-[100px] inline-block capitalize">
                {display}
              </span>
            </TooltipTrigger>
            <TooltipContent className="font-mono text-xs max-w-md break-all capitalize" side="top">
              {value}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      );
    },
    header: ({ column }) => <DataTableColumnHeader column={column} title="Agent" />,
    id: "prompt",
    meta: { label: "Agent" },
  },
];

const agentIdToHumanReadable = (agentId: string) => {
  const parts = agentId.split("_");
  const name = parts.length >= 3 ? parts.slice(2).join("_") : agentId;
  return name.replaceAll("-", " ");
};
