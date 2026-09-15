import {
  CartesianGrid,
  Line,
  LineChart,
  Tooltip as RechartsTooltip,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

export interface ScorePoint {
  iteration: number;
  /** 0–100, null while unscored. */
  best: number | null;
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ value: number | null }>;
  label?: number;
}

function ChartTooltip({ active, payload, label }: ChartTooltipProps) {
  const value = payload?.[0]?.value;
  if (!active || value == null) return null;
  return (
    <div className="rounded-md border border-border bg-background px-3 py-2 text-xs">
      <p className="font-medium text-foreground">
        {label === 0 ? "Baseline" : `Iteration ${label}`}
      </p>
      <p className="font-mono tabular-nums text-muted-foreground">{value.toFixed(1)}</p>
    </div>
  );
}

export function OptimizerScoreChart({
  points,
  baseline,
}: {
  points: ScorePoint[];
  baseline: number | null;
}) {
  return (
    <div
      aria-label="Line chart of best candidate score per iteration"
      className="cursor-default select-none [&_*]:outline-none"
      role="img"
    >
      <ResponsiveContainer height={220} width="100%">
        <LineChart data={points} margin={{ bottom: 4, left: -18, right: 12, top: 8 }}>
          <CartesianGrid className="stroke-border/60" strokeDasharray="3 3" />
          <XAxis
            allowDecimals={false}
            dataKey="iteration"
            tick={{ fontSize: 10 }}
            tickLine={false}
          />
          <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} tickLine={false} width={40} />
          <RechartsTooltip
            content={<ChartTooltip />}
            cursor={{ stroke: "var(--muted-foreground)", strokeOpacity: 0.3 }}
          />
          {baseline != null && (
            <ReferenceLine
              label={{ fill: "var(--muted-foreground)", fontSize: 10, value: "baseline" }}
              stroke="var(--chart-2)"
              strokeDasharray="4 4"
              y={baseline}
            />
          )}
          <Line
            connectNulls
            dataKey="best"
            dot={{ r: 3 }}
            isAnimationActive={false}
            name="Best score"
            stroke="var(--chart-1)"
            strokeWidth={1.5}
            type="monotone"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
