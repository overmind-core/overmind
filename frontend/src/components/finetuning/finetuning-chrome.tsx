import type { ReactNode } from "react";

import { DeltaChip } from "@/components/ui/delta-chip";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { scoreChipClass } from "@/lib/colors";
import { ResolvedStatusBadge, resolveStatus } from "@/lib/job-status";
import { PROSE } from "@/lib/typography";
import { cn, scorePct } from "@/lib/utils";

export { DeltaChip };

const EXPERIMENT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
] as const;

export const experimentColor = (index: number): string =>
  EXPERIMENT_COLORS[index % EXPERIMENT_COLORS.length];

export const METRIC_HELP = {
  accuracy: "Share of next tokens predicted exactly right on the training data, per step.",
  classSeries: "Precision, recall and F1 per class across checkpoint evals.",
  classTable:
    "Per-class precision, recall and F1 from the latest checkpoint eval. Support is the number of held-out examples with that true label.",
  confusion:
    "Rows are true labels, columns are predicted labels. Hover a cell for the count and row percentage.",
  gradNorm: "Global gradient norm per optimiser step.",
  learningRate: "Learning-rate schedule over training steps.",
  loss: "Cross-entropy training loss per optimiser step.",
} as const;

export function HelpTip({ label, text }: { label: string; text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          aria-label={`What does ${label} show?`}
          className="inline-flex size-4 shrink-0 items-center justify-center rounded-sm border border-border text-xs leading-none text-muted-foreground transition-colors hover:bg-wash-raised hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          type="button"
        >
          ?
        </button>
      </TooltipTrigger>
      <TooltipContent className={cn(PROSE, "max-w-72")} side="top">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

/** Same score tiers as the evaluations run UI. */
export function EvalScoreChip({ value }: { value: number | null | undefined }) {
  if (value == null) return <span className="text-muted-foreground/40">—</span>;
  const pct = scorePct(value);
  return (
    <span
      className={cn(
        "inline-flex items-center justify-center rounded-sm border px-1.5 py-0.5 font-mono text-xs font-semibold tabular-nums",
        scoreChipClass(pct)
      )}
    >
      {pct}%
    </span>
  );
}

/** Scoped to fine-tuning on purpose: the skull glyph is local, every other
 *  page uses the neutral StatusBadge. */
export function FtStatusBadge({
  status,
  fallback,
  label,
  solidProgress = false,
  progress,
  className,
}: {
  status: string | null | undefined;
  fallback: string;
  label?: string;
  solidProgress?: boolean;
  progress?: number | null;
  className?: string;
}) {
  const cfg = resolveStatus(status, fallback);
  const icon =
    cfg.tone === "success" ? (
      <Icon.success className="size-3.5" />
    ) : cfg.tone === "error" ? (
      <Icon.skull className="size-3.5" />
    ) : (
      cfg.icon
    );
  return (
    <ResolvedStatusBadge
      cfg={cfg}
      className={className}
      icon={icon}
      label={label}
      progress={progress}
      solidProgress={solidProgress}
    />
  );
}

export function HeaderStat({
  label,
  primary = false,
  children,
  className,
}: {
  label: string;
  primary?: boolean;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        {label}
      </span>
      <span
        className={cn(
          "font-mono tabular-nums leading-none",
          primary ? "text-xl font-semibold" : "text-sm font-medium"
        )}
      >
        {children}
      </span>
    </div>
  );
}

export function ProgressBar({
  label,
  percent,
  terminal,
}: {
  label: string;
  percent: number | null;
  terminal: boolean;
}) {
  return (
    <div
      aria-label={label}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={percent ?? (terminal ? 100 : 0)}
      className="h-1.5 overflow-hidden rounded-sm bg-muted"
      role="progressbar"
    >
      <div
        className={cn(
          "h-full rounded-sm transition-all duration-500 motion-reduce:transition-none",
          terminal ? "bg-success" : "bg-primary"
        )}
        style={{ width: `${Math.max(terminal && percent == null ? 100 : 0, percent ?? 0)}%` }}
      />
    </div>
  );
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="pixel-label mb-2 text-xs text-muted-foreground">{children}</p>;
}

export function DefRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: ReactNode | null;
  mono?: boolean;
}) {
  return (
    <div className="flex gap-4">
      <dt className="w-28 shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "min-w-0 flex-1 text-sm",
          mono && "font-mono text-xs",
          value == null && "italic text-muted-foreground/60"
        )}
      >
        {value ?? "not yet"}
      </dd>
    </div>
  );
}
