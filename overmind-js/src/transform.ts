import { LangfuseOtelSpanAttributes as Lf } from "@langfuse/core";
import { type Attributes, type AttributeValue, SpanStatusCode } from "@opentelemetry/api";
import { hrTimeToMilliseconds } from "@opentelemetry/core";
import type { ReadableSpan } from "@opentelemetry/sdk-trace-base";

import { OvermindAttributes as A, OvermindSpanType } from "./attributes";

const LANGFUSE_PREFIX = "langfuse.";
const OBSERVATION_METADATA_PREFIX = `${Lf.OBSERVATION_METADATA}.`;
const TRACE_METADATA_PREFIX = `${Lf.TRACE_METADATA}.`;

const SPAN_TYPE_BY_OBSERVATION: Record<string, OvermindSpanType> = {
  generation: OvermindSpanType.LLM_CALL,
  embedding: OvermindSpanType.LLM_CALL,
  tool: OvermindSpanType.TOOL_CALL,
  retriever: OvermindSpanType.RETRIEVAL,
  agent: OvermindSpanType.WORKFLOW,
  chain: OvermindSpanType.WORKFLOW,
  guardrail: OvermindSpanType.WORKFLOW,
  evaluator: OvermindSpanType.WORKFLOW,
  span: OvermindSpanType.FUNCTION,
  event: OvermindSpanType.FUNCTION,
};

const USAGE_KEYS = {
  prompt: ["input", "prompt_tokens", "input_tokens", "promptTokens", "inputTokens"],
  completion: ["output", "completion_tokens", "output_tokens", "completionTokens", "outputTokens"],
  total: ["total", "total_tokens", "totalTokens"],
  cacheRead: [
    "cache_read_input_tokens",
    "cache_read_tokens",
    "input_cached_tokens",
    "input_cache_read",
    "cacheReadInputTokens",
  ],
} as const;

const PARAMETER_KEYS = {
  temperature: ["temperature"],
  maxTokens: ["max_tokens", "maxTokens", "max_completion_tokens", "maxOutputTokens"],
  topP: ["top_p", "topP"],
} as const;

type JsonObject = Record<string, unknown>;

export type SpanView = Pick<
  ReadableSpan,
  "name" | "attributes" | "status" | "startTime" | "endTime" | "events"
>;

function parseJson(value: AttributeValue | undefined): unknown {
  if (typeof value !== "string") return undefined;
  try {
    return JSON.parse(value);
  } catch {
    return undefined;
  }
}

function parseObject(value: AttributeValue | undefined): JsonObject | undefined {
  const parsed = parseJson(value);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as JsonObject)
    : undefined;
}

function pickNumber(source: JsonObject | undefined, keys: readonly string[]): number | undefined {
  if (!source) return undefined;
  for (const key of keys) {
    const raw = source[key];
    const num = typeof raw === "string" && raw.trim() !== "" ? Number(raw) : raw;
    if (typeof num === "number" && Number.isFinite(num)) return num;
  }
  return undefined;
}

function sumDefined(...values: Array<number | undefined>): number | undefined {
  const present = values.filter((v): v is number => v !== undefined);
  return present.length ? present.reduce((a, b) => a + b, 0) : undefined;
}

export function toOvermindAttributes(span: SpanView): Attributes {
  const out: Attributes = {};
  const langfuse: Attributes = {};

  for (const [key, value] of Object.entries(span.attributes)) {
    if (value === undefined) continue;
    if (key.startsWith(LANGFUSE_PREFIX)) langfuse[key] = value;
    else out[key] = value;
  }

  // Attributes the caller already set in Overmind's vocabulary win.
  const put = (key: string, value: AttributeValue | undefined) => {
    if (value === undefined || out[key] !== undefined) return;
    out[key] = value;
  };

  const observationType = str(langfuse[Lf.OBSERVATION_TYPE]);
  const spanType = observationType ? SPAN_TYPE_BY_OBSERVATION[observationType] : undefined;
  put(A.SPAN_TYPE, spanType);

  const input = str(langfuse[Lf.OBSERVATION_INPUT]) ?? str(langfuse[Lf.TRACE_INPUT]);
  const output = str(langfuse[Lf.OBSERVATION_OUTPUT]) ?? str(langfuse[Lf.TRACE_OUTPUT]);
  put(A.INPUTS, input);
  put(A.OUTPUTS, output);

  if (spanType === OvermindSpanType.TOOL_CALL) {
    put(A.TOOL_NAME, span.name);
    const args = parseObject(input);
    if (args) put(A.TOOL_ARG_KEYS, JSON.stringify(Object.keys(args)));
  }
  if (spanType === OvermindSpanType.RETRIEVAL) {
    if (input !== undefined) put(A.RETRIEVAL_QUERY_CHARS, input.length);
    const results = parseJson(langfuse[Lf.OBSERVATION_OUTPUT]);
    if (Array.isArray(results)) put(A.RETRIEVAL_RESULT_COUNT, results.length);
  }

  mapModel(langfuse, span, put);
  mapStatus(langfuse, span, put);
  mapIdentity(langfuse, out, put);
  mapMetadata(langfuse, out);

  put(A.DURATION_SECONDS, (hrTimeToMilliseconds(span.endTime) - hrTimeToMilliseconds(span.startTime)) / 1000);
  return out;
}

function str(value: AttributeValue | undefined): string | undefined {
  return typeof value === "string" ? value : undefined;
}

type Put = (key: string, value: AttributeValue | undefined) => void;

function mapModel(langfuse: Attributes, span: SpanView, put: Put): void {
  const model = str(langfuse[Lf.OBSERVATION_MODEL]);
  if (model) {
    put(A.LLM_MODEL, model);
    put(A.OTEL_LLM_REQUEST_MODEL, model);
  }

  const parameters = parseObject(langfuse[Lf.OBSERVATION_MODEL_PARAMETERS]);
  if (parameters) {
    put(A.LLM_REQUEST_TEMPERATURE, pickNumber(parameters, PARAMETER_KEYS.temperature));
    put(A.LLM_REQUEST_MAX_TOKENS, pickNumber(parameters, PARAMETER_KEYS.maxTokens));
    put(A.LLM_REQUEST_TOP_P, pickNumber(parameters, PARAMETER_KEYS.topP));
    const known = new Set<string>([
      ...PARAMETER_KEYS.temperature,
      ...PARAMETER_KEYS.maxTokens,
      ...PARAMETER_KEYS.topP,
    ]);
    const rest = Object.fromEntries(Object.entries(parameters).filter(([k]) => !known.has(k)));
    if (Object.keys(rest).length) put(A.LLM_REQUEST_KWARGS, JSON.stringify(rest));
  }

  const usage = parseObject(langfuse[Lf.OBSERVATION_USAGE_DETAILS]);
  if (usage) {
    const prompt = pickNumber(usage, USAGE_KEYS.prompt);
    const completion = pickNumber(usage, USAGE_KEYS.completion);
    const total = pickNumber(usage, USAGE_KEYS.total) ?? sumDefined(prompt, completion);
    put(A.LLM_PROMPT_TOKENS, prompt);
    put(A.LLM_COMPLETION_TOKENS, completion);
    put(A.LLM_TOTAL_TOKENS, total);
    put(A.LLM_CACHE_READ_TOKENS, pickNumber(usage, USAGE_KEYS.cacheRead));
    put(A.OTEL_LLM_USAGE_PROMPT_TOKENS, prompt);
    put(A.OTEL_LLM_USAGE_COMPLETION_TOKENS, completion);
    put(A.OTEL_LLM_USAGE_TOTAL_TOKENS, total);
  }

  const cost = parseObject(langfuse[Lf.OBSERVATION_COST_DETAILS]);
  if (cost) {
    put(A.LLM_COST, pickNumber(cost, ["total"]) ?? sumDefined(pickNumber(cost, ["input"]), pickNumber(cost, ["output"])));
  }

  const completionStart = parseJson(langfuse[Lf.OBSERVATION_COMPLETION_START_TIME]);
  if (typeof completionStart === "string") {
    const firstTokenMs = Date.parse(completionStart);
    const elapsed = (firstTokenMs - hrTimeToMilliseconds(span.startTime)) / 1000;
    if (Number.isFinite(elapsed) && elapsed >= 0) put(A.LLM_TTFT_SECONDS, elapsed);
  }
}

function mapStatus(langfuse: Attributes, span: SpanView, put: Put): void {
  const failed = span.status.code === SpanStatusCode.ERROR || str(langfuse[Lf.OBSERVATION_LEVEL]) === "ERROR";
  put(A.STATUS, failed ? "failed" : "success");
  if (!failed) return;

  const message = span.status.message || str(langfuse[Lf.OBSERVATION_STATUS_MESSAGE]);
  put(A.ERROR_MESSAGE, message);
  const exception = span.events.find((event) => event.name === "exception");
  put(A.ERROR_TYPE, str(exception?.attributes?.["exception.type"]));
}

function mapIdentity(langfuse: Attributes, out: Attributes, put: Put): void {
  put(A.WORKFLOW_NAME, str(langfuse[Lf.TRACE_NAME]));
  // `user.id` and `session.id` carry no `langfuse.` prefix, so they arrive in `out`.
  put(A.CONVERSATION_ID, str(out[Lf.TRACE_SESSION_ID]) ?? str(langfuse[Lf.TRACE_COMPAT_SESSION_ID]));
  put(Lf.TRACE_USER_ID, str(langfuse[Lf.TRACE_COMPAT_USER_ID]));
  put(A.ENVIRONMENT, str(langfuse[Lf.ENVIRONMENT]));
  put(A.RELEASE, str(langfuse[Lf.RELEASE]));
  put(A.VERSION, str(langfuse[Lf.VERSION]));
  put(A.PROMPT_NAME, str(langfuse[Lf.OBSERVATION_PROMPT_NAME]));
  const promptVersion = langfuse[Lf.OBSERVATION_PROMPT_VERSION];
  put(A.PROMPT_VERSION, typeof promptVersion === "number" || typeof promptVersion === "string" ? promptVersion : undefined);

  const tags = langfuse[Lf.TRACE_TAGS];
  if (Array.isArray(tags) && tags.length) put(A.TRACE_TAGS, JSON.stringify(tags));
}

function mapMetadata(langfuse: Attributes, out: Attributes): void {
  // Observation-level values take precedence over the trace-level ones `propagateAttributes` stamps.
  for (const [prefix, bare] of [
    [OBSERVATION_METADATA_PREFIX, Lf.OBSERVATION_METADATA],
    [TRACE_METADATA_PREFIX, Lf.TRACE_METADATA],
  ] as const) {
    for (const [key, value] of Object.entries(langfuse)) {
      if (typeof value !== "string") continue;
      let target: string | undefined;
      if (key === bare) target = "metadata";
      else if (key.startsWith(prefix)) target = `metadata.${key.slice(prefix.length)}`;
      if (target) out[target] ??= plainString(value);
    }
  }
}

/** Langfuse JSON-encodes metadata values, so a plain string arrives quoted. */
function plainString(value: string): string {
  const parsed = parseJson(value);
  return typeof parsed === "string" ? parsed : value;
}
