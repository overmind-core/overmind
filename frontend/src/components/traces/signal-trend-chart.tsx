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

import { dayLabel } from "@/components/traces/signal-trend-label";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { humanizeKey } from "@/lib/label-case";
import { cn } from "@/lib/utils";
import type { SignalTrend } from "@/openapi";

const SHOWN = 5;
const OTHER = "other";
const COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

export default function SignalTrendChart({
  trend,
  signal,
  day,
  onSelect,
}: {
  trend: SignalTrend;
  signal: string | undefined;
  day: string | undefined;
  onSelect: (selection: { signal?: string; day?: string }) => void;
}) {
  const top = trend.series.slice(0, SHOWN);
  const selected = trend.series.find((series) => series.signalType === signal);
  if (selected && !top.includes(selected)) top.splice(SHOWN - 1, 1, selected);
  const rest = trend.series.filter((series) => !top.includes(series));
  const keys = [
    ...top.map((series, index) => ({
      color: COLORS[index],
      key: series.signalType,
      total: series.total,
    })),
    ...(rest.length
      ? [
          {
            color: "var(--cat-6)",
            key: OTHER,
            total: rest.reduce((sum, series) => sum + series.total, 0),
          },
        ]
      : []),
  ];
  const data = trend.days.map((date, index) => ({
    day: date,
    ...Object.fromEntries(top.map((series) => [series.signalType, series.counts[index]])),
    ...(rest.length
      ? { [OTHER]: rest.reduce((sum, series) => sum + (series.counts[index] ?? 0), 0) }
      : {}),
  }));
  const name = (key: string) => (key === OTHER ? "Other" : humanizeKey(key));
  const dimmed = (key: string, date: string) =>
    (!!day && date !== day) || (!!signal && key !== signal);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        {keys.map(({ key, color, total }) => {
          const active = key !== OTHER && key === signal;
          return (
            <button
              aria-pressed={active}
              className={cn(
                "inline-flex h-6 cursor-pointer items-center gap-1.5 rounded-sm border px-2 text-xs transition-colors disabled:cursor-default",
                active
                  ? "border-foreground/60 bg-wash-raised text-foreground"
                  : "border-border text-muted-foreground hover:text-foreground",
                signal && !active && "opacity-60"
              )}
              disabled={key === OTHER}
              key={key}
              onClick={() => onSelect({ day, signal: active ? undefined : key })}
              type="button"
            >
              <span
                aria-hidden="true"
                className="size-2 rounded-xs"
                style={{ backgroundColor: color }}
              />
              {name(key)}
              <span className="tabular-nums text-foreground">{total}</span>
            </button>
          );
        })}
      </div>
      <div aria-label="Signal events per day" className="h-40" role="img">
        <ResponsiveContainer height="100%" width="100%">
          <BarChart data={data} margin={{ bottom: 0, left: 0, right: 8, top: 4 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis
              axisLine={false}
              dataKey="day"
              minTickGap={24}
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
                  nameFormatter={name}
                  payload={payload?.filter((entry) => Number(entry.value) > 0)}
                  valueFormatter={(value) => String(value)}
                />
              )}
              cursor={{ fill: "var(--wash-raised)" }}
            />
            {keys.map(({ key, color }) => (
              <Bar
                className="cursor-pointer"
                dataKey={key}
                fill={color}
                isAnimationActive={false}
                key={key}
                maxBarSize={28}
                name={key}
                onClick={(entry) => {
                  const date = (entry.payload as { day: string }).day;
                  const next = key === OTHER ? undefined : key;
                  onSelect(date === day && next === signal ? {} : { day: date, signal: next });
                }}
                stackId="signals"
              >
                {data.map((row) => (
                  <Cell fillOpacity={dimmed(key, row.day) ? 0.3 : 1} key={row.day} />
                ))}
              </Bar>
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
