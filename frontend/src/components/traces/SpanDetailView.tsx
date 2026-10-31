import type React from "react";
import { Fragment, useId, useMemo, useState } from "react";

import { EvaluationMethodBadge } from "@/components/evaluations/evaluation-method-badge";
import { type MetricResult, metricResults } from "@/components/evaluations/metric-list";
import { ScoreChip } from "@/components/evaluations/run-comparison";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { useTraceData } from "@/components/traces/contexts/TraceDataContext";
import { useTraceSelection } from "@/components/traces/contexts/TraceSelectionContext";
import {
  CodeTokens,
  ExpandableContent,
  InspectorSection as Section,
  StackTraceContent,
} from "@/components/traces/inspector-section";
import { TraceErrorDetailsView } from "@/components/traces/trace-error-details";
import { Badge } from "@/components/ui/badge";
import { BlockActions } from "@/components/ui/block-actions";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { isLikelyMarkdown, MarkdownContent } from "@/components/ui/markdown";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { SpanRow } from "@/hooks/use-traces";
import { spanStatusLabel } from "@/hooks/use-traces";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { ToolCallItem, TranscriptMessage } from "@/types/chat";

const NOOP = () => undefined;

function eventDate(timeUnixNano: unknown): Date | null {
  if (!timeUnixNano) return null;
  const date = new Date(Number(timeUnixNano) / 1_000_000);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatAttributeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);

  if (typeof value === "string") {
    try {
      const parsed = JSON.parse(value);
      if (typeof parsed === "object" && parsed !== null) {
        return JSON.stringify(parsed, null, 2);
      }
    } catch {
      // Not a JSON string, fall through
    }
  }
  return String(value);
}

function KeyValueRows({ entries }: { entries: Array<[string, unknown]> }) {
  const { projectId } = useTraceData();
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[minmax(0,1fr)_minmax(0,3fr)] gap-x-4 gap-y-2 text-xs">
      {entries.map(([key, value]) =>
        key.toLowerCase().includes("stacktrace") && typeof value === "string" ? (
          <Fragment key={key}>
            <dt className="sr-only">{key}</dt>
            <dd className="col-span-2 min-w-0">
              <StackTraceContent label={key} text={value} />
            </dd>
          </Fragment>
        ) : (
          <Fragment key={key}>
            <dt className="max-w-[20rem] break-all font-mono text-muted-foreground">
              {/* Older spans still carry PromptId; show it under the current name. */}
              {key === "PromptId" ? "CapabilityId" : key}
            </dt>
            <dd className="min-w-0">
              {[
                "Model",
                "Response model",
                "gen_ai.request.model",
                "gen_ai.response.model",
              ].includes(key) &&
              typeof value === "string" &&
              value.trim() ? (
                <FinetuningModelChip compact model={value} projectId={projectId} />
              ) : value !== null && typeof value === "object" ? (
                <FormattedValue value={value} />
              ) : (
                <ExpandableContent label={key} text={formatAttributeValue(value)}>
                  <pre className="whitespace-pre-wrap break-words font-mono leading-5">
                    {formatAttributeValue(value)}
                  </pre>
                </ExpandableContent>
              )}
            </dd>
          </Fragment>
        )
      )}
    </dl>
  );
}

const OVERMIND_GROUPS: Array<{ prefix: string; title: string }> = [
  { prefix: "overmind.capability.", title: "Capability" },
  { prefix: "overmind.optimize.", title: "Optimise" },
  { prefix: "overmind.setup.", title: "Setup" },
  { prefix: "overmind.run.", title: "Run" },
  { prefix: "overmind.init.", title: "Initialise" },
];

// Mirrors the SDK attribute contract (overmind/docs/tracing-attributes.md,
// overbae/api/overmind_attrs.py). Row = [label, ...candidate keys]; first present wins.
const MODEL_USAGE_FIELDS: Array<[string, ...string[]]> = [
  ["Model", "genai.model"],
  ["Response model", "genai.response.model"],
  ["Provider", "genai.provider"],
  ["Prompt tokens", "genai.prompt_tokens", "genai.usage.prompt_tokens"],
  ["Completion tokens", "genai.completion_tokens", "genai.usage.completion_tokens"],
  ["Total tokens", "genai.total_tokens", "genai.usage.total_tokens"],
  ["Cache-read tokens", "genai.cache_read_tokens"],
  ["Cost (USD)", "genai.cost"],
  ["Temperature", "genai.request.temperature"],
  ["Max tokens", "genai.request.max_tokens"],
  ["Top P", "genai.request.top_p"],
  ["Finish reason", "genai.response.finish_reason"],
  ["Response chars", "genai.response.message_chars"],
  ["Streaming", "genai.streaming"],
  ["Time to first token (s)", "genai.time_to_first_token_seconds"],
  ["Elapsed (s)", "genai.elapsed_seconds"],
];

const RETRIEVAL_FIELDS: Array<[string, ...string[]]> = [
  ["Query chars", "overmind.retrieval.query_chars"],
  ["Result count", "overmind.retrieval.result_count"],
];

// Rendered by dedicated sections; excluded from the generic attributes table.
const HIDDEN_ATTRIBUTE_KEYS = new Set<string>([
  "overmind.input_data",
  "overmind.input.data",
  "inputs",
  "traceloop.entity.input",
  "overmind.output_data",
  "overmind.output.data",
  "outputs",
  "traceloop.entity.output",
  "available_tools",
  ...MODEL_USAGE_FIELDS.flatMap(([, ...keys]) => keys),
  ...RETRIEVAL_FIELDS.flatMap(([, ...keys]) => keys),
]);

function hasAttrValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

function collectFieldEntries(
  attributes: Record<string, unknown> | undefined,
  fields: Array<[string, ...string[]]>
): Array<[string, unknown]> {
  if (!attributes) return [];
  const entries: Array<[string, unknown]> = [];
  for (const [label, ...keys] of fields) {
    const key = keys.find((k) => hasAttrValue(attributes[k]));
    if (key !== undefined) entries.push([label, attributes[key]]);
  }
  return entries;
}

function groupSpanAttributes(
  attributes: Record<string, unknown> | undefined,
  previewKeys: ReadonlyArray<string | null>
): {
  groups: Array<{ title: string; entries: Array<[string, unknown]> }>;
  rest: Array<[string, unknown]>;
} {
  const grouped: Record<string, Array<[string, unknown]>> = {};
  for (const { prefix } of OVERMIND_GROUPS) grouped[prefix] = [];
  const rest: Array<[string, unknown]> = [];

  if (attributes && typeof attributes === "object") {
    for (const [key, value] of Object.entries(attributes)) {
      if (HIDDEN_ATTRIBUTE_KEYS.has(key) || previewKeys.includes(key)) continue;
      const match = OVERMIND_GROUPS.find((g) => key.startsWith(g.prefix));
      if (match) {
        const stripped = key.slice(match.prefix.length);
        grouped[match.prefix].push([stripped || key, value]);
      } else {
        rest.push([key, value]);
      }
    }
  }

  const groups = OVERMIND_GROUPS.filter(({ prefix }) => grouped[prefix].length > 0).map(
    ({ prefix, title }) => ({ entries: grouped[prefix], title })
  );

  return { groups, rest };
}

type ToolParameterSchema = {
  type?: string;
  description?: string;
  enum?: unknown[];
  [key: string]: unknown;
};

type ToolFunctionDef = {
  name?: string;
  description?: string;
  parameters?: {
    type?: string;
    properties?: Record<string, ToolParameterSchema>;
    required?: string[];
    [key: string]: unknown;
  };
};

type ToolDefinition = {
  type?: string;
  function?: ToolFunctionDef;
  // flat shape fallback (name at top level)
  name?: string;
  description?: string;
  parameters?: ToolFunctionDef["parameters"];
};

function ToolCallBadge() {
  return (
    <span className="chip-label inline-flex items-center rounded-sm border border-cat-2/30 bg-cat-2/10 px-2 py-0.5 text-xs font-semibold text-cat-2">
      Tool call
    </span>
  );
}

// OpenAI wire shape, or the flat {name, arguments} the backend normalizes to.
function ToolCallBlock({ tc }: { tc: ToolCallItem }) {
  const args = tc.function?.arguments ?? tc.arguments;
  const prettyArgs = useMemo(() => {
    if (args === undefined || args === null || args === "") return "";
    if (typeof args !== "string") return JSON.stringify(args, null, 2);
    try {
      return JSON.stringify(JSON.parse(args), null, 2);
    } catch {
      return args;
    }
  }, [args]);

  return (
    <div className="mt-1 rounded-sm border border-border/60 bg-wash-raised px-2 py-1.5">
      <span className="font-semibold text-cat-2">{tc.function?.name ?? tc.name ?? "tool"}</span>
      {prettyArgs && (
        <ExpandableContent
          label={`${tc.function?.name ?? tc.name ?? "Tool"} arguments`}
          text={prettyArgs}
        >
          <pre className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
            {prettyArgs}
          </pre>
        </ExpandableContent>
      )}
    </div>
  );
}

function ToolParamRow({
  name,
  schema,
  required,
}: {
  name: string;
  schema: ToolParameterSchema;
  required: boolean;
}) {
  return (
    <div className="-mx-3 grid grid-cols-[140px_80px_60px_1fr] gap-x-3 items-baseline px-3 py-1.5 border-b border-border/60 last:border-0 text-xs">
      <span className="font-mono text-cat-2 truncate">{name}</span>
      <span className="font-mono text-muted-foreground">{schema.type ?? "—"}</span>
      <span>
        {required ? (
          <span className="text-warning font-medium">required</span>
        ) : (
          <span className="text-muted-foreground">optional</span>
        )}
      </span>
      <span className={cn(PROSE, "text-muted-foreground leading-relaxed")}>
        {schema.description ?? ""}
        {schema.enum && schema.enum.length > 0 && (
          <span className="ml-1 text-muted-foreground">
            ({schema.enum.map((v) => JSON.stringify(v)).join(" | ")})
          </span>
        )}
      </span>
    </div>
  );
}

function AvailableToolCard({ tool }: { tool: ToolDefinition }) {
  const fn = tool.function ?? tool;
  const name = fn.name;
  const description = fn.description;
  const parameters = fn.parameters;
  const params = parameters?.properties ?? {};
  const required = new Set(parameters?.required ?? []);
  const paramEntries = Object.entries(params);

  return (
    <div className="rounded-sm border border-border bg-wash-subtle overflow-hidden">
      <div className="flex items-start gap-2 px-3 py-2 bg-wash-raised border-b border-border/70">
        <span className="font-semibold text-cat-2 font-mono text-sm leading-snug">
          {name ?? "unnamed"}
        </span>
      </div>
      {description && (
        <p
          className={cn(
            PROSE,
            "px-3 py-2 text-xs text-muted-foreground border-b border-border/60 leading-relaxed"
          )}
        >
          {description}
        </p>
      )}
      {paramEntries.length > 0 && (
        <div className="overflow-x-auto px-3 py-2">
          <div className="min-w-[440px]">
            <div className="pixel-label grid grid-cols-[140px_80px_60px_1fr] gap-x-3 mb-1 text-xs text-muted-foreground">
              <span>Parameter</span>
              <span>Type</span>
              <span>Required</span>
              <span>Description</span>
            </div>
            {paramEntries.map(([pName, pSchema]) => (
              <ToolParamRow
                key={pName}
                name={pName}
                required={required.has(pName)}
                schema={pSchema}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AvailableToolsBlock({ tools }: { tools: ToolDefinition[] }) {
  if (!tools || tools.length === 0) return null;
  return (
    <Section
      badge={
        <span className="chip-label inline-flex items-center rounded-sm border border-cat-2/30 bg-cat-2/10 px-2 py-0.5 text-xs font-semibold text-cat-2">
          {tools.length}
        </span>
      }
      label="Available tools"
    >
      <div className="space-y-2">
        {tools.map((tool, idx) => (
          <AvailableToolCard key={idx} tool={tool} />
        ))}
      </div>
    </Section>
  );
}

const ROLE_LABELS: Record<string, string> = {
  assistant: "Assistant",
  system: "System",
  tool: "Tool",
  user: "User",
};

function blockText(block: unknown, role: string | undefined): string | null {
  if (typeof block === "string") return block;
  const { content, response, text, type } = (block ?? {}) as Record<string, unknown>;
  if (type === "text" || type === "reasoning") {
    const value = text ?? content;
    if (typeof value === "string") return value;
  }
  if (type === "tool_call") return null;
  // Server tools (Claude web search) return results inside the assistant message;
  // the tool's own span shows them. Client tool results arrive in user or tool messages.
  if (type === "tool_call_response") {
    if (role === "assistant") return null;
    return typeof response === "string" ? response : JSON.stringify(response, null, 2);
  }
  return JSON.stringify(block, null, 2);
}

// Chat APIs send `content` as a string or typed blocks (OpenAI, Anthropic); OTel GenAI
// messages send `parts`. Text reads as text, tool calls as tool-call rows, the rest as JSON.
function contentText(content: unknown, role: string | undefined): string {
  if (content === null || content === undefined) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return JSON.stringify(content, null, 2);
  return content
    .map((block) => blockText(block, role))
    .filter((value) => value !== null)
    .join("\n");
}

function MessageRow({ msg }: { msg: TranscriptMessage }) {
  const roleLabel = ROLE_LABELS[msg.role ?? ""] ?? msg.role ?? "Message";
  const toolCalls =
    msg.tool_calls ??
    ((msg.parts ?? []).filter(
      (part) => (part as { type?: unknown })?.type === "tool_call"
    ) as ToolCallItem[]);
  const hasToolCalls = toolCalls.length > 0;
  const text = contentText(msg.content ?? msg.parts, msg.role);
  const isTool = msg.role === "tool";
  const canMarkdown =
    !isTool && typeof text === "string" && text.length > 0 && isLikelyMarkdown(text);
  const [mode, setMode] = useState<"raw" | "markdown">(canMarkdown ? "markdown" : "raw");

  return (
    <div>
      <ExpandableContent
        label={`${roleLabel} content`}
        render={(content, toggle) => (
          <>
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-muted-foreground">{roleLabel}</span>
                {hasToolCalls && (
                  <span className="chip-label inline-flex items-center rounded-sm border border-cat-2/30 bg-cat-2/10 px-2 py-0.5 text-xs font-semibold text-cat-2">
                    Tool call
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                {toggle}
                {text.length > 0 && (
                  <BlockActions
                    mode={mode}
                    onToggleMode={() => setMode((m) => (m === "raw" ? "markdown" : "raw"))}
                    showToggle={canMarkdown}
                    text={text}
                  />
                )}
              </div>
            </div>
            {content}
          </>
        )}
        text={text}
      >
        {text !== "" &&
          (mode === "markdown" && canMarkdown ? (
            <MarkdownContent compact>{text}</MarkdownContent>
          ) : (
            <div className={cn(PROSE, "whitespace-pre-wrap text-xs")}>{text}</div>
          ))}
      </ExpandableContent>
      {toolCalls.map((tc, tcIdx) => (
        <ToolCallBlock key={tcIdx} tc={tc} />
      ))}
    </div>
  );
}

type JsonView = "formatted" | "tree";

function JsonValue({
  value,
  depth = 0,
  label,
}: {
  value: unknown;
  depth?: number;
  label?: string;
}) {
  if (value === null || typeof value !== "object") {
    const text = JSON.stringify(value) ?? "null";
    return (
      <div className="flex min-w-0 items-baseline gap-2 py-0.5">
        {label !== undefined && <span className="shrink-0 text-muted-foreground">{label}:</span>}
        <div className="min-w-0 flex-1">
          <ExpandableContent label={label ?? "JSON value"} text={text}>
            <span className="whitespace-pre-wrap break-words">
              <CodeTokens text={text} />
            </span>
          </ExpandableContent>
        </div>
      </div>
    );
  }
  const array = Array.isArray(value);
  const entries = Object.entries(value);
  if (entries.length === 0)
    return (
      <span>
        {label !== undefined ? `${label}: ` : ""}
        {array ? "[]" : "{}"}
      </span>
    );
  const singleRecord =
    array &&
    value.length === 1 &&
    value[0] !== null &&
    typeof value[0] === "object" &&
    !Array.isArray(value[0]);
  const children = singleRecord ? Object.entries(value[0]) : entries;
  const opening = singleRecord ? "[{" : array ? "[" : "{";
  const closing = singleRecord ? "}]" : array ? "]" : "}";
  return (
    <details className="py-0.5" open={depth < 2 || entries.length <= 3}>
      <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
        {label !== undefined && <span className="text-foreground">{label}: </span>}
        {opening}
      </summary>
      <div className="ml-2 border-l border-border/70 pl-4">
        {children.map(([key, item]) => (
          <JsonValue depth={depth + 1} key={key} label={key} value={item} />
        ))}
      </div>
      <span className="pl-4 text-muted-foreground">{closing}</span>
    </details>
  );
}

function isMessageLike(value: unknown): value is TranscriptMessage {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    typeof (value as { role?: unknown }).role === "string"
  );
}

function isMessageList(value: unknown): value is TranscriptMessage[] {
  return Array.isArray(value) && value.length > 0 && value.every(isMessageLike);
}

function isPlainScalar(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  );
}

function FormattedValue({ value }: { value: unknown }) {
  if (typeof value === "string") {
    if (value.length === 0) return <span className="text-muted-foreground">""</span>;
    return <div className={cn(PROSE, "whitespace-pre-wrap break-words")}>{value}</div>;
  }
  if (value === null || typeof value === "number" || typeof value === "boolean") {
    return <span className="break-all font-mono">{value === null ? "null" : String(value)}</span>;
  }
  if (isMessageList(value)) {
    return (
      <div className="divide-y divide-border/70">
        {value.map((msg, idx) => (
          <div className="py-3 first:pt-0 last:pb-0" key={idx}>
            <MessageRow msg={msg} />
          </div>
        ))}
      </div>
    );
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-muted-foreground">[]</span>;
    if (value.length === 1) return <FormattedValue value={value[0]} />;
    return (
      <div className="divide-y divide-border/70">
        {value.map((item, idx) => (
          <div className="py-3 first:pt-0" key={idx}>
            <p className="mb-2 text-xs text-muted-foreground">[{idx}]</p>
            <FormattedValue value={item} />
          </div>
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="text-muted-foreground">{"{}"}</span>;
  return (
    <div className="space-y-3">
      {entries.map(([key, val]) => {
        const inline =
          isPlainScalar(val) &&
          !(typeof val === "string" && (val.includes("\n") || val.length > 60));
        if (inline) {
          return (
            <div
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] items-baseline gap-x-4"
              key={key}
            >
              <span className="min-w-0 break-words font-mono text-muted-foreground">{key}</span>
              <span className="min-w-0 break-all font-mono">
                {val === "" ? (
                  <span className="text-muted-foreground">""</span>
                ) : val === null ? (
                  "null"
                ) : (
                  String(val)
                )}
              </span>
            </div>
          );
        }
        if (val !== null && typeof val === "object") {
          const count = Array.isArray(val) ? val.length : Object.keys(val).length;
          if (count <= 3)
            return (
              <div className="space-y-2" key={key}>
                <p className="font-mono text-muted-foreground">{key}</p>
                <FormattedValue value={val} />
              </div>
            );
          return (
            <details className="py-1" key={key}>
              <summary className="cursor-pointer break-all font-mono text-muted-foreground hover:text-foreground">
                <span>{key}</span>{" "}
                <span className="ml-2 text-xs">
                  {count} {Array.isArray(val) ? "items" : "fields"}
                </span>
              </summary>
              <div className="mt-3">
                <FormattedValue value={val} />
              </div>
            </details>
          );
        }
        return (
          <div key={key}>
            <span className="min-w-0 break-words font-mono text-muted-foreground">{key}</span>
            <div className="mt-1">
              <ExpandableContent label={key} text={formatAttributeValue(val)}>
                <FormattedValue value={val} />
              </ExpandableContent>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ViewToggle({ onChange, value }: { onChange: (next: JsonView) => void; value: JsonView }) {
  return (
    <Select onValueChange={(next) => onChange(next as JsonView)} value={value}>
      <SelectTrigger
        aria-label="View format"
        className="bg-transparent text-muted-foreground hover:bg-muted"
        size="xs"
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="formatted">Formatted</SelectItem>
        <SelectItem value="tree">Tree</SelectItem>
      </SelectContent>
    </Select>
  );
}

function JsonBlock({
  badge,
  id,
  title,
  value,
}: {
  badge?: React.ReactNode;
  id?: string;
  title: string;
  value: unknown;
}) {
  const parsedValue = useMemo(() => {
    if (typeof value !== "string") return value;
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }, [value]);

  const prettyJson = useMemo(() => JSON.stringify(parsedValue, null, 2) ?? "", [parsedValue]);

  const isStringValue = typeof parsedValue === "string";
  const messageList = isMessageList(parsedValue) ? parsedValue : null;
  const messageObject = !messageList && isMessageLike(parsedValue) ? parsedValue : null;
  const canMarkdown = isStringValue && parsedValue.length > 0 && isLikelyMarkdown(parsedValue);

  // Hidden only when both views would render identically (short single-line string).
  const stringNeedsToggle =
    isStringValue && (parsedValue.includes("\n") || parsedValue.length > 80);
  const showToggle = Boolean(
    messageList || messageObject || canMarkdown || !isStringValue || stringNeedsToggle
  );
  const [view, setView] = useState<JsonView>(
    messageList || messageObject || isStringValue ? "formatted" : "tree"
  );

  if (value === null || value === undefined) {
    return (
      <Section label={title}>
        <Card className="bg-wash-subtle px-3 py-2">
          <p className="text-xs text-muted-foreground">Not captured</p>
        </Card>
      </Section>
    );
  }

  const jsonPre = "whitespace-pre-wrap break-words font-mono text-xs leading-5";

  let content: React.ReactNode;
  if (view === "tree") {
    content = (
      <div className="font-mono text-xs leading-5">
        <JsonValue value={parsedValue} />
      </div>
    );
  } else if (messageList) {
    content = (
      <div className="space-y-4 font-mono text-xs">
        {messageList.map((msg, idx) => (
          <div className="py-3 first:pt-0 last:pb-0" key={idx}>
            <MessageRow msg={msg} />
          </div>
        ))}
      </div>
    );
  } else if (messageObject) {
    content = (
      <div className="font-mono text-xs">
        <MessageRow msg={messageObject} />
      </div>
    );
  } else if (isStringValue) {
    content = canMarkdown ? (
      <div className="min-w-0">
        <MarkdownContent compact>{parsedValue}</MarkdownContent>
      </div>
    ) : (
      <pre className={jsonPre}>{parsedValue || '""'}</pre>
    );
  } else {
    content = (
      <div className="text-xs leading-5">
        <FormattedValue value={parsedValue} />
      </div>
    );
  }

  return (
    <ExpandableContent
      label={`${title} content`}
      render={(body, toggle) => (
        <Section
          actions={
            <div className="flex items-center gap-1.5">
              {showToggle && <ViewToggle onChange={setView} value={view} />}
              {toggle}
              <BlockActions
                mode="raw"
                onToggleMode={NOOP}
                showToggle={false}
                text={isStringValue ? parsedValue : prettyJson}
              />
            </div>
          }
          badge={badge}
          id={id}
          label={title}
        >
          <Card className="min-w-0 bg-wash-subtle px-3 py-2">{body}</Card>
        </Section>
      )}
      text={isStringValue && view !== "tree" ? parsedValue : ""}
    >
      {content}
    </ExpandableContent>
  );
}

function MetricScore({ metric }: { metric: MetricResult }) {
  if (metric.error)
    return (
      <Badge size="chip" variant="error">
        Error
      </Badge>
    );
  if (metric.verdict === "abstain")
    return (
      <Badge size="chip" variant="neutral">
        Abstained
      </Badge>
    );
  if (metric.score == null)
    return (
      <Badge size="chip" variant="neutral">
        Not scored
      </Badge>
    );
  return <ScoreChip value={metric.score} />;
}

function MetricExplanation({ metric }: { metric: MetricResult }) {
  return (
    <dl className="grid grid-cols-[7rem_minmax(0,1fr)] items-baseline gap-x-4 gap-y-3 text-xs">
      {metric.expectation && (
        <>
          <dt className="text-muted-foreground">Expectation</dt>
          <dd className="min-w-0 whitespace-pre-wrap break-words leading-6">
            {metric.expectation}
          </dd>
        </>
      )}
      {metric.verdict && (
        <>
          <dt className="text-muted-foreground">Verdict</dt>
          <dd>{metric.verdict}</dd>
        </>
      )}
      <dt className="text-muted-foreground">{metric.error ? "Evaluation error" : "Explanation"}</dt>
      <dd className="min-w-0">
        <ExpandableContent label="score explanation" text={metric.error || metric.reason || ""}>
          <p
            className={cn(
              "whitespace-pre-wrap break-words leading-6",
              metric.error && "text-destructive"
            )}
          >
            {metric.error || metric.reason || "No explanation recorded"}
          </p>
        </ExpandableContent>
      </dd>
    </dl>
  );
}

function ScoreRow({
  name,
  metric,
  children,
  aggregate = false,
}: {
  name: string;
  metric: MetricResult;
  children: React.ReactNode;
  aggregate?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <Fragment>
      <TableRow className={open ? "bg-wash-raised" : undefined}>
        <TableCell className="p-0" colSpan={3}>
          <button
            aria-controls={id}
            aria-describedby={`${id}-result`}
            aria-expanded={open}
            aria-label={`Inspect score: ${name}`}
            className="grid w-full grid-cols-[minmax(0,1fr)_8rem_6rem] items-center gap-0 p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            onClick={() => setOpen(!open)}
            type="button"
          >
            <span className="flex min-w-0 items-start gap-2 pr-3 whitespace-normal break-words">
              <Icon.chevronRight
                aria-hidden
                className={cn(
                  "mt-0.5 size-3.5 shrink-0 text-muted-foreground",
                  open && "rotate-90"
                )}
              />
              <span>{name}</span>
            </span>
            <span>
              {aggregate ? (
                <span className="text-muted-foreground">Aggregate</span>
              ) : metric.type === "llm" || metric.type === "deterministic" ? (
                <EvaluationMethodBadge type={metric.type} />
              ) : (
                <span className="text-muted-foreground">{metric.type || "Not recorded"}</span>
              )}
            </span>
            <span className="text-right" id={`${id}-result`}>
              <MetricScore metric={metric} />
            </span>
          </button>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="hover:bg-transparent">
          <TableCell className="whitespace-normal p-4" colSpan={3}>
            <div className="min-w-0 space-y-4" id={id}>
              {children}
            </div>
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

function TraceScoresSection({ span }: { span: SpanRow }) {
  const { spans } = useTraceData();
  const { setSelectedSpanId } = useTraceSelection();
  const entries = metricResults(span.evalMetrics);
  const referenceKey = ["overmind.expected", "expected_output", "expected"].find(
    (key) => span.spanAttributes[key] != null
  );
  return (
    <Table
      aria-label="Evaluation scores"
      className="min-w-0 table-fixed text-xs"
      containerClassName="min-h-0 flex-1"
    >
      <TableHeader>
        <TableRow>
          <TableHead className="pl-8">Evaluation</TableHead>
          <TableHead className="w-32">Method</TableHead>
          <TableHead className="w-28 pr-3 text-right">Score</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {span.trajectory && (
          <ScoreRow aggregate metric={{ score: span.trajectoryScore }} name="Trajectory">
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>Trace-level result</span>
              {span.trajectory.span_count != null && (
                <span>{span.trajectory.span_count} spans evaluated</span>
              )}
              {span.trajectory.passed != null && (
                <Badge size="chip" variant={span.trajectory.passed ? "success" : "warning"}>
                  {span.trajectory.passed ? "Passed" : "Did not pass"}
                </Badge>
              )}
            </div>
            {metricResults(span.trajectory.metrics).length ? (
              metricResults(span.trajectory.metrics).map(([name, metric]) => (
                <Section badge={<MetricScore metric={metric} />} key={name} label={name}>
                  <MetricExplanation metric={metric} />
                </Section>
              ))
            ) : (
              <p className="text-xs text-muted-foreground">No component results recorded</p>
            )}
            <Section defaultOpen={false} label="Trace spans">
              <div className="divide-y divide-border/70">
                {spans.map((source) => (
                  <button
                    className="flex w-full items-center justify-between gap-3 px-2 py-2 text-left text-xs hover:bg-wash-raised focus-visible:ring-2 focus-visible:ring-ring"
                    key={source.spanId}
                    onClick={() => setSelectedSpanId(source.spanId)}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="block break-words">
                        {source.name || source.scopeName || "Unnamed span"}
                      </span>
                      <span className="font-mono text-muted-foreground">{source.spanId}</span>
                    </span>
                    <Icon.chevronRight className="size-3 shrink-0" />
                  </button>
                ))}
              </div>
            </Section>
          </ScoreRow>
        )}
        {entries.map(([name, metric]) => (
          <ScoreRow key={name} metric={metric} name={name}>
            <MetricExplanation metric={metric} />
            {span.evalIntent && (
              <Section label="Intent">
                <p className="whitespace-pre-wrap text-xs leading-5">{span.evalIntent}</p>
              </Section>
            )}
            <JsonBlock title="Output" value={span.outputs} />
            <JsonBlock title="Input" value={span.inputs} />
            {referenceKey && (
              <JsonBlock title="Reference" value={span.spanAttributes[referenceKey]} />
            )}
          </ScoreRow>
        ))}
      </TableBody>
    </Table>
  );
}

interface SpanDetailViewProps {
  span: SpanRow;
}

export function SpanDetailView({ span }: SpanDetailViewProps) {
  const { projectId } = useTraceData();
  const { groups: overmindGroups, rest: spanAttributeEntries } = groupSpanAttributes(
    span.spanAttributes,
    [span.inputAttribute, span.outputAttribute]
  );

  const modelUsageEntries = collectFieldEntries(span.spanAttributes, MODEL_USAGE_FIELDS);
  const retrievalEntries = collectFieldEntries(span.spanAttributes, RETRIEVAL_FIELDS);

  const isError = span.statusCode === 2;

  const spanInfoEntries: Array<[string, unknown]> = [
    ["Name", span.name || span.scopeName || "—"],
    ["Scope", span.scopeName || "—"],
    ["Span ID", span.spanId],
    ["Parent Span ID", span.parentSpanId ?? "—"],
    ["Duration", span.durationNano > 0 ? `${(span.durationNano / 1_000_000).toFixed(2)}ms` : "—"],
    ["Status", spanStatusLabel(span.statusCode)],
  ];

  const scoreCount = metricResults(span.evalMetrics).length + (span.trajectory ? 1 : 0);
  const TypeIcon =
    span.spanType === "llm_call"
      ? Icon.model
      : span.spanType === "tool_call"
        ? Icon.tool
        : Icon.code;

  const eventSections = span.events.map((event, index) => {
    const time = eventDate(event.timeUnixNano);
    return (
      <Section
        actions={
          <BlockActions
            mode="raw"
            onToggleMode={NOOP}
            showToggle={false}
            text={JSON.stringify(event, null, 2)}
          />
        }
        badge={
          <>
            <Badge size="chip" variant="neutral">
              Event
            </Badge>
            {time ? (
              <time
                className="font-mono text-xs text-muted-foreground"
                dateTime={time.toISOString()}
              >
                {time.toLocaleString()}
              </time>
            ) : null}
          </>
        }
        key={index}
        label={event.name}
      >
        <KeyValueRows
          entries={[
            ...(time ? [["Time", time.toISOString()] as [string, unknown]] : []),
            ...Object.entries(event.attributes ?? {}),
          ]}
        />
      </Section>
    );
  });

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-card text-card-foreground">
      <div className="shrink-0 px-3 py-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2">
            <span
              className={cn(
                "flex size-5 shrink-0 items-center justify-center rounded-sm",
                span.spanType === "llm_call"
                  ? "bg-cat-1/10 text-cat-1"
                  : span.spanType === "tool_call"
                    ? "bg-cat-2/10 text-cat-2"
                    : "bg-muted text-muted-foreground"
              )}
            >
              <TypeIcon className="size-3.5" />
            </span>
            <h3 className="min-w-0 break-words text-sm font-medium">
              {span.name || span.scopeName || "Unnamed span"}
            </h3>
          </div>
          {span.model && <FinetuningModelChip compact model={span.model} projectId={projectId} />}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs tabular-nums text-muted-foreground">
          <span className={isError ? "text-destructive" : undefined}>
            {spanStatusLabel(span.statusCode)}
          </span>
          {span.startTimeUnixNano > 0 && (
            <time dateTime={new Date(span.startTimeUnixNano / 1_000_000).toISOString()}>
              {new Date(span.startTimeUnixNano / 1_000_000).toLocaleString()}
            </time>
          )}
          <span>
            {span.durationNano > 0
              ? formatDuration(span.durationNano / 1_000_000)
              : "Duration unavailable"}
          </span>
          {span.totalTokens != null && <span>{formatNumber(span.totalTokens)} tokens</span>}
          {span.totalCost != null && <span>{formatCost(span.totalCost)}</span>}
        </div>
      </div>
      {span.errorDetails && <TraceErrorDetailsView details={span.errorDetails} />}
      <Tabs className="flex min-h-0 flex-1 flex-col" defaultValue="preview">
        <TabsList
          aria-label="Span details"
          className="mx-3 my-1 h-8 max-w-[calc(100%-1.5rem)] shrink-0 self-start justify-start gap-0.5 overflow-x-auto border border-border bg-background p-0.5"
        >
          {[
            { label: "Preview", value: "preview" },
            { label: "Attributes", value: "attributes" },
            { count: scoreCount, label: "Scores", value: "scores" },
            { count: span.events.length, label: "Events", value: "events" },
          ].map(({ value, label, count }) => (
            <TabsTrigger
              aria-label={count === undefined ? label : `${label} (${count})`}
              className="h-6 gap-1.5 px-2 text-xs data-[state=active]:bg-control data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              key={value}
              value={value}
            >
              {label}
              {count !== undefined && (
                <span
                  aria-hidden
                  className="rounded-sm bg-muted px-1 text-xs tabular-nums text-muted-foreground"
                >
                  {count}
                </span>
              )}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent className="mt-0 min-h-0 flex-1 overflow-auto px-3 py-2" value="preview">
          <JsonBlock title="Input" value={span.inputs} />
          <JsonBlock
            badge={
              span.spanAttributes?.response_type === "tool_calls" ? <ToolCallBadge /> : undefined
            }
            title="Output"
            value={span.outputs}
          />
          {(spanAttributeEntries.length > 0 || modelUsageEntries.length > 0) && (
            <Section label="Attributes">
              <KeyValueRows entries={[...modelUsageEntries, ...spanAttributeEntries]} />
            </Section>
          )}
          {eventSections}
          {Array.isArray(span.spanAttributes?.available_tools) &&
            span.spanAttributes.available_tools.length > 0 && (
              <details className="py-2">
                <summary className="cursor-pointer text-xs text-muted-foreground">
                  Available tools ({span.spanAttributes.available_tools.length})
                </summary>
                <AvailableToolsBlock
                  tools={span.spanAttributes.available_tools as ToolDefinition[]}
                />
              </details>
            )}
        </TabsContent>
        <TabsContent className="mt-0 min-h-0 flex-1 overflow-auto px-3 py-2" value="attributes">
          <Section label="Span">
            <KeyValueRows entries={spanInfoEntries} />
          </Section>
          {modelUsageEntries.length > 0 && (
            <Section label="Model / usage">
              <KeyValueRows entries={modelUsageEntries} />
            </Section>
          )}
          {retrievalEntries.length > 0 && (
            <Section label="Retrieval">
              <KeyValueRows entries={retrievalEntries} />
            </Section>
          )}
          {overmindGroups.map((group) => (
            <Section key={group.title} label={group.title}>
              <KeyValueRows entries={group.entries} />
            </Section>
          ))}
          {spanAttributeEntries.length > 0 && (
            <Section label="Attributes">
              <KeyValueRows entries={spanAttributeEntries} />
            </Section>
          )}
        </TabsContent>
        <TabsContent className="mt-0 flex min-h-0 flex-1 flex-col overflow-hidden" value="scores">
          {scoreCount > 0 ? (
            <TraceScoresSection span={span} />
          ) : (
            <EmptyState
              description="Evaluation results for this span will appear here."
              icon={Icon.listBox}
              size="compact"
              title="No recorded scores"
            />
          )}
        </TabsContent>
        <TabsContent className="mt-0 min-h-0 flex-1 overflow-auto px-3 py-2" value="events">
          {span.events.length === 0 ? (
            <EmptyState
              description="This span has no captured events."
              icon={Icon.list}
              size="compact"
              title="No recorded events"
            />
          ) : (
            eventSections
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
