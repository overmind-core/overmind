// Hand-rolled SVG: recharts has no box or violin. The plot stretches
// horizontally (preserveAspectRatio="none"), so every stroke is non-scaling and
// all text lives in HTML. Scores are 0–1 fractions, rendered as 0–100%.

import { type ComponentType, type SVGProps, useState } from "react";

import {
  boxStats,
  histogramBins,
  kdeCurve,
} from "@/components/evaluations/score-distribution-math";
import { HelpTip } from "@/components/finetuning/finetuning-chrome";
import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import { TooltipProvider } from "@/components/ui/tooltip";
import { seriesColor } from "@/lib/colors";
import { humanizeKey } from "@/lib/label-case";
import { cn, scorePct } from "@/lib/utils";

export interface DistributionScore {
  name?: string | null;
  value?: number | null;
  variant?: string | null;
  scope?: string | null;
}

interface Series {
  key: string;
  label: string;
  color: string;
  values: number[];
}

type ViewMode = "scores" | "distribution" | "histogram" | "box" | "violin";

const VIEWS: Array<{
  id: ViewMode;
  label: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
}> = [
  { icon: Icon.list, id: "scores", label: "Scores" },
  { icon: Icon.analytics, id: "distribution", label: "Distribution" },
  { icon: Icon.chartBar, id: "histogram", label: "Histogram" },
  { icon: Icon.aspectRatio, id: "box", label: "Box & Whisker" },
  { icon: Icon.sortable, id: "violin", label: "Violin" },
];

// Lane views draw one row per series; overlay views share the whole frame.
const LANE_VIEWS: ReadonlySet<ViewMode> = new Set(["scores", "box", "violin"]);

function buildSeries(
  scores: DistributionScore[],
  variants: Array<{ id: string; label: string; order?: number }>,
  metrics: string[]
): Series[] {
  const ordered = [...variants].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const byMetric = new Map<string, Map<string, number[]>>();
  for (const s of scores) {
    if (s.scope === "dataset" || !s.name || !s.variant || s.value == null) continue;
    if (!byMetric.has(s.name)) byMetric.set(s.name, new Map());
    const byVariant = byMetric.get(s.name)!;
    if (!byVariant.has(s.variant)) byVariant.set(s.variant, []);
    byVariant.get(s.variant)!.push(s.value);
  }
  const multiModel = ordered.length > 1;
  const series: Series[] = [];
  for (const name of metrics) {
    const byVariant = byMetric.get(name);
    if (!byVariant) continue;
    for (const v of ordered) {
      const values = byVariant.get(v.id);
      if (!values?.length) continue;
      const displayName = humanizeKey(name);
      series.push({
        color: "", // assigned below so palette indices stay dense
        key: `${name}::${v.id}`,
        label: multiModel ? `${v.label} · ${displayName}` : displayName,
        values,
      });
    }
  }
  return series.map((s, i) => ({ ...s, color: seriesColor(i) }));
}

const seriesMean = (s: Series): number => s.values.reduce((a, b) => a + b, 0) / s.values.length;

// viewBox is 0..100 wide (score %) × plotH tall (real pixels).

const MIN_PLOT_H = 160; // px — overlay views need room even with few series
const LANE_H = 24; // px per series lane
const BIN_COUNT = 10;
const AXIS_TICKS = [0, 25, 50, 75, 100];
const KDE_TRIM = 0.02; // drop near-zero KDE tails so curves don't leave a hairline across the frame

const xPos = (fraction: number): number => fraction * 100;

/** KDE samples above the noise floor, as {x: 0–100, d: 0–1} points. */
const trimCurve = (curve: number[]): Array<{ x: number; d: number }> => {
  const start = curve.findIndex((d) => d > KDE_TRIM);
  if (start < 0) return [];
  let end = curve.length - 1;
  while (end > start && curve[end] <= KDE_TRIM) end -= 1;
  const step = 100 / (curve.length - 1);
  return curve.slice(start, end + 1).map((d, j) => ({ d, x: (start + j) * step }));
};

const GridLines = ({ horizontal, plotH }: { horizontal: boolean; plotH: number }) => (
  <>
    {/* x=0 is the axis border on the SVG itself, so start at 25 */}
    {AXIS_TICKS.slice(1).map((tick) => (
      <line
        key={tick}
        stroke="var(--border)"
        strokeOpacity={0.5}
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
        x1={tick}
        x2={tick}
        y1={0}
        y2={plotH}
      />
    ))}
    {horizontal &&
      [1, 2, 3, 4].map((k) => (
        <line
          key={k}
          stroke="var(--border)"
          strokeOpacity={0.5}
          strokeWidth={1}
          vectorEffect="non-scaling-stroke"
          x1={0}
          x2={100}
          y1={plotH - (k / 4) * plotH}
          y2={plotH - (k / 4) * plotH}
        />
      ))}
  </>
);

const MeanTick = ({ cy, halfH, mean }: { cy: number; halfH: number; mean: number }) => (
  <line
    stroke="var(--foreground)"
    strokeDasharray="2 2"
    strokeOpacity={0.6}
    strokeWidth={1}
    vectorEffect="non-scaling-stroke"
    x1={xPos(mean)}
    x2={xPos(mean)}
    y1={cy - halfH}
    y2={cy + halfH}
  />
);

const FlatTick = ({
  color,
  cy,
  halfH,
  value,
}: {
  color: string;
  cy: number;
  halfH: number;
  value: number;
}) => (
  <line
    stroke={color}
    strokeLinecap="round"
    strokeWidth={5}
    vectorEffect="non-scaling-stroke"
    x1={xPos(value)}
    x2={xPos(value)}
    y1={cy - halfH}
    y2={cy + halfH}
  />
);

const isZeroVariance = (values: number[]): boolean =>
  Math.max(...values) - Math.min(...values) < 0.005;

const ScoresView = ({ series, plotH }: { series: Series[]; plotH: number }) => {
  const laneH = plotH / series.length;
  return (
    <>
      {series.map((s, i) => {
        const cy = (i + 0.5) * laneH;
        const halfH = Math.max(3, Math.min(laneH * 0.3, 8));
        return (
          <rect
            fill={s.color}
            fillOpacity={0.85}
            height={halfH * 2}
            key={s.key}
            width={Math.max(0.5, xPos(seriesMean(s)))}
            x={0}
            y={cy - halfH}
          />
        );
      })}
    </>
  );
};

/** HTML, not `<text>`: the plot SVG stretches, so SVG text would distort. Long
 * bars carry the label inside the bar end so the frame never clips it. */
const ScoresLabels = ({ series }: { series: Series[] }) => (
  <div aria-hidden className="pointer-events-none absolute inset-0">
    {series.map((s, i) => {
      const pct = scorePct(seriesMean(s));
      const inside = pct > 85;
      return (
        <span
          className={cn(
            "absolute -translate-y-1/2 font-mono text-xs font-medium leading-none tabular-nums",
            inside ? "-translate-x-full pr-1.5 text-background" : "pl-1.5 text-muted-foreground"
          )}
          key={s.key}
          style={{ left: `${pct}%`, top: `${((i + 0.5) / series.length) * 100}%` }}
        >
          {pct}%
        </span>
      );
    })}
  </div>
);

const DistributionView = ({ series, plotH }: { series: Series[]; plotH: number }) => (
  <>
    {series.map((s) => {
      const points = trimCurve(kdeCurve(s.values));
      if (points.length < 2) return null;
      const yOf = (d: number) => plotH - d * (plotH - 10);
      const top = points.map((p) => `${p.x},${yOf(p.d)}`).join(" L");
      const first = points[0];
      const last = points[points.length - 1];
      return (
        <g key={s.key}>
          <path
            d={`M${first.x},${plotH} L${top} L${last.x},${plotH} Z`}
            fill={s.color}
            fillOpacity={0.15}
          />
          <path
            d={`M${top}`}
            fill="none"
            stroke={s.color}
            strokeLinejoin="round"
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
          />
        </g>
      );
    })}
  </>
);

const HistogramView = ({
  series,
  yMax,
  plotH,
}: {
  series: Series[];
  yMax: number;
  plotH: number;
}) => {
  const binned = series.map((s) => histogramBins(s.values, BIN_COUNT));
  const slot = 100 / BIN_COUNT;
  return (
    <>
      {Array.from({ length: BIN_COUNT }, (_, bin) => {
        let stackTop = plotH;
        return (
          <g key={bin}>
            {binned.map((bins, si) => {
              const count = bins[bin];
              if (count === 0) return null;
              const h = (count / yMax) * plotH;
              stackTop -= h;
              return (
                <rect
                  fill={series[si].color}
                  fillOpacity={0.85}
                  height={h}
                  key={series[si].key}
                  width={slot - 2}
                  x={bin * slot + 1}
                  y={stackTop}
                />
              );
            })}
          </g>
        );
      })}
    </>
  );
};

const BoxView = ({ series, plotH }: { series: Series[]; plotH: number }) => {
  const laneH = plotH / series.length;
  return (
    <>
      {series.map((s, i) => {
        const stats = boxStats(s.values);
        if (!stats) return null;
        const cy = (i + 0.5) * laneH;
        const boxHalf = Math.max(2, Math.min(laneH * 0.28, 9));
        // A zero-width box is invisible at the frame edge for all-100% data.
        if (isZeroVariance(s.values)) {
          return (
            <FlatTick
              color={s.color}
              cy={cy}
              halfH={boxHalf * 1.3}
              key={s.key}
              value={stats.median}
            />
          );
        }
        const capHalf = boxHalf * 0.6;
        const strokeProps = {
          stroke: s.color,
          strokeWidth: 1,
          vectorEffect: "non-scaling-stroke",
        } as const;
        return (
          <g key={s.key}>
            <line {...strokeProps} x1={xPos(stats.min)} x2={xPos(stats.q1)} y1={cy} y2={cy} />
            <line {...strokeProps} x1={xPos(stats.q3)} x2={xPos(stats.max)} y1={cy} y2={cy} />
            <line
              {...strokeProps}
              x1={xPos(stats.min)}
              x2={xPos(stats.min)}
              y1={cy - capHalf}
              y2={cy + capHalf}
            />
            <line
              {...strokeProps}
              x1={xPos(stats.max)}
              x2={xPos(stats.max)}
              y1={cy - capHalf}
              y2={cy + capHalf}
            />
            <rect
              {...strokeProps}
              fill={s.color}
              fillOpacity={0.25}
              height={boxHalf * 2}
              width={Math.max(0.4, xPos(stats.q3) - xPos(stats.q1))}
              x={xPos(stats.q1)}
              y={cy - boxHalf}
            />
            <line
              {...strokeProps}
              strokeWidth={2}
              x1={xPos(stats.median)}
              x2={xPos(stats.median)}
              y1={cy - boxHalf}
              y2={cy + boxHalf}
            />
            <MeanTick cy={cy} halfH={boxHalf * 1.3} mean={stats.mean} />
          </g>
        );
      })}
    </>
  );
};

const ViolinView = ({ series, plotH }: { series: Series[]; plotH: number }) => {
  const laneH = plotH / series.length;
  return (
    <>
      {series.map((s, i) => {
        const cy = (i + 0.5) * laneH;
        const amp = Math.max(3, Math.min(laneH / 2 - 3, 16));
        if (isZeroVariance(s.values)) {
          return (
            <FlatTick
              color={s.color}
              cy={cy}
              halfH={amp * 0.7}
              key={s.key}
              value={Math.min(...s.values)}
            />
          );
        }
        const points = trimCurve(kdeCurve(s.values));
        if (points.length < 2) return null;
        const top = points.map((p) => `${p.x},${cy - p.d * amp}`).join(" L");
        const bottom = [...points]
          .reverse()
          .map((p) => `${p.x},${cy + p.d * amp}`)
          .join(" L");
        return (
          <g key={s.key}>
            <path
              d={`M${points[0].x},${cy} L${top} L${bottom} Z`}
              fill={s.color}
              fillOpacity={0.35}
              stroke={s.color}
              strokeLinejoin="round"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
            <MeanTick cy={cy} halfH={amp} mean={seriesMean(s)} />
          </g>
        );
      })}
    </>
  );
};

export function ScoreDistributions({
  scores,
  variants,
  metrics,
}: {
  scores: DistributionScore[];
  variants: Array<{ id: string; label: string; order?: number }>;
  metrics: string[];
}) {
  const [view, setView] = useState<ViewMode>("scores");
  const series = buildSeries(scores, variants, metrics);
  if (series.length === 0) return null;

  // Overlay views share the lane-derived height so toggling doesn't jump.
  const plotH = Math.max(MIN_PLOT_H, series.length * LANE_H);
  const laneH = plotH / series.length;
  const laneAligned = LANE_VIEWS.has(view);

  // 4 equal intervals of a whole-number step, so tick labels land on integers.
  const binned = series.map((s) => histogramBins(s.values, BIN_COUNT));
  const peakCount = Math.max(
    1,
    ...Array.from({ length: BIN_COUNT }, (_, bin) =>
      binned.reduce((sum, bins) => sum + bins[bin], 0)
    )
  );
  const yStep = Math.max(1, Math.ceil(peakCount / 4));
  const yMax = yStep * 4;
  const yTicks = [4, 3, 2, 1, 0].map((k) => k * yStep);

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Icon.chartBar className="size-4 text-muted-foreground" />
        <h3 className="text-xs">Scores</h3>
        {/* Radix tooltips crash without a provider and there is no app-wide one. */}
        <TooltipProvider delayDuration={200}>
          <HelpTip
            label="Scores"
            text="Scores: each test's mean. Distribution: estimated density. Histogram: counts in 10% buckets. Box: min, quartiles, median. Violin: density per test. Dashed line: the test mean. Thick tick: every sample scored the same value."
          />
        </TooltipProvider>
        <div
          aria-label="Score view"
          className="ml-auto inline-flex items-center gap-0.5 rounded-sm border border-border/60 p-0.5"
          role="group"
        >
          {VIEWS.map((option) => (
            <button
              aria-pressed={view === option.id}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors",
                view === option.id
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              )}
              key={option.id}
              onClick={() => setView(option.id)}
              type="button"
            >
              <option.icon aria-hidden className="size-3.5" />
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-4 sm:flex-row">
        {/* In lane views each legend row takes the exact lane geometry, so the
            name sits centred on its own lane. */}
        <div
          className={cn("flex shrink-0 flex-col sm:w-48", !laneAligned && "gap-1.5")}
          style={laneAligned ? { height: plotH } : undefined}
        >
          {series.map((s) => (
            <div
              className="flex min-w-0 items-center gap-1.5"
              key={s.key}
              style={laneAligned ? { height: laneH } : undefined}
            >
              <span
                aria-hidden
                className="size-2 shrink-0 rounded-xs"
                style={{ background: s.color }}
              />
              <span
                className="min-w-0 flex-1 truncate font-mono text-xs text-foreground"
                title={`${s.label} · mean ${scorePct(seriesMean(s))}% · n=${s.values.length}`}
              >
                {s.label}
              </span>
            </div>
          ))}
        </div>

        <div className="flex min-w-0 flex-1 gap-1.5">
          <div className="flex shrink-0 gap-1" style={{ height: plotH }}>
            <span
              className={cn(
                "rotate-180 self-center text-xs text-muted-foreground/70 [writing-mode:vertical-rl]",
                view !== "histogram" && "invisible"
              )}
            >
              Count
            </span>
            <div className="flex w-5 flex-col items-end justify-between text-xs leading-none tabular-nums text-muted-foreground/70">
              {view === "histogram" && yTicks.map((tick) => <span key={tick}>{tick}</span>)}
            </div>
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="relative">
              <svg
                aria-hidden
                className="block w-full overflow-visible border-b border-l border-border/60"
                height={plotH}
                preserveAspectRatio="none"
                viewBox={`0 0 100 ${plotH}`}
              >
                <GridLines horizontal={view === "histogram"} plotH={plotH} />
                {view === "scores" ? (
                  <ScoresView plotH={plotH} series={series} />
                ) : view === "distribution" ? (
                  <DistributionView plotH={plotH} series={series} />
                ) : view === "histogram" ? (
                  <HistogramView plotH={plotH} series={series} yMax={yMax} />
                ) : view === "box" ? (
                  <BoxView plotH={plotH} series={series} />
                ) : (
                  <ViolinView plotH={plotH} series={series} />
                )}
              </svg>
              {view === "scores" && <ScoresLabels series={series} />}
            </div>
            <div className="flex justify-between text-xs tabular-nums text-muted-foreground/70">
              {AXIS_TICKS.map((tick) => (
                <span key={tick}>{tick}%</span>
              ))}
            </div>
            <div className="text-center text-xs text-muted-foreground/70">Score</div>
          </div>
        </div>
      </div>
    </Card>
  );
}
