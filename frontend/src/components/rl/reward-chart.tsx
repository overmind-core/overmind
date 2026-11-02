import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { GraphTooltip } from "@/components/ui/graph-tooltip";

export interface CurvePoint {
  step: number;
  value: number | null;
}

export interface CurveSeries {
  id: string;
  name: string;
  color: string;
  points: CurvePoint[];
  /** Raw values draw faint behind their smoothed line. */
  faint?: boolean;
}

export interface CurveReference {
  value: number;
  label: string;
  color: string;
}

const AXIS_TICK = { fill: "var(--muted-foreground)", fontSize: 11 };

export function RewardChart({
  series,
  references = [],
  maxSteps,
  height = 240,
  percent = false,
}: {
  series: CurveSeries[];
  references?: CurveReference[];
  maxSteps: number;
  height?: number;
  percent?: boolean;
}) {
  const rows = new Map<number, Record<string, number | null>>();
  for (const s of series) {
    for (const p of s.points) {
      const row = rows.get(p.step) ?? { step: p.step };
      row[s.id] = p.value;
      rows.set(p.step, row);
    }
  }
  const data = [...rows.values()].sort((a, b) => (a.step as number) - (b.step as number));
  const format = (v: number) => (percent ? `${Math.round(v * 100)}%` : v.toFixed(2));

  return (
    <ResponsiveContainer height={height} width="100%">
      <LineChart data={data} margin={{ bottom: 4, left: 0, right: 12, top: 8 }}>
        <CartesianGrid className="stroke-border/60" strokeDasharray="3 3" vertical={false} />
        <XAxis
          axisLine={false}
          dataKey="step"
          domain={[1, Math.max(maxSteps, 2)]}
          tick={AXIS_TICK}
          tickLine={false}
          type="number"
        />
        <YAxis
          axisLine={false}
          domain={[0, 1]}
          tick={AXIS_TICK}
          tickFormatter={format}
          tickLine={false}
          ticks={[0, 0.25, 0.5, 0.75, 1]}
          width={40}
        />
        {references.map((ref) => (
          <ReferenceLine
            ifOverflow="extendDomain"
            key={ref.label}
            label={{
              fill: ref.color,
              fontSize: 11,
              position: "insideTopLeft",
              value: `${ref.label} ${format(ref.value)}`,
            }}
            stroke={ref.color}
            strokeDasharray="4 4"
            y={ref.value}
          />
        ))}
        <Tooltip
          content={({ active, payload, label }) => (
            <GraphTooltip
              active={active}
              label={`step ${label}`}
              payload={payload?.filter((entry) => !String(entry.dataKey).endsWith(":raw"))}
              valueFormatter={format}
            />
          )}
        />
        {series.map((s) => (
          <Line
            connectNulls
            dataKey={s.id}
            dot={false}
            isAnimationActive={false}
            key={s.id}
            name={s.name}
            stroke={s.color}
            strokeOpacity={s.faint ? 0.25 : 1}
            strokeWidth={s.faint ? 1 : 2}
            type="monotone"
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
