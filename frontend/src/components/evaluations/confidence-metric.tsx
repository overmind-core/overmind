import { useState } from "react";

import { Card } from "@/components/ui/card";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { seriesColor } from "@/lib/colors";

export type ConfidenceEvidence = {
  reliability?: {
    decisions: number;
    confidence: number;
    reference_mass: number;
  }[];
  metrics: {
    accuracy?: { decisions: number };
    top1_reference_mass?: { decisions: number };
  };
};

export function summarizeConfidence(evidence: ConfidenceEvidence) {
  const bins = evidence.reliability;
  if (
    !bins?.length ||
    bins.some(
      (bin) =>
        !Number.isSafeInteger(bin.decisions) ||
        bin.decisions <= 0 ||
        [bin.confidence, bin.reference_mass].some(
          (value) => !Number.isFinite(value) || value < 0 || value > 1
        )
    )
  )
    return null;
  const count = bins.reduce((total, bin) => total + bin.decisions, 0);
  if (evidence.metrics.top1_reference_mass?.decisions !== count) return null;
  const confidence = bins.reduce((sum, bin) => sum + bin.confidence * bin.decisions, 0) / count;
  const observed = bins.reduce((sum, bin) => sum + bin.reference_mass * bin.decisions, 0) / count;
  // Absolute gaps within bands do not cancel when over- and underconfidence coexist.
  const error =
    bins.reduce(
      (sum, bin) => sum + Math.abs(bin.confidence - bin.reference_mass) * bin.decisions,
      0
    ) / count;
  return {
    confidence,
    count,
    error,
    observed,
    observedLabel: evidence.metrics.accuracy?.decisions === count ? "Correct" : "Reference support",
  };
}

export type ConfidenceSummary = NonNullable<ReturnType<typeof summarizeConfidence>>;
const percent = (value: number) => `${(value * 100).toFixed(1)}%`;

function ConfidenceBar({
  summary,
  name,
  label,
  color,
}: {
  summary: ConfidenceSummary;
  name: string;
  label?: string;
  color: string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const { confidence, observed, observedLabel, count, error } = summary;
  const gap = confidence - observed;
  const difference = `${(Math.abs(gap) * 100).toFixed(1)} pp`;
  const gapLabel =
    Math.abs(gap) < 0.0005
      ? "Averages match"
      : `${gap > 0 ? "Overconfident" : "Underconfident"} by ${difference}`;
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap justify-between gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>
          {label ? `${label} · ` : ""}
          {count.toLocaleString()} decisions
        </span>
        <span>Calibration error {(error * 100).toFixed(1)} pp</span>
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            aria-label={`${name}${label ? ` · ${label}` : ""}: ${observedLabel} ${percent(observed)}, claimed ${percent(confidence)}. ${gapLabel}. Calibration error ${(error * 100).toFixed(1)} pp.`}
            className="relative block h-5 w-full cursor-default bg-muted focus-visible:outline focus-visible:outline-ring"
            onBlur={() => setHover(null)}
            onFocus={() => setHover(confidence * 100)}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              setHover(
                Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100))
              );
            }}
            type="button"
          >
            <span
              aria-hidden
              className="absolute inset-y-0 left-0"
              style={{ backgroundColor: color, width: `${observed * 100}%` }}
            />
            <span
              aria-hidden
              className="absolute inset-y-0 bg-muted"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(135deg, currentColor 0 2px, transparent 2px 6px)",
                color,
                left: `${Math.min(confidence, observed) * 100}%`,
                width: `${Math.abs(gap) * 100}%`,
              }}
            />
            <span
              aria-hidden
              className="absolute -inset-y-1 w-0.5 -translate-x-1/2 bg-foreground"
              style={{ left: `${confidence * 100}%` }}
            />
            {hover != null && (
              <span
                aria-hidden
                className="absolute -inset-y-1 border-l border-dashed border-muted-foreground"
                style={{ left: `${hover}%` }}
              />
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent className="border-0 bg-transparent p-0">
          <GraphTooltip
            active
            label={`${name}${label ? ` · ${label}` : ""}`}
            payload={[
              { color, name: observedLabel, value: observed },
              { color: "var(--foreground)", name: "Claimed", value: confidence },
            ]}
            valueFormatter={percent}
          />
        </TooltipContent>
      </Tooltip>
      <div className="flex flex-wrap justify-between gap-2 text-xs tabular-nums">
        <span>
          {observedLabel} {percent(observed)}
        </span>
        <span>Claimed {percent(confidence)}</span>
      </div>
    </div>
  );
}

export function ConfidenceMetric({
  benchmark,
  rows,
}: {
  benchmark: string;
  rows: {
    key: string;
    name: string;
    colorIndex: number;
    original: ConfidenceSummary | null;
    adjusted: ConfidenceSummary | null;
  }[];
}) {
  const measured = rows.filter((row) => row.original || row.adjusted);
  if (!measured.length) return null;
  return (
    <Card aria-label={`${benchmark} · Confidence`} className="min-w-0">
      <div className="flex items-start gap-2 border-b border-border px-5 py-4">
        <Icon.analytics className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <h3 className="text-sm font-medium">Confidence</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Claimed confidence compared with observed results. Smaller gaps are better.
          </p>
        </div>
      </div>
      <div className="divide-y divide-border/70">
        {measured.map((row) => (
          <div className="space-y-3 px-4 py-4" key={row.key}>
            <p className="flex min-w-0 items-center gap-2 text-sm">
              <span
                className="size-2 shrink-0 rounded-xs"
                style={{ backgroundColor: seriesColor(row.colorIndex) }}
              />
              <span className="min-w-0 break-words">{row.name}</span>
            </p>
            {row.original && (
              <ConfidenceBar
                color={seriesColor(row.colorIndex)}
                label={row.adjusted ? "Original" : undefined}
                name={row.name}
                summary={row.original}
              />
            )}
            {row.adjusted && (
              <ConfidenceBar
                color={seriesColor(row.colorIndex)}
                label="Adjusted"
                name={row.name}
                summary={row.adjusted}
              />
            )}
          </div>
        ))}
      </div>
    </Card>
  );
}
