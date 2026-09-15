import { type ReactNode, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { DeltaChip } from "@/components/ui/delta-chip";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { scoreChipClass } from "@/lib/colors";
import { resolveStatus, statusBadgeClassName } from "@/lib/job-status";
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
  solidProgress = false,
  className,
}: {
  status: string | null | undefined;
  fallback: string;
  solidProgress?: boolean;
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
    <Badge
      className={cn(statusBadgeClassName(cfg, solidProgress), className)}
      // Tables take the traces-table chip metrics; the solid face keeps h-7.
      size={solidProgress ? "default" : "chip"}
      variant={cfg.variant}
    >
      {icon}
      {cfg.label}
    </Badge>
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

export function buildConfigChips(
  hp: Record<string, unknown>,
  _job: { provider?: string | null }
): string[] {
  const chips: string[] = [];
  if (hp.context_length != null) chips.push(`ctx ${hp.context_length}`);
  if (hp.batch_size != null) chips.push(`bs ${hp.batch_size}`);
  if (hp.n_epochs != null) chips.push(`${hp.n_epochs} ep`);
  if (typeof hp.learning_rate === "number") {
    chips.push(`lr ${hp.learning_rate.toExponential(1)}`);
  } else if (hp.learning_rate != null && hp.learning_rate !== "") {
    chips.push(`lr ${hp.learning_rate}`);
  }
  const loraR = hp.lora_r ?? hp.lora_rank;
  if (loraR != null) chips.push(`lora r${loraR}`);
  if (hp.lora_alpha != null) chips.push(`α ${hp.lora_alpha}`);
  if (hp.packing === true) chips.push("Packed");
  return chips;
}

export function buildConfigItems(
  hp: Record<string, unknown>,
  _job: { provider?: string | null }
): Array<{ label: string; value: string }> {
  const items: Array<{ label: string; value: string }> = [];
  const add = (label: string, value: unknown) => {
    if (value == null || value === "") return;
    items.push({ label, value: String(value) });
  };
  add("Context length", hp.context_length);
  add("Batch size", hp.batch_size);
  add("Epochs", hp.n_epochs);
  add(
    "Learning rate",
    typeof hp.learning_rate === "number" ? hp.learning_rate.toExponential(1) : hp.learning_rate
  );
  add("LoRA rank", hp.lora_r ?? hp.lora_rank);
  add("LoRA alpha", hp.lora_alpha);
  if (typeof hp.packing === "boolean") add("Packing", hp.packing ? "on" : "off");
  return items;
}

function ConfigParamChip({ label }: { label: string }) {
  return (
    <Badge
      className="h-6 shrink-0 gap-1 px-1.5 font-mono leading-none tabular-nums"
      variant="neutral"
    >
      {label}
    </Badge>
  );
}

export function RunConfigDisclosure({
  chips,
  items,
}: {
  chips: string[];
  items: Array<{ label: string; value: string }>;
}) {
  const [expanded, setExpanded] = useState(false);
  if (chips.length === 0 && items.length === 0) return null;
  return (
    <Card className="flex flex-col p-0">
      <button
        aria-expanded={expanded}
        className="flex min-h-11 items-center gap-2 rounded-md px-4 py-2.5 text-left transition-colors hover:bg-wash-subtle focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => setExpanded((v) => !v)}
        type="button"
      >
        <Icon.settings className="size-4 shrink-0 text-muted-foreground" />
        <span className="shrink-0 text-xs font-medium leading-none">Run configuration</span>
        {!expanded && chips.length > 0 && (
          <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5 overflow-hidden">
            {chips.map((c) => (
              <ConfigParamChip key={c} label={c} />
            ))}
          </span>
        )}
        <Icon.chevronDown
          className={cn(
            "ml-auto size-4 shrink-0 text-muted-foreground transition-transform",
            expanded && "rotate-180"
          )}
        />
      </button>
      {expanded && items.length > 0 && (
        <dl className="grid grid-cols-2 gap-x-6 gap-y-3 border-t border-border/70 px-4 py-3 sm:grid-cols-3 lg:grid-cols-4">
          {items.map((it) => (
            <div className="flex flex-col gap-1" key={it.label}>
              <dt className="text-xs leading-none text-muted-foreground">{it.label}</dt>
              <dd className="font-mono text-xs font-medium leading-none tabular-nums">
                {it.value}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}
