import { LANGFUSE_TRACER_NAME } from "@langfuse/core";
import type { ExportResult } from "@opentelemetry/core";
import { ExportResultCode } from "@opentelemetry/core";
import type { ReadableSpan, SpanExporter } from "@opentelemetry/sdk-trace-base";

import { OvermindAttributes as A } from "./attributes";
import { toOvermindAttributes } from "./transform";

export type ShouldExportSpan = (span: ReadableSpan) => boolean;

const OBSERVABILITY_PREFIXES = ["langfuse.", "gen_ai.", "genai.", "overmind.", "llm.", "tool.", "openinference."];

// Stamped on every span by the context processor, so they say nothing about the span itself.
const IDENTITY_KEYS = new Set<string>([A.CAPABILITY_ID, A.CAPABILITY_NAME]);

/** Langfuse observations and spans carrying GenAI or Overmind attributes; unrelated HTTP/DB spans stay local. */
export function isObservabilitySpan(span: ReadableSpan): boolean {
  if (span.instrumentationScope.name === LANGFUSE_TRACER_NAME) return true;
  return Object.keys(span.attributes).some(
    (key) => !IDENTITY_KEYS.has(key) && OBSERVABILITY_PREFIXES.some((prefix) => key.startsWith(prefix)),
  );
}

export function toOvermindSpan(span: ReadableSpan): ReadableSpan {
  return {
    name: span.name,
    kind: span.kind,
    spanContext: () => span.spanContext(),
    parentSpanContext: span.parentSpanContext,
    startTime: span.startTime,
    endTime: span.endTime,
    status: span.status,
    attributes: toOvermindAttributes(span),
    links: span.links,
    events: span.events,
    duration: span.duration,
    ended: span.ended,
    resource: span.resource,
    instrumentationScope: span.instrumentationScope,
    droppedAttributesCount: span.droppedAttributesCount,
    droppedEventsCount: span.droppedEventsCount,
    droppedLinksCount: span.droppedLinksCount,
  };
}

/** Rewrites Langfuse attributes to the Overmind contract before delegating to the wire exporter. */
export class OvermindSpanExporter implements SpanExporter {
  constructor(
    private readonly inner: SpanExporter,
    private readonly shouldExportSpan: ShouldExportSpan = isObservabilitySpan,
  ) {}

  export(spans: ReadableSpan[], resultCallback: (result: ExportResult) => void): void {
    const exported = spans.filter(this.shouldExportSpan).map(toOvermindSpan);
    if (exported.length === 0) {
      resultCallback({ code: ExportResultCode.SUCCESS });
      return;
    }
    this.inner.export(exported, resultCallback);
  }

  forceFlush(): Promise<void> {
    return this.inner.forceFlush?.() ?? Promise.resolve();
  }

  shutdown(): Promise<void> {
    return this.inner.shutdown();
  }
}
