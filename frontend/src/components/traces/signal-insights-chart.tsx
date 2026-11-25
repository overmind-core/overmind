import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { GraphTooltip } from "@/components/ui/graph-tooltip";
import type { SignalInsight } from "@/openapi";

export type ChartSeries = { key: string; label: string; color: string; insight?: SignalInsight };

function dayLabel(day: string) {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });
}

export default function SignalInsightsChart({
  days,
  series,
  focused,
  onFocus,
}: {
  days: string[];
  series: (ChartSeries & { daily: number[] })[];
  focused: string | null;
  onFocus: (item: ChartSeries) => void;
}) {
  const data = days.map((day, index) => ({
    day,
    ...Object.fromEntries(series.map((item) => [item.key, item.daily[index] ?? 0])),
  }));
  const labels = Object.fromEntries(series.map((item) => [item.key, item.label]));

  return (
    <div aria-label="Signal events per day by issue" className="h-44" role="img">
      <ResponsiveContainer height="100%" width="100%">
        <BarChart data={data} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            axisLine={false}
            dataKey="day"
            minTickGap={16}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            tickFormatter={dayLabel}
            tickLine={false}
          />
          <YAxis
            allowDecimals={false}
            axisLine={false}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            tickLine={false}
            width={32}
          />
          <Tooltip
            content={({ active, payload, label }) => (
              <GraphTooltip
                active={active}
                label={label != null ? dayLabel(String(label)) : undefined}
                nameFormatter={(key) => labels[key] ?? key}
                payload={payload?.filter((entry) => Number(entry.value) > 0)}
                valueFormatter={(value) => `${value} ${value === 1 ? "event" : "events"}`}
              />
            )}
            cursor={{ fill: "var(--wash-raised)" }}
          />
          {series.map((item) => (
            <Bar
              className={item.insight ? "cursor-pointer" : undefined}
              dataKey={item.key}
              fill={item.color}
              isAnimationActive={false}
              key={item.key}
              maxBarSize={36}
              name={item.key}
              onClick={() => item.insight && onFocus(item)}
              stackId="insights"
            >
              {data.map((row) => (
                <Cell fillOpacity={focused && focused !== item.key ? 0.3 : 1} key={row.day} />
              ))}
            </Bar>
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
