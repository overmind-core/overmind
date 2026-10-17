import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  type SpanExporter,
  type SpanProcessor,
  SimpleSpanProcessor,
} from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";

import pkg from "../package.json";
import { OvermindAttributes as A } from "./attributes";
import { OvermindContextProcessor } from "./context";
import { OvermindSpanExporter, type ShouldExportSpan } from "./exporter";

export const DEFAULT_BASE_URL = "https://api.overmindlab.ai";

export interface OvermindOptions {
  /** Defaults to `OVERMIND_API_KEY`. Without one, tracing stays off. */
  apiKey?: string;
  /** Defaults to `OVERMIND_API_URL`, then Overmind Cloud. Spans go to `{baseUrl}/api/v1/traces`. */
  baseUrl?: string;
  /** Defaults to `OVERMIND_CAPABILITY_ID`. */
  capabilityId?: string;
  /** Defaults to `OVERMIND_CAPABILITY_NAME`. */
  capabilityName?: string;
  /** Defaults to `OVERMIND_PROJECT_ID`. Needed only for account-scoped keys. */
  projectId?: string;
  /** Defaults to `OVERMIND_SERVICE_NAME`. */
  serviceName?: string;
  /** Defaults to `OVERMIND_ENVIRONMENT`, then `NODE_ENV`. */
  environment?: string;
  /** `immediate` exports each span as it ends; for short-lived scripts and tests. */
  exportMode?: "batched" | "immediate";
  /** Batch flush interval in milliseconds. Defaults to `OVERMIND_SPAN_FLUSH_INTERVAL_MS` or 2000. */
  flushIntervalMs?: number;
  /** Replaces the default filter that keeps unrelated spans out of Overmind. */
  shouldExportSpan?: ShouldExportSpan;
  /** Replaces the OTLP/protobuf wire exporter. */
  exporter?: SpanExporter;
}

let provider: NodeTracerProvider | undefined;

function wireExporter(apiKey: string, baseUrl: string): SpanExporter {
  return new OTLPTraceExporter({
    url: `${baseUrl}/api/v1/traces`,
    headers: { "X-Api-Key": apiKey },
  });
}

/** Registers the global tracer provider that sends Langfuse observations to Overmind. Idempotent. */
export function init(options: OvermindOptions = {}): NodeTracerProvider | undefined {
  if (provider) return provider;

  const env = process.env;
  const apiKey = options.apiKey ?? env.OVERMIND_API_KEY;
  if (!apiKey && !options.exporter) {
    console.warn("Overmind tracing is off: set OVERMIND_API_KEY or pass apiKey to init().");
    return undefined;
  }

  const baseUrl = (options.baseUrl ?? env.OVERMIND_API_URL ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
  const capabilityId = options.capabilityId ?? env.OVERMIND_CAPABILITY_ID;
  const capabilityName = options.capabilityName ?? env.OVERMIND_CAPABILITY_NAME;
  const projectId = options.projectId ?? env.OVERMIND_PROJECT_ID;

  const resource = resourceFromAttributes({
    "service.name": options.serviceName ?? env.OVERMIND_SERVICE_NAME ?? "overmind-telemetry",
    "deployment.environment": options.environment ?? env.OVERMIND_ENVIRONMENT ?? env.NODE_ENV ?? "development",
    [A.SDK_NAME]: "overmind-js",
    [A.SDK_VERSION]: pkg.version,
    ...(capabilityId ? { [A.CAPABILITY_ID]: capabilityId } : {}),
    ...(capabilityName ? { [A.CAPABILITY_NAME]: capabilityName } : {}),
    ...(projectId ? { [A.PROJECT_ID]: projectId } : {}),
  });

  const exporter = new OvermindSpanExporter(
    options.exporter ?? wireExporter(apiKey as string, baseUrl),
    options.shouldExportSpan,
  );
  const exportProcessor: SpanProcessor =
    options.exportMode === "immediate"
      ? new SimpleSpanProcessor(exporter)
      : new BatchSpanProcessor(exporter, {
          scheduledDelayMillis:
            options.flushIntervalMs ?? Number(env.OVERMIND_SPAN_FLUSH_INTERVAL_MS ?? 2000),
          maxExportBatchSize: 256,
        });

  provider = new NodeTracerProvider({
    resource,
    spanProcessors: [new OvermindContextProcessor({ capabilityId, capabilityName }), exportProcessor],
  });
  provider.register();
  return provider;
}

export async function flush(): Promise<void> {
  await provider?.forceFlush();
}

export async function shutdown(): Promise<void> {
  const current = provider;
  provider = undefined;
  await current?.shutdown();
}
