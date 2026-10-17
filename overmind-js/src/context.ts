import { getPropagatedAttributesFromContext } from "@langfuse/core";
import { type Context, context, createContextKey } from "@opentelemetry/api";
import type { Span, SpanProcessor } from "@opentelemetry/sdk-trace-base";

import { OvermindAttributes as A } from "./attributes";

const CAPABILITY_ID_KEY = createContextKey("overmind.capability.id");
const CAPABILITY_NAME_KEY = createContextKey("overmind.capability.name");

/** Spans started inside `fn` carry this capability, overriding the `init()` default. */
export function withCapability<T>(capability: { id: string; name?: string }, fn: () => T): T {
  let ctx = context.active().setValue(CAPABILITY_ID_KEY, capability.id);
  if (capability.name) ctx = ctx.setValue(CAPABILITY_NAME_KEY, capability.name);
  return context.with(ctx, fn);
}

export interface IdentityDefaults {
  capabilityId?: string;
  capabilityName?: string;
}

/** Stamps the capability and `propagateAttributes()` values onto each span at start. */
export class OvermindContextProcessor implements SpanProcessor {
  constructor(private readonly defaults: IdentityDefaults) {}

  onStart(span: Span, parentContext: Context): void {
    const scopedId = parentContext.getValue(CAPABILITY_ID_KEY);
    // A scoped capability never inherits the default capability's name.
    const capabilityId = scopedId ?? this.defaults.capabilityId;
    const capabilityName = scopedId
      ? parentContext.getValue(CAPABILITY_NAME_KEY)
      : this.defaults.capabilityName;
    if (typeof capabilityId === "string") span.setAttribute(A.CAPABILITY_ID, capabilityId);
    if (typeof capabilityName === "string") span.setAttribute(A.CAPABILITY_NAME, capabilityName);
    span.setAttributes(getPropagatedAttributesFromContext(parentContext));
  }

  onEnd(): void {}

  forceFlush(): Promise<void> {
    return Promise.resolve();
  }

  shutdown(): Promise<void> {
    return Promise.resolve();
  }
}
