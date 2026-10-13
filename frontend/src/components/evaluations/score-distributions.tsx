// Hand-rolled SVG: recharts has no box or violin. The plot stretches
// horizontally (preserveAspectRatio="none"), so every stroke is non-scaling and
// all text lives in HTML. Scores are 0–1 fractions, rendered as 0–100%.

import {
  type ComponentType,
  type SVGProps,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import {
  boxStats,
  histogramBins,
  kdeCurve,
} from "@/components/evaluations/score-distribution-math";
import { HelpTip } from "@/components/finetuning/finetuning-chrome";
import { Card } from "@/components/ui/card";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import { TooltipProvider } from "@/components/ui/tooltip";
import { seriesColor } from "@/lib/colors";
import { humanizeKey } from "@/lib/label-case";
import { cn, plural, scorePct } from "@/lib/utils";

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
    for (const [index, v] of ordered.entries()) {
      const values = byVariant.get(v.id);
      if (!values?.length) continue;
      const displayName = humanizeKey(name);
      series.push({
        color: seriesColor(index),
        key: `${name}::${v.id}`,
        label: multiModel ? `${v.label} · ${displayName}` : displayName,
        values,
      });
    }
  }
  return series;
}

const seriesMean = (s: Series): number => s.values.reduce((a, b) => a + b, 0) / s.values.length;

// viewBox is 0..100 wide (score %) × plotH tall (real pixels).

const OVERLAY_PLOT_H = 180;
const LANE_H = 48;
const LANE_MARK_Y = 34;
const AXIS_H = 24;
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
    {AXIS_TICKS.slice(1, -1).map((tick) => (
      <line
        key={tick}
        stroke="var(--border)"
        strokeDasharray="3 3"
        strokeOpacity={0.7}
        strokeWidth={1}
        vectorEffect="non-scaling-stroke"
        x1={tick}
        x2={tick}
        y1={0}
        y2={plotH}
      />
    ))}
    {horizontal &&
      [1, 2, 3].map((k) => (
        <line
          key={k}
          stroke="var(--border)"
          strokeDasharray="3 3"
          strokeOpacity={0.7}
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

const ScoresView = ({ series }: { series: Series[] }) => {
  return (
    <>
      {series.map((s, i) => {
        const cy = i * LANE_H + LANE_MARK_Y;
        return (
          <rect
            fill={s.color}
            fillOpacity={0.85}
            height={12}
            key={s.key}
            width={Math.max(0, Math.min(100, xPos(seriesMean(s))))}
            x={0}
            y={cy - 6}
          />
        );
      })}
    </>
  );
};

function SeriesLabel({ series, showMean = false }: { series: Series; showMean?: boolean }) {
  return (
    <div className="flex min-w-0 items-center gap-1.5 text-xs leading-4">
      <span
        aria-hidden
        className="size-2 shrink-0 rounded-xs"
        style={{ background: series.color }}
      />
      <span
        className="min-w-0 flex-1 truncate"
        title={`${series.label} · mean ${scorePct(seriesMean(series))}% · n=${series.values.length}`}
      >
        {series.label}
      </span>
      {showMean && <span className="shrink-0 tabular-nums">{scorePct(seriesMean(series))}%</span>}
    </div>
  );
}

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
                  width={slot - 0.6}
                  x={bin * slot + 0.3}
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

const BoxView = ({ series }: { series: Series[] }) => {
  return (
    <>
      {series.map((s, i) => {
        const stats = boxStats(s.values);
        if (!stats) return null;
        const cy = i * LANE_H + LANE_MARK_Y;
        const boxHalf = 8;
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

const ViolinView = ({ series }: { series: Series[] }) => {
  return (
    <>
      {series.map((s, i) => {
        const cy = i * LANE_H + LANE_MARK_Y;
        const amp = 10;
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

function ScorePlotHover({ series, view }: { series: Series[]; view: ViewMode }) {
  const tooltipId = useId();
  const [hover, setHover] = useState<{
    index: number;
    fraction: number;
    x: number;
    y: number;
  } | null>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const summaries = useMemo(() => series.map((s) => boxStats(s.values)!), [series]);
  const bins = useMemo(() => series.map((s) => histogramBins(s.values, BIN_COUNT)), [series]);
  const curves = useMemo(() => series.map((s) => kdeCurve(s.values)), [series]);
  const laneAligned = LANE_VIEWS.has(view);
  const bin = Math.min(BIN_COUNT - 1, Math.floor((hover?.fraction ?? 0) * BIN_COUNT));
  const densityIndex = Math.round((hover?.fraction ?? 0) * (curves[0].length - 1));
  const selectedIndex = Math.min(series.length - 1, hover?.index ?? 0);
  const selected = series[selectedIndex];
  const summary = summaries[selectedIndex];
  const percentage = (value: number) => `${scorePct(value)}%`;
  let label = `Mean score · ${plural(selected.values.length, "sample")}`;
  let payload = [{ color: selected.color, name: selected.label, value: summary.mean }];
  let format = percentage;
  if (view === "histogram") {
    label = `Score ${bin * 10}%–${(bin + 1) * 10}%`;
    payload = series.map((s, index) => ({
      color: s.color,
      name: s.label,
      value: bins[index][bin],
    }));
    format = (value) => `${value} ${value === 1 ? "sample" : "samples"}`;
  } else if (view === "distribution") {
    label = `Relative density · score ${scorePct(hover?.fraction ?? 0)}%`;
    payload = series.map((s, index) => ({
      color: s.color,
      name: s.label,
      value: curves[index][densityIndex],
    }));
    format = (value) => value.toFixed(3);
  } else if (view === "box" || view === "violin") {
    label = `${selected.label} · ${plural(selected.values.length, "sample")}`;
    payload = [
      { color: selected.color, name: "Minimum", value: summary.min },
      { color: selected.color, name: "25th percentile", value: summary.q1 },
      { color: selected.color, name: "Median", value: summary.median },
      { color: selected.color, name: "75th percentile", value: summary.q3 },
      { color: selected.color, name: "Maximum", value: summary.max },
      { color: selected.color, name: "Mean", value: summary.mean },
    ];
  }

  useLayoutEffect(() => {
    const tooltip = tooltipRef.current;
    if (!tooltip || !hover) return;
    const { width, height } = tooltip.getBoundingClientRect();
    tooltip.style.left = `${Math.max(12, Math.min(hover.x + 12, window.innerWidth - width - 12))}px`;
    const below = hover.y + 12;
    tooltip.style.top = `${Math.max(12, below + height <= window.innerHeight - 12 ? below : hover.y - height - 12)}px`;
  }, [hover]);

  return (
    <>
      <div
        aria-describedby={hover ? tooltipId : undefined}
        aria-description={
          laneAligned
            ? "Use Up and Down to inspect each series."
            : "Use Left and Right to inspect scores."
        }
        aria-label="Evaluation score chart"
        aria-roledescription="chart"
        className="absolute inset-0 outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        onBlur={() => setHover(null)}
        onFocus={(event) => {
          if (!event.currentTarget.matches(":focus-visible")) return;
          const rect = event.currentTarget.getBoundingClientRect();
          setHover({ fraction: 0, index: 0, x: rect.left + 12, y: rect.top + LANE_MARK_Y });
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setHover(null);
            return;
          }
          const keys = laneAligned ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
          if (![...keys, "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const rect = event.currentTarget.getBoundingClientRect();
          const direction = event.key === keys[0] ? -1 : 1;
          const last = laneAligned ? series.length - 1 : view === "histogram" ? BIN_COUNT - 1 : 60;
          const current = laneAligned
            ? (hover?.index ?? 0)
            : view === "histogram"
              ? bin
              : densityIndex;
          const next =
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? last
                : Math.max(0, Math.min(last, current + direction));
          const fraction = laneAligned
            ? summaries[next].mean
            : view === "histogram"
              ? (next + 0.5) / BIN_COUNT
              : next / last;
          setHover({
            fraction,
            index: laneAligned ? next : 0,
            x: rect.left + fraction * rect.width,
            y: rect.top + (laneAligned ? next * LANE_H + LANE_MARK_Y : rect.height / 2),
          });
        }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          const y = event.clientY - rect.top;
          if (laneAligned && y >= series.length * LANE_H) {
            setHover(null);
            return;
          }
          setHover({
            fraction: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
            index: laneAligned
              ? Math.min(series.length - 1, Math.max(0, Math.floor(y / LANE_H)))
              : 0,
            x: event.clientX,
            y: event.clientY,
          });
        }}
        role="application"
        // biome-ignore lint/a11y/noNoninteractiveTabindex: The chart implements arrow-key navigation, matching Recharts' focusable application role.
        tabIndex={0}
      />
      {hover && (
        <div
          aria-hidden
          className="pointer-events-none absolute inset-y-0 border-l border-dashed border-muted-foreground/70"
          data-slot="score-cursor"
          style={{ left: `${hover.fraction * 100}%` }}
        />
      )}
      {hover &&
        createPortal(
          <div
            className="pointer-events-none fixed z-50 w-80 max-w-[calc(100vw-1.5rem)] [&>[role=tooltip]]:w-full [&>[role=tooltip]]:min-w-0"
            id={tooltipId}
            ref={tooltipRef}
          >
            <GraphTooltip active label={label} payload={payload} valueFormatter={format} />
          </div>,
          document.body
        )}
    </>
  );
}

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

  const laneAligned = LANE_VIEWS.has(view);
  const plotH = laneAligned ? series.length * LANE_H + AXIS_H : OVERLAY_PLOT_H;

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

  return (
    <Card className="min-w-0">
      <div className="flex min-h-11 flex-wrap items-center gap-2 border-b border-border/70 px-3 py-2.5">
        <Icon.chartBar className="size-4 text-muted-foreground" />
        <h3 className="text-sm leading-none">Scores</h3>
        {/* Radix tooltips crash without a provider and there is no app-wide one. */}
        <TooltipProvider delayDuration={200}>
          <HelpTip
            label="Scores"
            text="Scores: each test's mean. Distribution: estimated density. Histogram: counts in 10% buckets. Box: min, quartiles, median. Violin: density per test. Dashed line: the test mean. Thick tick: every sample scored the same value."
          />
        </TooltipProvider>
        <div
          aria-label="Score view"
          className="ml-auto flex max-w-full flex-wrap items-center gap-0.5 rounded-sm border border-border/60 p-0.5"
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

      {!laneAligned && (
        <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-b border-border/70 px-3 py-2">
          {series.map((s) => (
            <SeriesLabel key={s.key} series={s} />
          ))}
        </div>
      )}
      <div className="relative">
        <svg
          aria-hidden
          className="block w-full overflow-visible"
          data-slot="score-plot"
          height={plotH}
          preserveAspectRatio="none"
          viewBox={`0 0 100 ${plotH}`}
        >
          <GridLines horizontal={view === "histogram"} plotH={plotH} />
          {view === "scores" ? (
            <ScoresView series={series} />
          ) : view === "distribution" ? (
            <DistributionView plotH={plotH} series={series} />
          ) : view === "histogram" ? (
            <HistogramView plotH={plotH} series={series} yMax={yMax} />
          ) : view === "box" ? (
            <BoxView series={series} />
          ) : (
            <ViolinView series={series} />
          )}
        </svg>
        {laneAligned ? (
          series.map((s, index) => (
            <div className="absolute inset-x-3" key={s.key} style={{ top: index * LANE_H + 6 }}>
              <SeriesLabel series={s} showMean={view === "scores"} />
            </div>
          ))
        ) : (
          <span className="pointer-events-none absolute top-2 left-3 bg-card/90 px-1 text-xs tabular-nums text-muted-foreground">
            {view === "histogram" ? `${yMax} samples` : "Relative density"}
          </span>
        )}
        <div className="pointer-events-none absolute inset-x-3 bottom-1.5 flex justify-between text-xs tabular-nums text-muted-foreground">
          <span className="bg-card/90 px-1">0%</span>
          <span className="bg-card/90 px-1">100%</span>
        </div>
        <ScorePlotHover key={view} series={series} view={view} />
      </div>
    </Card>
  );
}
