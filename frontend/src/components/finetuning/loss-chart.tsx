import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { recordedPoints } from "@/components/finetuning/metric-summary";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import type { LossCurveData } from "@/hooks/use-finetuning";
import { seriesColor } from "@/lib/colors";

interface LossChartProps {
  modelName: string;
  data: LossCurveData;
  height?: number;
}

interface ChartPoint {
  epoch: number;
  train?: number | null;
  valid?: number | null;
}

function buildChartData(data: LossCurveData): ChartPoint[] {
  const length = data.epochs?.length ?? data.steps?.length ?? 0;
  if (length === 0) return [];
  const epochs = data.epochs?.length ? data.epochs : (data.steps ?? []);
  const valid = data.valid_loss?.length ? data.valid_loss : (data.eval_loss ?? []);

  return Array.from({ length }, (_, i) => ({
    epoch: epochs[i] ?? i + 1,
    train: data.train_loss?.[i] ?? null,
    valid: valid[i] ?? null,
  })).filter((p) => p.train != null || p.valid != null);
}

const MAX_RENDERED_POINTS = 500;

/** Round up to a clean 1/2/5×10^k increment. */
function niceIncrement(raw: number): number {
  if (raw <= 1) return 1;
  const pow = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5]) {
    if (m * pow >= raw) return m * pow;
  }
  return 10 * pow;
}

export function strideFor(n: number, maxPoints = MAX_RENDERED_POINTS): number {
  return n <= maxPoints ? 1 : niceIncrement(Math.ceil(n / maxPoints));
}

/** Mean per window, not nth-point sampling, so spikes survive; keyed on step,
    not index, so buckets align across experiments in overlay charts. */
export function bucketPoints(points: MetricPoint[], stride: number): MetricPoint[] {
  if (stride <= 1) return points;
  const buckets = new Map<number, { sum: number; n: number; step: number }>();
  for (const p of points) {
    const key = Math.ceil(p.step / stride);
    const b = buckets.get(key);
    if (b) {
      b.sum += p.value;
      b.n += 1;
      b.step = Math.max(b.step, p.step);
    } else {
      buckets.set(key, { n: 1, step: p.step, sum: p.value });
    }
  }
  return [...buckets.values()]
    .sort((a, b) => a.step - b.step)
    .map((b) => ({ step: b.step, value: b.sum / b.n }));
}

function bucketChartPoints(points: ChartPoint[], stride: number): ChartPoint[] {
  if (stride <= 1) return points;
  const buckets = new Map<
    number,
    { step: number; tSum: number; tN: number; vSum: number; vN: number }
  >();
  for (const p of points) {
    const key = Math.ceil(p.epoch / stride);
    const b = buckets.get(key) ?? { step: p.epoch, tN: 0, tSum: 0, vN: 0, vSum: 0 };
    b.step = Math.max(b.step, p.epoch);
    if (p.train != null) {
      b.tSum += p.train;
      b.tN += 1;
    }
    if (p.valid != null) {
      b.vSum += p.valid;
      b.vN += 1;
    }
    buckets.set(key, b);
  }
  return [...buckets.values()]
    .sort((a, b) => a.step - b.step)
    .map((b) => ({
      epoch: b.step,
      train: b.tN ? b.tSum / b.tN : null,
      valid: b.vN ? b.vSum / b.vN : null,
    }));
}

/** Numeric axis with clean tick increments — recharts otherwise labels every point. */
function stepAxisProps(steps: number[]) {
  if (steps.length < 2) return {};
  let min = steps[0];
  let max = steps[0];
  for (const s of steps) {
    if (s < min) min = s;
    if (s > max) max = s;
  }
  if (max <= min) return {};
  const inc = niceIncrement(Math.max(1, Math.ceil((max - min) / 6)));
  const ticks: number[] = [];
  for (let t = Math.ceil(min / inc) * inc; t <= max; t += inc) ticks.push(t);
  if (ticks.length === 0) ticks.push(max);
  return { domain: [min, max] as [number, number], ticks, type: "number" as const };
}

function formatTick(v: number): string {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a < 0.01 || a >= 100_000) return v.toExponential(1);
  return String(Number(v.toPrecision(3)));
}

const TRAIN_COLOR = seriesColor(0);
const VALID_COLOR = seriesColor(1);

export function LossChart({ data, height = 200, modelName }: LossChartProps) {
  const raw = buildChartData(data);
  const points = bucketChartPoints(raw, strideFor(raw.length));
  const validSeries = data.valid_loss?.length ? data.valid_loss : (data.eval_loss ?? []);
  const hasValid = validSeries.some((v) => v != null);

  if (points.length === 0) {
    return null;
  }

  return (
    <ResponsiveContainer height={height} width="100%">
      <LineChart data={points} margin={{ bottom: 0, left: -8, right: 4, top: 4 }}>
        <CartesianGrid className="stroke-border/50" strokeDasharray="3 3" />
        <XAxis
          dataKey="epoch"
          height={20}
          tick={{ fontSize: 10 }}
          tickLine={false}
          {...stepAxisProps(points.map((p) => p.epoch))}
        />
        <YAxis
          domain={["auto", "auto"]}
          tick={{ fontSize: 10 }}
          tickFormatter={formatTick}
          tickLine={false}
          width={48}
        />
        <Tooltip
          content={({ active, payload, label }) => (
            <GraphTooltip
              active={active}
              label={`${data.epochs?.length ? "Epoch" : "Step"} ${label}`}
              nameFormatter={(name) => `${modelName} · ${name}`}
              payload={payload}
            />
          )}
        />
        {hasValid && (
          <Legend
            formatter={(value) => <span className="text-xs text-muted-foreground">{value}</span>}
            iconSize={8}
            iconType="circle"
            wrapperStyle={{ paddingTop: 4 }}
          />
        )}
        <Line
          connectNulls
          dataKey="train"
          dot={points.length <= 10}
          isAnimationActive={false}
          name="train loss"
          stroke={TRAIN_COLOR}
          strokeWidth={1.5}
          type="monotone"
        />
        {hasValid && (
          <Line
            connectNulls
            dataKey="valid"
            dot={points.length <= 10}
            isAnimationActive={false}
            name="eval loss"
            stroke={VALID_COLOR}
            strokeDasharray="4 2"
            strokeWidth={1.5}
            type="monotone"
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}

export interface MetricPoint {
  step: number;
  value: number;
}

export interface MetricSeries {
  id: string;
  name: string;
  /** Stroke colour; CSS vars work, e.g. `var(--chart-1)`. */
  color: string;
  points: MetricPoint[];
}

interface MultiSeriesChartProps {
  series: MetricSeries[];
  height?: number;
  valueFormatter?: (v: number) => string;
  xLabel?: string;
}

/** No legend: the colour key lives on the experiment chips in the page header. */
export function MultiSeriesChart({
  series,
  height = 180,
  valueFormatter = (v) => v.toFixed(4),
  xLabel = "step",
}: MultiSeriesChartProps) {
  const drawn = series
    .map((item) => ({ ...item, points: recordedPoints(item.points) }))
    .filter((item) => item.points.length > 0);
  if (drawn.length === 0) return null;

  // One shared stride keeps experiment buckets on common steps.
  const stride = strideFor(Math.max(...drawn.map((s) => s.points.length)));
  const rowsByStep = new Map<number, Record<string, number | null>>();
  let low = Number.POSITIVE_INFINITY;
  let high = Number.NEGATIVE_INFINITY;
  for (const s of drawn) {
    for (const p of bucketPoints(s.points, stride)) {
      const row = rowsByStep.get(p.step) ?? { step: p.step };
      row[s.id] = p.value;
      rowsByStep.set(p.step, row);
      low = Math.min(low, p.value);
      high = Math.max(high, p.value);
    }
  }
  const rows = [...rowsByStep.values()].sort((a, b) => (a.step as number) - (b.step as number));
  const firstStep = rows[0].step as number;
  const lastStep = rows[rows.length - 1].step as number;
  const lowerBound = Math.min(0, low);
  const upperBound = low === 0 && high === 0 ? 1 : Math.max(0, high);
  const labelClass =
    "pointer-events-none absolute left-3 bg-card/90 px-1 font-mono text-xs tabular-nums text-muted-foreground";

  return (
    <div className="relative min-w-0">
      <ResponsiveContainer height={height} width="100%">
        <LineChart data={rows} margin={{ bottom: 0, left: 0, right: 0, top: 0 }}>
          <CartesianGrid
            className="stroke-border/60"
            strokeDasharray="3 3"
            syncWithTicks
            vertical={firstStep !== lastStep}
          />
          <XAxis
            {...stepAxisProps(rows.map((row) => row.step as number))}
            dataKey="step"
            domain={firstStep === lastStep ? [firstStep - 1, lastStep + 1] : [firstStep, lastStep]}
            hide
            interval={0}
            type="number"
          />
          <YAxis domain={[lowerBound, upperBound]} hide interval={0} tickCount={5} />
          <Tooltip
            content={({ active, payload, label }) => (
              <GraphTooltip
                active={active}
                label={`${xLabel} ${label}`}
                payload={payload}
                valueFormatter={valueFormatter}
              />
            )}
          />
          {drawn.map((s) => (
            <Line
              connectNulls
              dataKey={s.id}
              dot={s.points.length === 1 ? { fill: s.color, r: 3, strokeWidth: 0 } : false}
              isAnimationActive={false}
              key={s.id}
              name={s.name}
              stroke={s.color}
              strokeWidth={1.5}
              type="linear"
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
      <span className={`${labelClass} top-2`}>{formatTick(upperBound)}</span>
      {lowerBound < 0 && <span className={`${labelClass} bottom-8`}>{formatTick(lowerBound)}</span>}
      <div className="pointer-events-none absolute inset-x-3 bottom-2 flex justify-between font-mono text-xs tabular-nums text-muted-foreground">
        <span className="bg-card/90 px-1">
          {xLabel} {firstStep.toLocaleString()}
        </span>
        {lastStep !== firstStep && (
          <span className="bg-card/90 px-1">
            {xLabel} {lastStep.toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}

interface MetricSeriesChartProps {
  data: MetricPoint[];
  color: string;
  name: string;
  height?: number;
  valueFormatter?: (v: number) => string;
  xLabel?: string;
}

export function MetricSeriesChart({
  data,
  color,
  name,
  height = 180,
  valueFormatter = (v) => v.toFixed(4),
  xLabel = "step",
}: MetricSeriesChartProps) {
  return (
    <MultiSeriesChart
      height={height}
      series={[{ color, id: "value", name, points: data }]}
      valueFormatter={valueFormatter}
      xLabel={xLabel}
    />
  );
}
