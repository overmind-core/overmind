import { observe as langfuseObserve, type ObserveOptions } from "@langfuse/tracing";
import { trace } from "@opentelemetry/api";

import { OvermindAttributes as A } from "./attributes";
import { withCapability } from "./context";

export interface OvermindObserveOptions extends ObserveOptions {
  /** Binds the wrapped call and its descendants to this capability. Set it on entry points. */
  capabilityId?: string;
  capabilityName?: string;
  /** What the request asked for; overrides the intent read from the root input. */
  intent?: string;
  /** Checkable sentences about the span's output; `overmind sync` copies them into the capability's eval set. */
  expectations?: string[];
  /** Tool names, in order, a good run of this entry point calls. */
  referenceTrajectory?: string[];
}

const clean = (items: string[] | undefined) => (items ?? []).map((item) => item.trim()).filter(Boolean);

/** Langfuse's `observe`, plus the Overmind declarations `overmind chassis` and `overmind sync` read from source. */
export function observe<T extends (...args: any[]) => any>(fn: T, options: OvermindObserveOptions = {}): T {
  const { capabilityId, capabilityName, intent, expectations, referenceTrajectory, ...langfuseOptions } = options;
  const declared: Record<string, string | string[]> = {};
  if (intent?.trim()) declared[A.INTENT] = intent.trim();
  if (clean(expectations).length) declared[A.EXPECTATIONS] = clean(expectations);
  if (clean(referenceTrajectory).length) declared[A.REFERENCE_TRAJECTORY] = clean(referenceTrajectory);

  const stamped = function (this: unknown, ...args: Parameters<T>): ReturnType<T> {
    trace.getActiveSpan()?.setAttributes(declared);
    return fn.apply(this, args);
  } as T;
  const observed = langfuseObserve(stamped, langfuseOptions);

  if (!capabilityId) return observed;
  return function (this: unknown, ...args: Parameters<T>): ReturnType<T> {
    return withCapability({ id: capabilityId, name: capabilityName }, () => observed.apply(this, args));
  } as T;
}
