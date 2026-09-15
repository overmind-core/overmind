import { toast } from "sonner";
import * as z from "zod";

import { ApiError, NETWORK_ERROR_MESSAGE } from "@/lib/api-error";
import { isChunkLoadError } from "@/lib/chunk-error";
import { isPaymentRequired } from "@/lib/credits";
import { isPlanLimitError } from "@/lib/plan-limits";

const DEFAULT_FALLBACK = "Something went wrong";

export function errorMessage(err: unknown, fallback = DEFAULT_FALLBACK): string {
  if (typeof err === "string") return err;
  // A ZodError's own `.message` is the raw issue array as JSON — never fit for a screen.
  if (err instanceof z.ZodError) {
    const path = err.issues[0]?.path;
    const field = path && path.length > 0 ? String(path.at(-1)) : undefined;
    return field ? `"${field}" isn't valid.` : "That input isn't valid.";
  }
  // Server detail beats the caller's fallback; a status-derived generic doesn't.
  if (err instanceof ApiError) {
    if (err.hasDetail) return err.message;
    return fallback === DEFAULT_FALLBACK ? err.message : fallback;
  }
  if (isChunkLoadError(err)) {
    return "A new version was deployed. Refresh the page to continue.";
  }
  if (
    err instanceof TypeError &&
    /fetch|network/i.test(err.message) &&
    !/dynamically imported module/i.test(err.message)
  ) {
    return NETWORK_ERROR_MESSAGE;
  }
  if (err instanceof Error && err.message) return err.message;
  if (err && typeof err === "object" && "detail" in err) {
    const d = (err as { detail?: unknown }).detail;
    if (typeof d === "string") return d;
  }
  return fallback;
}

export const notify = {
  /**
   * Silent for 402s (the out-of-credits dialog owns that moment) and for
   * plan-limit 403s (the API middleware already toasts them once).
   */
  error(err: unknown, fallback = "Something went wrong") {
    if (isPaymentRequired(err)) return;
    if (isPlanLimitError(err)) return;
    const message = errorMessage(err, fallback);
    // Keyed by message so a retry storm or batch loop stacks one toast, not N.
    toast.error(message, { id: `err:${message}` });
  },
  info(message: string, description?: string) {
    toast(message, description ? { description } : undefined);
  },
  success(message: string, description?: string) {
    toast.success(message, description ? { description } : undefined);
  },
  /** `commit()` runs only once the toast expires — Undo cancels it outright. */
  undoable({
    message,
    optimistic,
    rollback,
    commit,
    delayMs = 5000,
  }: {
    message: string;
    optimistic?: () => void;
    rollback?: () => void;
    commit: () => void;
    delayMs?: number;
  }) {
    optimistic?.();
    let undone = false;
    const timer = setTimeout(() => {
      if (!undone) commit();
    }, delayMs);
    toast(message, {
      action: {
        label: "Undo",
        onClick: () => {
          undone = true;
          clearTimeout(timer);
          rollback?.();
        },
      },
      duration: delayMs,
    });
  },
};
