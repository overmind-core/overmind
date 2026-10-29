import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { Card } from "@/components/ui/card";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { TITLE } from "@/lib/typography";
import type { InferenceMetrics } from "@/openapi";

const number = (value: number | null | undefined) =>
  value == null ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
const credits = (value: number | null | undefined) =>
  value == null ? "—" : `${number(value)} credits`;

export function ModelUsage({ metrics }: { metrics: InferenceMetrics }) {
  const usage = metrics.dedicatedUsage;
  return (
    <section aria-label="Spend and usage" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className={TITLE.section}>Spend & usage</h2>
        <span className="text-xs text-muted-foreground">Dedicated charges · all traffic</span>
      </div>
      <Card className="overflow-hidden">
        <div className="grid grid-cols-2 divide-x divide-border/70 lg:grid-cols-4">
          {[
            ["Dedicated spend", credits(usage.credits)],
            ["Billed GPU hours", number(usage.gpuHours)],
            ["Input tokens", number(metrics.promptTokens)],
            ["Output tokens", number(metrics.completionTokens)],
          ].map(([label, value]) => (
            <div className="space-y-1 px-4 py-3" key={label}>
              <p className="text-xs text-muted-foreground">{label}</p>
              <p className="text-sm tabular-nums">{value}</p>
            </div>
          ))}
        </div>
        {usage.points.length ? (
          <div className="border-t border-border/70 p-3">
            <div aria-label="Dedicated spend chart" className="h-44" role="img">
              <ResponsiveContainer height="100%" width="100%">
                <BarChart
                  data={usage.points.map((point) => ({ ...point, time: point.bucket.getTime() }))}
                  margin={{ bottom: 0, left: 0, right: 8, top: 8 }}
                >
                  <CartesianGrid stroke="var(--border)" vertical={false} />
                  <XAxis
                    axisLine={false}
                    dataKey="time"
                    minTickGap={40}
                    tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                    tickFormatter={(time) =>
                      new Date(time).toLocaleString(undefined, {
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                        month: "short",
                      })
                    }
                    tickLine={false}
                  />
                  <YAxis
                    axisLine={false}
                    tick={{ fill: "var(--muted-foreground)", fontSize: 10 }}
                    tickFormatter={number}
                    tickLine={false}
                    width={48}
                  />
                  <Tooltip
                    content={({ active, payload, label }) => (
                      <GraphTooltip
                        active={active}
                        label={label != null ? new Date(Number(label)).toLocaleString() : undefined}
                        payload={payload}
                        valueFormatter={(value) => credits(value)}
                      />
                    )}
                  />
                  <Bar
                    dataKey="credits"
                    fill="var(--foreground)"
                    isAnimationActive={false}
                    maxBarSize={24}
                    name="Dedicated spend"
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        ) : (
          <p className="border-t border-border/70 px-4 py-3 text-xs text-muted-foreground">
            No dedicated charges in this period
          </p>
        )}
        {usage.breakdown.length > 1 ? (
          <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-border/70 px-3 py-2 text-xs text-muted-foreground">
            {usage.breakdown.map((item) => (
              <span key={item.gpuType ?? "unknown"}>
                {item.gpuType ?? "Unspecified GPU"}
                <span className="ml-2 tabular-nums text-foreground">{credits(item.credits)}</span>
              </span>
            ))}
          </div>
        ) : null}
      </Card>
    </section>
  );
}
