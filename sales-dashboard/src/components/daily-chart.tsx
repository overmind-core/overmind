import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { formatDay } from "~/lib/format";

import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { SectionCard } from "@/components/ui/section-card";

export type ChartSeries = { key: string; label: string; color: string };

export function DailyChart({
  title,
  total,
  data,
  series,
  format,
  stacked = false,
}: {
  title: string;
  total: string;
  data: Record<string, number | string>[];
  series: ChartSeries[];
  format: (value: number) => string;
  stacked?: boolean;
}) {
  return (
    <SectionCard
      headerEnd={<span className="font-mono text-sm tabular-nums">{total}</span>}
      title={title}
    >
      <div className="h-48">
        <ResponsiveContainer height="100%" width="100%">
          <BarChart data={data} margin={{ bottom: 0, left: -12, right: 8, top: 8 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              axisLine={false}
              dataKey="day"
              minTickGap={24}
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              tickFormatter={formatDay}
              tickLine={false}
            />
            <YAxis
              allowDecimals={false}
              axisLine={false}
              tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
              tickFormatter={(value) =>
                Number(value).toLocaleString(undefined, {
                  maximumFractionDigits: 1,
                  notation: "compact",
                })
              }
              tickLine={false}
              width={48}
            />
            <Tooltip
              content={({ active, payload, label }) => (
                <GraphTooltip
                  active={active}
                  label={label == null ? undefined : formatDay(String(label))}
                  payload={payload}
                  valueFormatter={format}
                />
              )}
              cursor={{ fill: "var(--wash-raised)" }}
            />
            {series.map((item) => (
              <Bar
                dataKey={item.key}
                fill={item.color}
                isAnimationActive={false}
                key={item.key}
                name={item.label}
                stackId={stacked ? "stack" : undefined}
              />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </SectionCard>
  );
}
