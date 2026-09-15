/**
 * Plan quota refusals: 403 with ``code: plan_limit_exceeded`` /
 * ``seat_limit_exceeded``. Unlike a credits 402, these toast a pricing link.
 */

import { toast } from "sonner";

import { PUBLIC_PRICING_URL } from "@/lib/marketing";

export class PlanLimitError extends Error {
  override name = "PlanLimitError";
  readonly status = 403;
  readonly code: string;

  constructor(message: string, code = "plan_limit_exceeded") {
    super(message);
    this.code = code;
  }
}

export function isPlanLimitError(err: unknown): boolean {
  return err instanceof PlanLimitError;
}

/** Parse DRF body ``{"detail":"…","code":"plan_limit_exceeded"}`` (or nested detail). */
export function planLimitFromBody(body: string): { message: string; code: string } | null {
  try {
    const parsed = JSON.parse(body) as {
      detail?: unknown;
      code?: unknown;
    };
    let message: string | undefined;
    let code: string | undefined;
    if (typeof parsed.code === "string") code = parsed.code;
    if (typeof parsed.detail === "string") {
      message = parsed.detail;
    } else if (parsed.detail && typeof parsed.detail === "object") {
      const d = parsed.detail as { detail?: unknown; code?: unknown };
      if (typeof d.detail === "string") message = d.detail;
      if (typeof d.code === "string") code = d.code;
    }
    if (
      code === "plan_limit_exceeded" ||
      code === "seat_limit_exceeded" ||
      (message && /Free plan|Upgrade to Pro/i.test(message))
    ) {
      return {
        code: code || "plan_limit_exceeded",
        message: message || "Plan limit reached. Upgrade to Pro for higher limits.",
      };
    }
  } catch {
    // non-JSON
  }
  return null;
}

export function emitPlanLimitToast(message: string) {
  // Fixed id: query retries fire this twice per refused request — keep one toast.
  toast.error(message, {
    action: {
      label: "Upgrade",
      onClick: () => {
        window.open(PUBLIC_PRICING_URL, "_blank", "noopener,noreferrer");
      },
    },
    id: "plan-limit",
  });
}
