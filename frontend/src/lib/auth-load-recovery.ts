import { trackEvent } from "@/analytics";
import { isChunkLoadError } from "@/lib/chunk-error";

export type AuthLoadFailure = "script" | "chunk";

// One automatic reload per tab: "done" stays set after recovery, so a repeat failure cannot loop.
const RETRY_KEY = "overmind.auth-load-retry";

function readRetry(): string | null | undefined {
  try {
    return window.sessionStorage.getItem(RETRY_KEY);
  } catch {
    return undefined;
  }
}

function writeRetry(value: string): boolean {
  try {
    window.sessionStorage.setItem(RETRY_KEY, value);
    return true;
  } catch {
    return false;
  }
}

/** Reloads once per tab and returns true; returns false when the fallback must show instead. */
export function retryAuthLoad(failure: AuthLoadFailure): boolean {
  if (readRetry() !== null) return false;
  if (!writeRetry(failure)) return false;
  window.location.reload();
  return true;
}

export function markAuthLoaded() {
  const failure = readRetry();
  if (failure !== "script" && failure !== "chunk") return;
  if (!writeRetry("done")) return;
  trackEvent("auth_load_recovered", { failure });
}

function retryClerkChunk(err: unknown) {
  const message = String((err as { message?: unknown })?.message ?? err);
  if (isChunkLoadError(err) && /clerk/i.test(message)) retryAuthLoad("chunk");
}

// Clerk's hosted script fetches its own chunks, outside the app's lazy-route recovery.
export function installClerkChunkRecovery() {
  window.addEventListener("error", (event) => retryClerkChunk(event.error ?? event.message));
  window.addEventListener("unhandledrejection", (event) => retryClerkChunk(event.reason));
}
