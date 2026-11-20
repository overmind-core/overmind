import { trackEvent } from "@/analytics";
import { isChunkLoadError } from "@/lib/chunk-error";
import { sessionStore } from "@/lib/storage";

export type AuthLoadFailure = "script" | "chunk";

// One automatic reload per tab: "done" stays set after recovery, so a repeat failure cannot loop.
const RETRY_KEY = "overmind.auth-load-retry";

/** Reloads once per tab and returns true; returns false when the fallback must show instead. */
export function retryAuthLoad(failure: AuthLoadFailure): boolean {
  if (sessionStore.get(RETRY_KEY) !== null) return false;
  if (!sessionStore.set(RETRY_KEY, failure)) return false;
  window.location.reload();
  return true;
}

export function markAuthLoaded() {
  const failure = sessionStore.get(RETRY_KEY);
  if (failure !== "script" && failure !== "chunk") return;
  if (!sessionStore.set(RETRY_KEY, "done")) return;
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
