import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { GraphTooltip } from "@/components/ui/graph-tooltip";
import type { ModelMarker } from "@/openapi";

export type TrendDatum = { at: number; value: number | null };

export function bucketLabel(at: number, bucket: "hour" | "day") {
  return new Date(at).toLocaleString(
    undefined,
    bucket === "hour"
      ? { day: "numeric", hour: "2-digit", month: "short", timeZone: "UTC" }
      : { day: "numeric", month: "short", timeZone: "UTC" }
  );
}

const LABEL_CLEARANCE = 0.12;
const LABEL_LEVELS = 3;

function bucketStart(at: number, bucket: "hour" | "day") {
  const size = bucket === "hour" ? 3_600_000 : 86_400_000;
  return at - (at % size);
}

function shortModel(model: string) {
  return model.split("/").pop() ?? model;
}

export default function TrendChart({
  data,
  bucket,
  markers,
  label,
  format,
  color,
  height = 176,
}: {
  data: TrendDatum[];
  bucket: "hour" | "day";
  markers: ModelMarker[];
  label: string;
  format: (value: number) => string;
  color: string;
  height?: number;
}) {
  // Releases close together would print over each other, so each steps up a line.
  const span = (data.at(-1)?.at ?? 0) - (data[0]?.at ?? 0);
  const placed: { marker: ModelMarker; level: number }[] = [];
  for (const marker of markers) {
    const previous = placed.at(-1);
    const close =
      previous && marker.at.getTime() - previous.marker.at.getTime() < span * LABEL_CLEARANCE;
    placed.push({ level: close && previous ? (previous.level + 1) % LABEL_LEVELS : 0, marker });
  }

  return (
    <div aria-label={`${label} over time`} role="img" style={{ height }}>
      <ResponsiveContainer height="100%" width="100%">
        <BarChart
          barCategoryGap="20%"
          data={data}
          margin={{
            bottom: 0,
            left: 0,
            right: 8,
            top: markers.length ? 10 + 12 * LABEL_LEVELS : 8,
          }}
        >
          <CartesianGrid stroke="var(--border)" strokeOpacity={0.6} vertical={false} />
          <XAxis
            axisLine={false}
            dataKey="at"
            minTickGap={24}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            tickFormatter={(at) => bucketLabel(Number(at), bucket)}
            tickLine={false}
          />
          <YAxis
            axisLine={false}
            tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
            tickFormatter={(value) => format(Number(value))}
            tickLine={false}
            width={48}
          />
          <Tooltip
            content={({ active, payload, label: at }) => (
              <GraphTooltip
                active={active}
                label={at != null ? bucketLabel(Number(at), bucket) : undefined}
                nameFormatter={() => label}
                payload={payload}
                valueFormatter={format}
              />
            )}
            cursor={{ fill: "var(--wash-raised)" }}
          />
          <Bar
            dataKey="value"
            fill={color}
            isAnimationActive={false}
            maxBarSize={28}
            radius={[2, 2, 0, 0]}
          />
          {placed.map(({ marker, level }) => (
            <ReferenceLine
              key={`${marker.model}-${marker.at.toISOString()}`}
              label={{
                fill: "var(--muted-foreground)",
                fontSize: 10,
                offset: 4 + 12 * level,
                position: "top",
                value: shortModel(marker.model),
              }}
              stroke="var(--muted-foreground)"
              strokeDasharray="3 3"
              x={bucketStart(marker.at.getTime(), bucket)}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
