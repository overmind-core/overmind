/**
 * Every colour meaning in the app is declared here; call sites never write a
 * hex, an `hsl()` or a raw Tailwind palette class. The semantic tokens behind
 * `TONE_*` are already tuned per theme, so never pair one with a `dark:`
 * variant.
 */

/** `neutral` means "no signal", not "unknown-and-probably-bad". */
export type StatusTone = "success" | "warning" | "info" | "error" | "neutral";

/** Matches `Badge`'s variants so a bare element and a `<Badge>` look identical. */
export const TONE_CHIP: Record<StatusTone, string> = {
  error: "border-destructive/40 bg-destructive/10 text-destructive",
  info: "border-info/40 bg-info/10 text-info",
  // Tinted from `--muted-foreground`, NOT `--border`: that token lands at
  // 1.25:1 against this chip's own fill, so the edge disappears.
  neutral: "border-muted-foreground/50 bg-wash-raised text-muted-foreground",
  success: "border-success/40 bg-success/10 text-success",
  warning: "border-warning/40 bg-warning/10 text-warning",
};

const TONE_TEXT: Record<StatusTone, string> = {
  error: "text-destructive",
  info: "text-info",
  neutral: "text-muted-foreground",
  success: "text-success",
  warning: "text-warning",
};

export const TONE_FILL: Record<StatusTone, string> = {
  error: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/40",
  success: "bg-success",
  warning: "bg-warning",
};

export const TONE_BADGE_VARIANT: Record<
  StatusTone,
  "success" | "warning" | "info" | "error" | "neutral"
> = {
  error: "error",
  info: "info",
  neutral: "neutral",
  success: "success",
  warning: "warning",
};

/** The product's good / borderline / poor cut, on a 0–100 scale. */
const SCORE_GOOD_PCT = 70;
const SCORE_BORDERLINE_PCT = 40;

export function scoreTone(pct: number): StatusTone {
  if (pct >= SCORE_GOOD_PCT) return "success";
  if (pct >= SCORE_BORDERLINE_PCT) return "warning";
  return "error";
}

export const scoreChipClass = (pct: number | null | undefined): string =>
  pct == null ? TONE_CHIP.neutral : TONE_CHIP[scoreTone(pct)];

export const scoreTextClass = (pct: number | null | undefined): string =>
  pct == null ? TONE_TEXT.neutral : TONE_TEXT[scoreTone(pct)];

export const scoreFillClass = (pct: number | null | undefined): string =>
  pct == null ? TONE_FILL.neutral : TONE_FILL[scoreTone(pct)];

// Tuples rather than a flat object: the formatter sorts object keys
// alphabetically, which would scatter these groups.
const STATES_BY_TONE: Array<[StatusTone, readonly string[]]> = [
  [
    "success",
    [
      "active",
      "completed",
      "connected",
      "deployed",
      "healthy",
      "live",
      "passed",
      "ready",
      "succeeded",
    ],
  ],
  ["error", ["blocked", "cancelled", "deleted", "disconnected", "error", "failed"]],
  [
    "info",
    [
      "analysing",
      "deploying",
      "pending",
      "preparing",
      "queued",
      "running",
      "scheduled",
      "training",
      "validating_files",
      "warming",
    ],
  ],
  [
    "warning",
    ["degraded", "dormant", "partially_completed", "paused", "review", "stale", "ungraded"],
  ],
  ["neutral", ["draft", "skipped", "unknown"]],
];

const STATE_TONE: Record<string, StatusTone> = Object.fromEntries(
  STATES_BY_TONE.flatMap(([tone, states]) => states.map((state) => [state, tone]))
);

export function domainStatus(state: string | null | undefined): StatusTone {
  if (!state) return "neutral";
  return STATE_TONE[state.trim().toLowerCase()] ?? "neutral";
}

/**
 * The AA-tuned `--cat-1..6` scale in styles.css, which `--chart-1..5` aliases.
 * There is no orange slot by design: orange means "warning" in this product.
 */
export const CATEGORICAL_SLOT_COUNT = 6;

export const seriesColor = (index: number): string =>
  `var(--cat-${(Math.abs(index) % CATEGORICAL_SLOT_COUNT) + 1})`;

export const SERIES_COLORS: string[] = Array.from({ length: CATEGORICAL_SLOT_COUNT }, (_, i) =>
  seriesColor(i)
);

/**
 * A fixed brand ink, not a theme token: the pixel heading icons are inverted
 * wholesale in dark mode via `dark:invert`, so this must stay constant.
 */
export const ICON_INK = "#16120F";

/**
 * Native `<input type="datetime-local">` chrome renders its own light picker,
 * so the trigger is forced light in dark mode to match it.
 */
export const NATIVE_DATE_TRIGGER =
  "dark:border-neutral-300 dark:bg-white dark:text-neutral-900 dark:shadow-none dark:hover:bg-neutral-100";
