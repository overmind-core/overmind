import { memo } from "react";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card } from "@/components/ui/card";

const colors = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

type ChartProps = {
  id: string;
  type: "bar" | "horizontal-bar" | "line" | "area" | "pie" | "scatter";
  title?: string;
  description?: string;
  data: Record<string, unknown>[];
  xKey: string;
  series: { key: string; label: string }[];
  stacked?: boolean;
};

// Adapted from assistant-ui/tool-ui's Chart; provenance and license live beside this file.
export const Chart = memo(function Chart({
  id,
  type,
  title,
  description,
  data,
  xKey,
  series,
  stacked,
}: ChartProps) {
  const horizontal = type === "horizontal-bar";
  const ChartComponent = type === "line" ? LineChart : type === "area" ? AreaChart : BarChart;
  const color = (index: number) => colors[index % colors.length];
  const tooltip = (
    <Tooltip
      contentStyle={{
        background: "var(--popover)",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-sm)",
        fontSize: 12,
      }}
      cursor={{ fill: "var(--wash-raised)", stroke: "var(--border)" }}
      itemStyle={{ color: "var(--foreground)", padding: 0 }}
      labelStyle={{ color: "var(--muted-foreground)", marginBottom: 4 }}
    />
  );
  const legend =
    series.length > 1 ? (
      <Legend iconSize={6} iconType="circle" wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
    ) : null;
  return (
    <Card className="min-w-0 overflow-hidden bg-card" data-slot="chart" data-tool-ui-id={id}>
      <div className="px-4 pt-3 pb-2">
        {title && <h3 className="text-sm font-medium">{title}</h3>}
        {description && <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>}
      </div>
      <div className="px-3 pb-3">
        <div
          className="w-full text-xs"
          style={{ height: horizontal ? Math.min(300, Math.max(180, data.length * 26 + 28)) : 200 }}
        >
          <ResponsiveContainer height="100%" minWidth={0} width="100%">
            {type === "pie" ? (
              <PieChart accessibilityLayer>
                {tooltip}
                <Pie
                  data={data}
                  dataKey={series[0].key}
                  innerRadius="48%"
                  isAnimationActive={false}
                  nameKey={xKey}
                  outerRadius="80%"
                  stroke="var(--card)"
                  strokeWidth={2}
                >
                  {data.map((_, index) => (
                    <Cell fill={color(index)} key={index} />
                  ))}
                </Pie>
                <Legend iconSize={6} iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              </PieChart>
            ) : type === "scatter" ? (
              <ScatterChart accessibilityLayer margin={{ bottom: 4, left: 0, right: 12, top: 8 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  axisLine={false}
                  dataKey={xKey}
                  name={xKey}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  tickLine={false}
                  type="number"
                />
                <YAxis
                  axisLine={false}
                  dataKey={series[0].key}
                  name={series[0].label}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  tickLine={false}
                  type="number"
                  width={40}
                />
                {tooltip}
                <Scatter data={data} fill={color(0)} isAnimationActive={false} />
              </ScatterChart>
            ) : (
              <ChartComponent
                accessibilityLayer
                data={data}
                layout={horizontal ? "vertical" : "horizontal"}
                margin={{ bottom: 4, left: 0, right: 12, top: 8 }}
              >
                <CartesianGrid
                  horizontal={!horizontal}
                  stroke="var(--border)"
                  strokeDasharray="3 3"
                  vertical={horizontal}
                />
                <XAxis
                  axisLine={false}
                  dataKey={horizontal ? undefined : xKey}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  tickLine={false}
                  tickMargin={8}
                  type={horizontal ? "number" : "category"}
                />
                <YAxis
                  axisLine={false}
                  dataKey={horizontal ? xKey : undefined}
                  tick={{ fill: "var(--muted-foreground)", fontSize: 11 }}
                  tickLine={false}
                  tickMargin={8}
                  type={horizontal ? "category" : "number"}
                  width={
                    horizontal
                      ? Math.min(
                          128,
                          Math.max(
                            72,
                            ...data.map((row) => String(row[xKey] ?? "").length * 5.5 + 12)
                          )
                        )
                      : 40
                  }
                />
                {tooltip}
                {legend}
                {series.map((s, index) =>
                  type === "line" ? (
                    <Line
                      activeDot={{ r: 4 }}
                      dataKey={s.key}
                      dot={false}
                      isAnimationActive={false}
                      key={s.key}
                      name={s.label}
                      stroke={color(index)}
                      strokeWidth={2}
                      type="linear"
                    />
                  ) : type === "area" ? (
                    <Area
                      dataKey={s.key}
                      fill={color(index)}
                      fillOpacity={0.12}
                      isAnimationActive={false}
                      key={s.key}
                      name={s.label}
                      stackId={stacked ? "stack" : undefined}
                      stroke={color(index)}
                      type="linear"
                    />
                  ) : (
                    <Bar
                      dataKey={s.key}
                      fill={color(index)}
                      isAnimationActive={false}
                      key={s.key}
                      maxBarSize={32}
                      name={s.label}
                      radius={2}
                      stackId={stacked ? "stack" : undefined}
                    />
                  )
                )}
              </ChartComponent>
            )}
          </ResponsiveContainer>
        </div>
      </div>
    </Card>
  );
});
