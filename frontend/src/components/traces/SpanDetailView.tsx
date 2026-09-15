import type React from "react";
import { Fragment, useMemo, useState } from "react";

import { FinetuningModelChip } from "@/components/finetuning/finetuning-model-chip";
import { useTraceData } from "@/components/traces/contexts/TraceDataContext";
import {
  conflictSummary,
  parseInvocationsRationale,
  traceScoreChipLabel,
  traceScoreTone,
} from "@/components/traces/trace-score-chips";
import { BlockActions } from "@/components/ui/block-actions";
import { Icon } from "@/components/ui/icons";
import { isLikelyMarkdown, MarkdownContent } from "@/components/ui/markdown";
import type { ExecutionConflict, SpanEvent, SpanRow, TraceScoreEntry } from "@/hooks/use-traces";
import { spanStatusLabel } from "@/hooks/use-traces";
import { TONE_CHIP } from "@/lib/colors";
import { PROSE, TITLE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";
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
    <section className="px-5 py-4" id={id}>
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
    <dl className="grid grid-cols-[minmax(8rem,max-content)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-xs">
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
    <div className="grid grid-cols-[140px_80px_60px_1fr] gap-x-3 items-baseline py-1.5 border-b border-border/60 last:border-0 text-xs">
      <span className="font-mono text-cat-2 truncate">{name}</span>
      <span className="font-mono text-muted-foreground">{schema.type ?? "—"}</span>
      <span>
        {required ? (
          <span className="text-warning font-medium">required</span>
        ) : (
          <span className="text-muted-foreground/60">optional</span>
        )}
      </span>
      <span className={cn(PROSE, "text-muted-foreground leading-relaxed")}>
        {schema.description ?? ""}
        {schema.enum && schema.enum.length > 0 && (
          <span className="ml-1 text-muted-foreground/70">
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
        <div className="px-3 py-2">
          <div className="pixel-label grid grid-cols-[140px_80px_60px_1fr] gap-x-3 mb-1 text-xs text-muted-foreground/60">
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
    if (value.length === 0) return <span className="text-muted-foreground/70">""</span>;
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
    if (value.length === 0) return <span className="text-muted-foreground/70">[]</span>;
    return (
      <div className="space-y-1.5">
        {value.map((item, idx) => (
          <div className="flex gap-2" key={idx}>
            <span className="select-none font-mono text-muted-foreground/60">{idx}</span>
            <div className="min-w-0 flex-1">
              <FormattedValue value={item} />
            </div>
          </div>
        ))}
      </div>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return <span className="text-muted-foreground/70">{"{}"}</span>;
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
                  <span className="text-muted-foreground/70">""</span>
                ) : val === null ? (
                  "null"
                ) : (
                  String(val)
                )}
              </span>
            </div>
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

  if (isStringValue) {
    if (parsedValue.length === 0) return null;
  } else if (Array.isArray(parsedValue)) {
    if (parsedValue.length === 0) return null;
  } else if (parsedValue === null || typeof parsedValue !== "object") {
    return null;
  }

  const surface = "rounded-md border border-border/60 bg-wash-subtle";
  const jsonPre = `whitespace-pre-wrap break-words p-3 font-mono text-xs ${surface}`;

  let content: React.ReactNode;
  if (view === "json") {
    content = <pre className={jsonPre}>{prettyJson}</pre>;
  } else if (messageList) {
    content = (
      <div className={`divide-y divide-border/70 px-3 font-mono text-xs ${surface}`}>
        {messageList.map((msg, idx) => (
          <div className="py-3" key={idx}>
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
      <pre className={jsonPre}>{parsedValue}</pre>
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

function TraceScoreRow({ name, entry }: { name: string; entry: TraceScoreEntry }) {
  const scoreLabel = traceScoreChipLabel(name, entry);
  const isSummary = entry.lane === "summary" || name === "invocations";
  const summaryDetail = (() => {
    const parsed = parseInvocationsRationale(entry.rationale);
    return parsed ? `${parsed.ok} of ${parsed.total} invocations passed` : entry.rationale;
  })();
  const badgeClass = cn(
    "chip-label inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-xs font-semibold",
    // Coverage chip, not a pass/fail grade.
    isSummary
      ? "border-foreground/30 bg-secondary text-secondary-foreground"
      : TONE_CHIP[traceScoreTone(entry)]
  );
  return (
    <div className="rounded-sm border border-border/60 bg-wash-subtle px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs font-medium">{isSummary ? "invocations" : name}</span>
        <span className={badgeClass}>
          {isSummary && <Icon.files className="size-3 shrink-0 opacity-80" />}
          {scoreLabel}
        </span>
      </div>
      {(isSummary ? summaryDetail : entry.rationale) && (
        <p
          className={cn(
            PROSE,
            "mt-1 whitespace-pre-wrap break-words text-xs text-muted-foreground"
          )}
        >
          {isSummary ? summaryDetail : entry.rationale}
        </p>
      )}
    </div>
  );
}

function ScoreConflictRow({ conflict }: { conflict: ExecutionConflict }) {
  return (
    <div className="rounded-sm border border-warning/40 bg-warning/10 px-3 py-2">
      <div className="flex items-center gap-1.5 text-xs font-medium text-warning">
        <Icon.warning className="size-3.5 shrink-0" />
        {conflictSummary(conflict)}
      </div>
      <dl className="mt-1 space-y-0.5 text-xs text-warning">
        {Object.entries(conflict.members).map(([name, value]) => (
          <div className="flex items-center justify-between gap-3" key={name}>
            <dt className="font-mono">{name}</dt>
            <dd className="tabular-nums">{scorePct(value)}%</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function SkippedMemberRow({ name }: { name: string }) {
  return (
    <div className="rounded-sm border border-border/60 px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">{name}</span>
        <span className="chip-label inline-flex items-center rounded-sm border border-muted-foreground/50 bg-wash-raised px-2 py-0.5 text-xs font-semibold text-muted-foreground">
          Skipped
        </span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">Not run for this unit. Retryable.</p>
    </div>
  );
}

function TraceScoresSection({
  scores,
  conflict,
  skipped,
}: {
  scores: Record<string, TraceScoreEntry>;
  conflict: ExecutionConflict | null;
  skipped: string[];
}) {
  const entries = Object.entries(scores);
  if (entries.length === 0 && skipped.length === 0) return null;
  return (
    <Section
      badge={
        <span className="chip-label inline-flex items-center rounded-sm border border-border/60 bg-wash-raised px-2 py-0.5 text-xs font-semibold text-muted-foreground">
          {entries.length}
        </span>
      }
      label="Trace scores"
    >
      <div className="space-y-2">
        {conflict && <ScoreConflictRow conflict={conflict} />}
        {entries.map(([name, entry]) => (
          <TraceScoreRow entry={entry} key={name} name={name} />
        ))}
        {skipped.map((name) => (
          <SkippedMemberRow key={name} name={name} />
        ))}
      </div>
    </Section>
  );
}

interface SpanDetailViewProps {
  span: SpanRow;
}

function hasValue(value: unknown): boolean {
  return value !== null && value !== undefined && value !== "";
}

function handleJump(targetId: string) {
  document.getElementById(targetId)?.scrollIntoView({ behavior: "smooth", block: "start" });
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

  const jumpTargets = [
    hasValue(span.inputs) && { id: "span-input", label: "Input" },
    hasValue(span.outputs) && { id: "span-output", label: "Output" },
    { id: "span-metadata", label: "Metadata" },
  ].filter(Boolean) as Array<{ id: string; label: string }>;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border border-border bg-card text-card-foreground">
      <div className="shrink-0 border-b border-border/70 px-5 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h2 className={TITLE.card}>Span details</h2>
            <p className="font-mono text-xs text-muted-foreground">
              {span.name || span.scopeName || "—"} · {span.spanId.slice(0, 8)}…
            </p>
          </div>
          {span.model && (
            <FinetuningModelChip
              className="shrink-0"
              compact
              model={span.model}
              projectId={projectId}
            />
          )}
        </div>
        {jumpTargets.length > 1 && (
          <nav
            aria-label="Jump to section"
            className="mt-2 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground"
          >
            <span>Jump to</span>
            {jumpTargets.map((target, idx) => (
              <Fragment key={target.id}>
                {idx > 0 && <span aria-hidden="true">·</span>}
                <button
                  className="rounded-sm px-0.5 transition-colors hover:text-foreground"
                  onClick={() => handleJump(target.id)}
                  type="button"
                >
                  {target.label}
                </button>
              </Fragment>
            ))}
          </nav>
        )}
      </div>

      {isError && span.statusMessage && (
        <div className="shrink-0 border-b border-destructive/30 bg-destructive/5 px-5 py-2.5">
          <p className="font-mono text-xs text-destructive">{span.statusMessage}</p>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="divide-y divide-border/70">
          {span.events.length > 0 && <ExceptionEventsSection events={span.events} />}
          {(Object.keys(span.traceScores).length > 0 || span.skippedMembers.length > 0) && (
            <TraceScoresSection
              conflict={span.executionConflict}
              scores={span.traceScores}
              skipped={span.skippedMembers}
            />
          )}
          <JsonBlock id="span-input" title="Input" value={span.inputs} />
          <JsonBlock
            badge={
              span.spanAttributes?.response_type === "tool_calls" ? <ToolCallBadge /> : undefined
            }
            id="span-output"
            title="Output"
            value={span.outputs}
          />
          {Array.isArray(span.spanAttributes?.available_tools) &&
            (span.spanAttributes.available_tools as ToolDefinition[]).length > 0 && (
              <AvailableToolsBlock
                tools={span.spanAttributes.available_tools as ToolDefinition[]}
              />
            )}
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
          <Section id="span-metadata" label="Span info">
            <KeyValueRows entries={spanInfoEntries} />
          </Section>
          {overmindGroups.map((group) => (
            <Section key={group.title} label={group.title}>
              <KeyValueRows entries={group.entries} />
            </Section>
          ))}
          {spanAttributeEntries.length > 0 && (
            <Section label="Span attributes">
              <KeyValueRows entries={spanAttributeEntries} />
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
