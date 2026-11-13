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

import type { ChartNode, Row } from "./types";

const COLORS = [1, 2, 3, 4, 5].map((slot) => `var(--chart-${slot})`);
const color = (index: number) => COLORS[index % COLORS.length];
const TICK = { fill: "var(--muted-foreground)", fontSize: 11 };

/** Import through `lazyChart`, so recharts stays out of the initial bundle. A missing
 *  value stays a gap: recharts draws nothing for null. */
export default function AssistantChart({ node, rows }: { node: ChartNode; rows: Row[] }) {
  const horizontal = node.kind === "horizontal-bar";
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
  const legend = (
    <Legend iconSize={6} iconType="circle" wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
  );
  const grid = (
    <CartesianGrid
      horizontal={!horizontal}
      stroke="var(--border)"
      strokeDasharray="3 3"
      vertical={horizontal}
    />
  );
  const Frame = node.kind === "line" ? LineChart : node.kind === "area" ? AreaChart : BarChart;
  const categoryWidth = Math.min(
    128,
    Math.max(72, ...rows.map((row) => String(row[node.x] ?? "").length * 5.5 + 12))
  );

  return (
    <div
      className="w-full px-3 pt-3 pb-3 text-xs"
      style={{ height: horizontal ? Math.min(300, Math.max(180, rows.length * 26 + 28)) : 200 }}
    >
      <ResponsiveContainer height="100%" minWidth={0} width="100%">
        {node.kind === "pie" ? (
          <PieChart accessibilityLayer>
            {tooltip}
            <Pie
              data={rows}
              dataKey={node.y[0]}
              innerRadius="48%"
              isAnimationActive={false}
              nameKey={node.x}
              outerRadius="80%"
              stroke="var(--card)"
              strokeWidth={2}
            >
              {rows.map((row, index) => (
                <Cell fill={color(index)} key={String(row[node.x])} />
              ))}
            </Pie>
            {legend}
          </PieChart>
        ) : node.kind === "scatter" ? (
          <ScatterChart accessibilityLayer margin={{ bottom: 4, left: 0, right: 12, top: 8 }}>
            <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
            <XAxis
              axisLine={false}
              dataKey={node.x}
              name={node.x}
              tick={TICK}
              tickLine={false}
              type="number"
            />
            <YAxis
              axisLine={false}
              dataKey={node.y[0]}
              name={node.y[0]}
              tick={TICK}
              tickLine={false}
              type="number"
              width={40}
            />
            {tooltip}
            <Scatter data={rows} fill={color(0)} isAnimationActive={false} />
          </ScatterChart>
        ) : (
          <Frame
            accessibilityLayer
            data={rows}
            layout={horizontal ? "vertical" : "horizontal"}
            margin={{ bottom: 4, left: 0, right: 12, top: 8 }}
          >
            {grid}
            <XAxis
              axisLine={false}
              dataKey={horizontal ? undefined : node.x}
              tick={TICK}
              tickLine={false}
              tickMargin={8}
              type={horizontal ? "number" : "category"}
            />
            <YAxis
              axisLine={false}
              dataKey={horizontal ? node.x : undefined}
              tick={TICK}
              tickLine={false}
              tickMargin={8}
              type={horizontal ? "category" : "number"}
              width={horizontal ? categoryWidth : 40}
            />
            {tooltip}
            {node.y.length > 1 && legend}
            {node.y.map((series, index) =>
              node.kind === "line" ? (
                <Line
                  activeDot={{ r: 4 }}
                  dataKey={series}
                  dot={false}
                  isAnimationActive={false}
                  key={series}
                  stroke={color(index)}
                  strokeWidth={2}
                  type="linear"
                />
              ) : node.kind === "area" ? (
                <Area
                  dataKey={series}
                  fill={color(index)}
                  fillOpacity={0.12}
                  isAnimationActive={false}
                  key={series}
                  stackId={node.stacked ? "stack" : undefined}
                  stroke={color(index)}
                  type="linear"
                />
              ) : (
                <Bar
                  dataKey={series}
                  fill={color(index)}
                  isAnimationActive={false}
                  key={series}
                  maxBarSize={32}
                  radius={2}
                  stackId={node.stacked ? "stack" : undefined}
                />
              )
            )}
          </Frame>
        )}
      </ResponsiveContainer>
    </div>
  );
}
