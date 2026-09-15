import { useId } from "react";

import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { ExecutionConflict, TraceScoreEntry } from "@/hooks/use-traces";
import { type StatusTone, scoreTone, TONE_CHIP } from "@/lib/colors";
import { truncateText } from "@/lib/formatters";
import { humanizeKey, sentenceCase } from "@/lib/label-case";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";

const SCORE_CHIP =
  "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium";
const RATIONALE_MAX = 240;

type ScoreReasonChipProps = {
  label: string;
  tone: StatusTone;
  rationale?: string | null;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
  ariaLabel?: string;
  onClick?: () => void;
};

export function ScoreReasonChip({
  label,
  tone,
  rationale,
  className,
  side = "left",
  ariaLabel,
  onClick,
}: ScoreReasonChipProps) {
  const reasonId = useId();
  const raw = (rationale ?? "").trim();
  const reason = raw ? truncateText(raw, RATIONALE_MAX) : "";
  const chipClass = cn(SCORE_CHIP, "shrink-0", TONE_CHIP[tone], className);

  if (!reason) {
    return (
      <span aria-label={ariaLabel ?? label} className={chipClass}>
        {label}
      </span>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-describedby={reasonId}
            aria-label={ariaLabel ?? label}
            className={cn(
              chipClass,
              "cursor-default focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            )}
            onClick={(e) => {
              e.stopPropagation();
              onClick?.();
            }}
            onKeyDown={(e) => e.stopPropagation()}
            title={reason}
            type="button"
          >
            {label}
          </button>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-xs" side={side}>
          <p className={cn(PROSE, "whitespace-pre-wrap break-words text-background/90")}>
            {reason}
          </p>
        </TooltipContent>
      </Tooltip>
      <span className="sr-only" id={reasonId}>
        {reason}
      </span>
    </TooltipProvider>
  );
}

export function traceScoreTone(entry: TraceScoreEntry): StatusTone {
  if (entry.outcome === "scored" && entry.score != null) return scoreTone(scorePct(entry.score));
  if (entry.passed === true) return "success";
  if (entry.passed === false) return "error";
  return "neutral";
}

function traceScoreChipClass(entry: TraceScoreEntry): string {
  return TONE_CHIP[traceScoreTone(entry)];
}

/** Coverage chip, not a pass/fail grade. */
function summaryChipClass(): string {
  return "border-foreground/30 bg-secondary text-secondary-foreground";
}

function isInvocationsSummary(name: string, entry: TraceScoreEntry): boolean {
  return entry.lane === "summary" || name === "invocations";
}

/** Rationale reads "10/20 invocations passed". */
export function parseInvocationsRationale(
  rationale: string | undefined
): { ok: number; total: number } | null {
  const m = (rationale || "").match(/(\d+)\s*\/\s*(\d+)/);
  if (!m) return null;
  return { ok: Number(m[1]), total: Number(m[2]) };
}

export function traceScoreChipLabel(name: string, entry: TraceScoreEntry): string {
  // The total, not ok/total: this counts coverage, not a pass ratio.
  if (isInvocationsSummary(name, entry)) {
    const parsed = parseInvocationsRationale(entry.rationale);
    if (parsed) return `${parsed.total} scored`;
    return "Scored";
  }
  if (entry.outcome === "scored" && entry.score != null) {
    return `${scorePct(entry.score)}%`;
  }
  if (entry.outcome === "scored") {
    if (entry.passed === true) return "Pass";
    if (entry.passed === false) return "Fail";
    return "Scored";
  }
  if (entry.outcome === "abstained") return "Abstained";
  // Wire outcomes are snake_case (e.g. `not_applicable` on a run-surface row).
  return entry.outcome ? humanizeKey(entry.outcome) : "—";
}

function summaryTooltipBody(entry: TraceScoreEntry): string {
  const parsed = parseInvocationsRationale(entry.rationale);
  if (parsed) return `${parsed.ok} of ${parsed.total} invocations passed`;
  return entry.rationale || "Nested invocations were scored individually.";
}

const CONFLICT_LANE_LABEL: Record<string, string> = { output: "Outcome" };

export function conflictSummary(conflict: ExecutionConflict): string {
  const values = Object.values(conflict.members);
  const lo = scorePct(Math.min(...values));
  const hi = scorePct(Math.max(...values));
  const lane = CONFLICT_LANE_LABEL[conflict.lane] ?? sentenceCase(conflict.lane || "outcome");
  return `${lane} judges disagree: ${lo}% vs ${hi}%`;
}

/** Rendered beside the score chip, never inside it. */
export function ScoreConflictMarker({
  conflict,
  className,
}: {
  conflict: ExecutionConflict;
  className?: string;
}) {
  const summary = conflictSummary(conflict);
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label={summary}
            className={cn(
              "inline-flex h-5 shrink-0 items-center rounded-sm border border-warning/40 bg-warning/10 px-1 text-warning",
              className
            )}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            <Icon.warning className="size-3" />
          </span>
        </TooltipTrigger>
        <TooltipContent className="max-w-xs text-xs" side="top">
          <p className="font-medium">{summary}</p>
          <ul className="mt-1">
            {Object.entries(conflict.members).map(([name, value]) => (
              <li className="flex items-center justify-between gap-3" key={name}>
                <span className="font-mono">{name}</span>
                <span className="tabular-nums">{scorePct(value)}%</span>
              </li>
            ))}
          </ul>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

type TraceExecutionScoreProps = {
  score: number | null;
  evaluations?: number;
  className?: string;
  tooltip?: string;
  scoringPending?: boolean;
  conflict?: ExecutionConflict | null;
};

export function TraceExecutionScore({
  score,
  evaluations,
  className,
  tooltip = "Task execution score",
  scoringPending = false,
  conflict = null,
}: TraceExecutionScoreProps) {
  if (score == null) {
    if (scoringPending) return <Spinner className={className} size="sm" />;
    return <span className="text-muted-foreground">—</span>;
  }
  const pct = scorePct(score);
  return (
    <span className="inline-flex items-center gap-1">
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              className={cn(
                "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium",
                TONE_CHIP[scoreTone(pct)],
                className
              )}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {pct}%
            </span>
          </TooltipTrigger>
          <TooltipContent className="text-xs" side="top">
            {tooltip}
            {evaluations ? ` · ${evaluations} evaluation${evaluations === 1 ? "" : "s"}` : ""}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      {conflict && <ScoreConflictMarker conflict={conflict} />}
    </span>
  );
}

type TraceScoreChipsProps = {
  scores: Record<string, TraceScoreEntry>;
  maxChips?: number;
  className?: string;
  empty?: "dash" | "hidden";
  /** Ignored when `empty` is `"hidden"`. */
  scoringPending?: boolean;
};

export function TraceScoreChips({
  scores,
  maxChips = 3,
  className,
  empty = "dash",
  scoringPending = false,
}: TraceScoreChipsProps) {
  const entries = Object.entries(scores ?? {});
  if (entries.length === 0) {
    if (empty === "hidden") return null;
    if (scoringPending) return <Spinner className={className} size="sm" />;
    return <span className="text-muted-foreground">—</span>;
  }
  const shown = entries.slice(0, maxChips);
  const extra = entries.length - shown.length;
  return (
    <TooltipProvider>
      <div
        aria-label="Trace scores"
        className={cn("flex flex-wrap items-center gap-1", className)}
        role="group"
      >
        {shown.map(([name, entry]) => {
          const scoreLabel = traceScoreChipLabel(name, entry);
          const isPct = entry.outcome === "scored" && entry.score != null;
          const isSummary = isInvocationsSummary(name, entry);
          return (
            <Tooltip key={name}>
              <TooltipTrigger asChild>
                <span
                  className={cn(
                    "chip-label inline-flex h-5 items-center gap-1 rounded-sm border px-1.5 text-xs font-medium",
                    isSummary ? summaryChipClass() : traceScoreChipClass(entry)
                  )}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  {isSummary && <Icon.files className="size-3 shrink-0 opacity-80" />}
                  {scoreLabel}
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs text-xs" side="top">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-medium">{isSummary ? "invocations" : name}</span>
                  {isPct && <span className="font-medium">{scoreLabel}</span>}
                </div>
                {isSummary ? (
                  <p className={cn(PROSE, "mt-1 text-background/90")}>
                    {summaryTooltipBody(entry)}
                  </p>
                ) : entry.rationale ? (
                  <p
                    className={cn(PROSE, "mt-1 whitespace-pre-wrap break-words text-background/90")}
                  >
                    {entry.rationale}
                  </p>
                ) : null}
              </TooltipContent>
            </Tooltip>
          );
        })}
        {extra > 0 && (
          <span
            className={cn(
              "chip-label inline-flex h-5 items-center rounded-sm border px-1.5 text-xs font-medium",
              TONE_CHIP.neutral
            )}
          >
            +{extra}
          </span>
        )}
      </div>
    </TooltipProvider>
  );
}
