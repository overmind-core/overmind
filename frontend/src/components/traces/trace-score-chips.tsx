import { useId } from "react";

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import type { TraceScoreEntry } from "@/hooks/use-traces";
import { type StatusTone, scoreTone, TONE_CHIP } from "@/lib/colors";
import { truncateText } from "@/lib/formatters";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";

const SCORE_CHIP =
  "chip-label inline-flex h-5 items-center whitespace-nowrap rounded-sm border px-1.5 text-xs font-medium";
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
  if (entry.outcome === "error") return "error";
  if (entry.outcome === "scored" && entry.score != null) return scoreTone(scorePct(entry.score));
  return "neutral";
}

export function traceScoreChipLabel(entry: TraceScoreEntry): string {
  if (entry.outcome === "error") return "Error";
  if (entry.outcome === "abstained") return "Abstained";
  return entry.score != null ? `${scorePct(entry.score)}%` : "—";
}

type TraceExecutionScoreProps = {
  score: number | null;
  evaluations?: number;
  className?: string;
  tooltip?: string;
};

export function TraceExecutionScore({
  score,
  evaluations,
  className,
  tooltip = "Trace score",
}: TraceExecutionScoreProps) {
  if (score == null) return <span className="text-muted-foreground">—</span>;
  const pct = scorePct(score);
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(SCORE_CHIP, TONE_CHIP[scoreTone(pct)], className)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {pct}%
          </span>
        </TooltipTrigger>
        <TooltipContent className="text-xs" side="top">
          {tooltip}
          {evaluations ? ` · ${evaluations} metric${evaluations === 1 ? "" : "s"}` : ""}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

type TraceScoreChipsProps = {
  scores: Record<string, TraceScoreEntry>;
  maxChips?: number;
  className?: string;
  empty?: "dash" | "hidden";
};

export function TraceScoreChips({
  scores,
  maxChips = 3,
  className,
  empty = "dash",
}: TraceScoreChipsProps) {
  const entries = Object.entries(scores ?? {}).filter(([, entry]) => entry.outcome !== "abstained");
  if (entries.length === 0) {
    if (empty === "hidden") return null;
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
          const label = traceScoreChipLabel(entry);
          return (
            <Tooltip key={name}>
              <TooltipTrigger asChild>
                <span
                  className={cn(SCORE_CHIP, "gap-1", TONE_CHIP[traceScoreTone(entry)])}
                  onClick={(e) => e.stopPropagation()}
                  onKeyDown={(e) => e.stopPropagation()}
                >
                  {label}
                </span>
              </TooltipTrigger>
              <TooltipContent className="max-w-xs text-xs" side="top">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono font-medium">{name}</span>
                  <span className="font-medium">{label}</span>
                </div>
                {entry.rationale && (
                  <p
                    className={cn(PROSE, "mt-1 whitespace-pre-wrap break-words text-background/90")}
                  >
                    {entry.rationale}
                  </p>
                )}
              </TooltipContent>
            </Tooltip>
          );
        })}
        {extra > 0 && <span className={cn(SCORE_CHIP, TONE_CHIP.neutral)}>+{extra}</span>}
      </div>
    </TooltipProvider>
  );
}
