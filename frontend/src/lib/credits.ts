/**
 * Every 402 publishes to this module's listeners, which the global
 * `OutOfCreditsDialog` renders — which is why `notify.error` stays silent for a
 * 402 rather than stacking a toast on top of the dialog.
 */

const OUT_OF_CREDITS_MESSAGE = "You're out of credits.";

/** Carries the server's message, already extracted from the 402 body. */
export class PaymentRequiredError extends Error {
  override name = "PaymentRequiredError";
  readonly status = 402;

  constructor(message = OUT_OF_CREDITS_MESSAGE) {
    super(message);
  }
}

export function isPaymentRequired(err: unknown): boolean {
  if (err instanceof PaymentRequiredError) return true;
  if (!err || typeof err !== "object") return false;
  const response = (err as { response?: unknown }).response;
  return (
    !!response && typeof response === "object" && (response as { status?: number }).status === 402
  );
}

/** Falls back to the shared copy rather than surfacing a status line. */
export function paymentRequiredMessage(body: string): string {
  try {
    const parsed = JSON.parse(body) as {
      detail?: unknown;
      error?: { message?: unknown };
    };
    const detail = parsed?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    const errorMessage = parsed?.error?.message;
    if (typeof errorMessage === "string" && errorMessage.trim()) return errorMessage;
  } catch {
    // Non-JSON body — the generic message is better than the raw text.
  }
  return OUT_OF_CREDITS_MESSAGE;
}

export type PaymentRequiredContext = {
  /** Verb phrase, e.g. "start this training run" — embedded in the dialog copy. */
  action?: string;
};

type Listener = (context: PaymentRequiredContext) => void;

const listeners = new Set<Listener>();

export function onPaymentRequired(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function emitPaymentRequired(context: PaymentRequiredContext = {}) {
  for (const listener of listeners) listener(context);
}

/**
 * Surfaces that explain a spent balance in place claim the moment while
 * mounted, so the global dialog stays out of their way.
 */
let surfaceClaims = 0;

export function claimCreditsSurface(): () => void {
  surfaceClaims += 1;
  return () => {
    surfaceClaims -= 1;
  };
}

export function hasCreditsSurface(): boolean {
  return surfaceClaims > 0;
}

/** Ordered most-specific first — several of these prefixes nest. */
const PATH_ACTIONS: ReadonlyArray<readonly [string, string]> = [
  ["/generate-evals", "generate evaluators"],
  ["/compile-rubric", "compile this rubric"],
  ["/generate-prompt", "generate this evaluator prompt"],
  ["/context/refresh", "refresh this dataset's context"],
  ["/finetuning-jobs", "start this training run"],
  ["/deployed-models", "deploy this model"],
  ["/eval-runs", "run this evaluation"],
  ["/optimizer-experiments", "start this optimisation"],
  ["/chat/completions", "run this model"],
  ["/jobs", "run this job"],
];

export function actionForPath(path: string): string | undefined {
  for (const [fragment, action] of PATH_ACTIONS) {
    if (path.includes(fragment)) return action;
  }
  return undefined;
}
