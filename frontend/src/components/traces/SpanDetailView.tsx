import type React from "react";
import { Fragment, useMemo, useState } from "react";

import { EvaluationMethodBadge } from "@/components/evaluations/evaluation-method-badge";
import { metricResults } from "@/components/evaluations/metric-list";
import { ScoreChip } from "@/components/evaluations/run-comparison";
import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { useTraceData } from "@/components/traces/contexts/TraceDataContext";
import { Badge } from "@/components/ui/badge";
import { BlockActions } from "@/components/ui/block-actions";
import { EmptyState } from "@/components/ui/empty-state";
import { Icon } from "@/components/ui/icons";
import { isLikelyMarkdown, MarkdownContent } from "@/components/ui/markdown";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { SpanEvent, SpanRow } from "@/hooks/use-traces";
import { spanStatusLabel } from "@/hooks/use-traces";
import { formatCost, formatDuration, formatNumber } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn } from "@/lib/utils";
import type { ToolCallItem, TranscriptMessage } from "@/types/chat";

const NOOP = () => undefined;

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

function Section({
  actions,
  badge,
  children,
  id,
  label,
}: {
  actions?: React.ReactNode;
  badge?: React.ReactNode;
  children: React.ReactNode;
  id?: string;
  label: string;
}) {
  return (
    <section className="px-4 py-3" id={id}>
      <div className="mb-2 flex min-h-5 items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-muted-foreground">{label}</span>
          {badge}
        </div>
        {actions}
      </div>
      {children}
    </section>
  );
}

function KeyValueRows({ entries }: { entries: Array<[string, unknown]> }) {
  if (entries.length === 0) return null;
  return (
    <dl className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-x-4 gap-y-1.5 text-xs">
      {entries.map(([key, value]) => (
        <Fragment key={key}>
          <dt className="max-w-[20rem] break-all font-mono text-muted-foreground">
            {/* Older spans still carry PromptId; show it under the current name. */}
            {key === "PromptId" ? "CapabilityId" : key}
          </dt>
          <dd className="whitespace-pre-wrap break-all font-mono">{formatAttributeValue(value)}</dd>
        </Fragment>
      ))}
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

function groupSpanAttributes(attributes: Record<string, unknown> | undefined): {
  groups: Array<{ title: string; entries: Array<[string, unknown]> }>;
  rest: Array<[string, unknown]>;
} {
  const grouped: Record<string, Array<[string, unknown]>> = {};
  for (const { prefix } of OVERMIND_GROUPS) grouped[prefix] = [];
  const rest: Array<[string, unknown]> = [];

  if (attributes && typeof attributes === "object") {
    for (const [key, value] of Object.entries(attributes)) {
      if (HIDDEN_ATTRIBUTE_KEYS.has(key)) continue;
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

function ToolCallBlock({ tc }: { tc: ToolCallItem }) {
  const prettyArgs = useMemo(() => {
    if (!tc.function?.arguments) return "";
    try {
      return JSON.stringify(JSON.parse(tc.function.arguments), null, 2);
    } catch {
      return tc.function.arguments;
    }
  }, [tc.function?.arguments]);

  return (
    <div className="mt-1 rounded-sm border border-border/60 bg-wash-raised px-2 py-1.5">
      <span className="font-semibold text-cat-2">{tc.function?.name ?? "tool"}</span>
      {prettyArgs && (
        <pre className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
          {prettyArgs}
        </pre>
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

function MessageRow({ msg }: { msg: TranscriptMessage }) {
  const roleLabel = ROLE_LABELS[msg.role ?? ""] ?? msg.role ?? "Message";
  const hasToolCalls = (msg.tool_calls?.length ?? 0) > 0;
  const text = msg.content ?? "";
  const isTool = msg.role === "tool";
  const canMarkdown =
    !isTool && typeof text === "string" && text.length > 0 && isLikelyMarkdown(text);
  const [mode, setMode] = useState<"raw" | "markdown">(canMarkdown ? "markdown" : "raw");

  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-muted-foreground">{roleLabel}</span>
          {hasToolCalls && (
            <span className="chip-label inline-flex items-center rounded-sm border border-cat-2/30 bg-cat-2/10 px-2 py-0.5 text-xs font-semibold text-cat-2">
              Tool call
            </span>
          )}
        </div>
        {text.length > 0 && (
          <BlockActions
            mode={mode}
            onToggleMode={() => setMode((m) => (m === "raw" ? "markdown" : "raw"))}
            showToggle={canMarkdown}
            text={text}
          />
        )}
      </div>
      {text !== "" && (
        <div className="break-words">
          {mode === "markdown" && canMarkdown ? (
            <MarkdownContent compact>{text}</MarkdownContent>
          ) : (
            <div className={cn(PROSE, "whitespace-pre-wrap text-xs")}>{text}</div>
          )}
        </div>
      )}
      {msg.tool_calls?.map((tc, tcIdx) => (
        <ToolCallBlock key={tcIdx} tc={tc} />
      ))}
    </div>
  );
}

type JsonView = "formatted" | "json";

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
    return (
      <div className="space-y-1.5">
        {value.map((item, idx) => (
          <div className="flex gap-2" key={idx}>
            <span className="select-none font-mono text-muted-foreground">{idx}</span>
            <div className="min-w-0 flex-1">
              <FormattedValue value={item} />
            </div>
          </div>
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="text-muted-foreground">{"{}"}</span>;
  return (
    <div className="space-y-2">
      {entries.map(([key, val]) => {
        const inline =
          isPlainScalar(val) &&
          !(typeof val === "string" && (val.includes("\n") || val.length > 60));
        if (inline) {
          return (
            <div className="flex flex-wrap items-baseline gap-x-2" key={key}>
              <span className="font-mono text-muted-foreground">{key}</span>
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
          return (
            <details className="border-b border-border/60 py-1 last:border-0" key={key}>
              <summary className="cursor-pointer break-all font-mono text-muted-foreground hover:text-foreground">
                {key}{" "}
                <span className="ml-2 text-xs">
                  {count} {Array.isArray(val) ? "items" : "fields"}
                </span>
              </summary>
              <div className="mt-2 pl-3">
                <FormattedValue value={val} />
              </div>
            </details>
          );
        }
        return (
          <div key={key}>
            <span className="font-mono text-muted-foreground">{key}</span>
            <div className="mt-0.5 pl-3">
              <FormattedValue value={val} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ViewToggle({ onChange, value }: { onChange: (next: JsonView) => void; value: JsonView }) {
  return (
    <div
      aria-label="Toggle view format"
      className="inline-flex items-center gap-0.5 rounded-sm border border-border/60 p-0.5"
      role="group"
    >
      {(["formatted", "json"] as const).map((option) => (
        <button
          aria-pressed={value === option}
          className={cn(
            "rounded-md px-1.5 py-0.5 text-xs font-medium capitalize transition-colors",
            value === option
              ? "bg-muted text-foreground"
              : "text-muted-foreground hover:text-foreground"
          )}
          key={option}
          onClick={() => onChange(option)}
          type="button"
        >
          {option === "json" ? "JSON" : option}
        </button>
      ))}
    </div>
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
  const [view, setView] = useState<JsonView>("formatted");

  if (value === null || value === undefined) {
    return (
      <Section label={title}>
        <p className="text-xs text-muted-foreground">Not captured</p>
      </Section>
    );
  }

  const surface = "bg-wash-subtle";
  const jsonPre = `whitespace-pre-wrap break-words p-3 font-mono text-xs ${surface}`;

  let content: React.ReactNode;
  if (view === "json") {
    content = <pre className={jsonPre}>{prettyJson}</pre>;
  } else if (messageList) {
    content = (
      <div className={`divide-y divide-border/70 font-mono text-xs ${surface}`}>
        {messageList.map((msg, idx) => (
          <div className="px-3 py-3" key={idx}>
            <MessageRow msg={msg} />
          </div>
        ))}
      </div>
    );
  } else if (messageObject) {
    content = (
      <div className={`p-3 font-mono text-xs ${surface}`}>
        <MessageRow msg={messageObject} />
      </div>
    );
  } else if (isStringValue) {
    content = canMarkdown ? (
      <div className={`p-3 ${surface}`}>
        <MarkdownContent compact>{parsedValue}</MarkdownContent>
      </div>
    ) : (
      <pre className={jsonPre}>{parsedValue || '""'}</pre>
    );
  } else {
    content = (
      <div className={`p-3 text-xs ${surface}`}>
        <FormattedValue value={parsedValue} />
      </div>
    );
  }

  return (
    <Section
      actions={
        <div className="flex items-center gap-1.5">
          {showToggle && <ViewToggle onChange={setView} value={view} />}
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
      {content}
    </Section>
  );
}

function TraceScoresSection({ span }: { span: SpanRow }) {
  const entries = metricResults(span.evalMetrics);
  return (
    <Table
      aria-label="Evaluation scores"
      className="min-w-0 table-fixed text-xs"
      containerClassName="min-h-0 flex-1"
    >
      <TableHeader>
        <TableRow>
          <TableHead>Eval name</TableHead>
          <TableHead className="w-32">Type</TableHead>
          <TableHead className="w-24 text-right">Score</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {span.trajectoryScore != null && (
          <TableRow>
            <TableCell>Trajectory</TableCell>
            <TableCell className="text-muted-foreground">Aggregate</TableCell>
            <TableCell className="text-right">
              <ScoreChip value={span.trajectoryScore} />
            </TableCell>
          </TableRow>
        )}
        {entries.map(([name, metric]) => (
          <TableRow key={name}>
            <TableCell className="whitespace-normal break-words">{name}</TableCell>
            <TableCell>
              {metric.type === "llm" || metric.type === "deterministic" ? (
                <EvaluationMethodBadge type={metric.type} />
              ) : (
                <span className="text-muted-foreground">{metric.type || "Not recorded"}</span>
              )}
            </TableCell>
            <TableCell className="text-right">
              {metric.verdict === "abstain" ? (
                <Badge size="chip" variant="neutral">
                  Abstained
                </Badge>
              ) : metric.error ? (
                <Badge size="chip" variant="error">
                  Error
                </Badge>
              ) : metric.score == null ? (
                <Badge size="chip" variant="neutral">
                  Not scored
                </Badge>
              ) : (
                <ScoreChip value={metric.score} />
              )}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

interface SpanDetailViewProps {
  span: SpanRow;
}

function ExceptionEventsSection({ events }: { events: SpanEvent[] }) {
  const exceptionEvents = events.filter((e) => e.name === "exception");
  if (exceptionEvents.length === 0) return null;
  return (
    <Section label="Exceptions">
      <div className="space-y-3">
        {exceptionEvents.map((ev, idx) => {
          const attrs = ev.attributes ?? {};
          const type = attrs["exception.type"] as string | undefined;
          const message = attrs["exception.message"] as string | undefined;
          const stacktrace = attrs["exception.stacktrace"] as string | undefined;
          return (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3" key={idx}>
              {type && (
                <p className="mb-1 font-mono text-xs font-semibold text-destructive">{type}</p>
              )}
              {message && (
                <p className="whitespace-pre-wrap break-words font-mono text-xs text-destructive/80">
                  {message}
                </p>
              )}
              {stacktrace && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                    Stack trace
                  </summary>
                  <pre className="mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground">
                    {stacktrace}
                  </pre>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

export function SpanDetailView({ span }: SpanDetailViewProps) {
  const { projectId } = useTraceData();
  const { groups: overmindGroups, rest: spanAttributeEntries } = groupSpanAttributes(
    span.spanAttributes
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

  const scoreCount = Object.keys(span.traceScores).length + (span.trajectoryScore != null ? 1 : 0);

  return (
    <div className="flex h-full min-w-0 flex-col overflow-hidden bg-card text-card-foreground">
      <div className="shrink-0 px-4 pt-3 pb-2">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <h3 className="min-w-0 break-words text-sm font-medium">
            {span.name || span.scopeName || "Unnamed span"}
          </h3>
          {span.model && <FinetuningModelChip compact model={span.model} projectId={projectId} />}
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs tabular-nums text-muted-foreground">
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
      {isError && span.statusMessage && (
        <div className="shrink-0 border-y border-destructive/40 bg-destructive/5 px-4 py-2">
          <p className="break-words text-xs text-destructive">{span.statusMessage}</p>
        </div>
      )}
      <Tabs className="flex min-h-0 flex-1 flex-col" defaultValue="preview">
        <TabsList
          aria-label="Span details"
          className="h-10 w-full shrink-0 justify-start gap-3 overflow-x-auto rounded-none border-b border-border/70 bg-transparent px-4 py-0"
        >
          {[
            ["preview", "Preview"],
            ["attributes", "Attributes"],
            ["scores", `Scores (${scoreCount})`],
            ["events", `Events (${span.events.length})`],
          ].map(([value, label]) => (
            <TabsTrigger
              className="h-10 rounded-none border-b-2 border-transparent px-0 text-xs data-[state=active]:border-foreground data-[state=active]:bg-transparent data-[state=active]:text-foreground data-[state=active]:hover:text-foreground"
              key={value}
              value={value}
            >
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent className="mt-0 min-h-0 flex-1 overflow-auto" value="preview">
          <JsonBlock title="Input" value={span.inputs} />
          <JsonBlock
            badge={
              span.spanAttributes?.response_type === "tool_calls" ? <ToolCallBadge /> : undefined
            }
            title="Output"
            value={span.outputs}
          />
          {Array.isArray(span.spanAttributes?.available_tools) &&
            span.spanAttributes.available_tools.length > 0 && (
              <details className="mx-4 mb-3 border-t border-border/70 pt-3">
                <summary className="cursor-pointer text-xs text-muted-foreground">
                  Available tools ({span.spanAttributes.available_tools.length})
                </summary>
                <AvailableToolsBlock
                  tools={span.spanAttributes.available_tools as ToolDefinition[]}
                />
              </details>
            )}
        </TabsContent>
        <TabsContent
          className="mt-0 min-h-0 flex-1 divide-y divide-border/70 overflow-auto"
          value="attributes"
        >
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
        <TabsContent className="mt-0 min-h-0 flex-1 overflow-auto" value="events">
          {span.events.length === 0 ? (
            <EmptyState
              description="This span has no captured events."
              icon={Icon.list}
              size="compact"
              title="No recorded events"
            />
          ) : (
            <>
              <ExceptionEventsSection events={span.events} />
              {span.events
                .filter((event) => event.name !== "exception")
                .map((event, index) => (
                  <Section key={index} label={event.name}>
                    <KeyValueRows
                      entries={[
                        ...(event.timeUnixNano
                          ? [
                              ["Time", new Date(event.timeUnixNano / 1_000_000).toISOString()] as [
                                string,
                                unknown,
                              ],
                            ]
                          : []),
                        ...Object.entries(event.attributes ?? {}),
                      ]}
                    />
                  </Section>
                ))}
            </>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
