import type { MetricPoint, MetricSeries } from "@/components/finetuning/loss-chart";

export type MetricKind = "loss" | "accuracy" | "learningRate" | "gradient";

export function recordedPoints(points: MetricPoint[]): MetricPoint[] {
  return points
    .filter((point) => Number.isFinite(point.step) && Number.isFinite(point.value))
    .sort((a, b) => a.step - b.step);
}

export function summarizeMetric(points: MetricPoint[], kind: MetricKind) {
  const measured = recordedPoints(points);
  if (!measured.length) return null;
  const first = measured[0].value;
  const latest = measured[measured.length - 1].value;
  const reference = measured.reduce(
    (value, point) =>
      kind === "loss" ? Math.min(value, point.value) : Math.max(value, point.value),
    first
  );
  const third =
    kind === "learningRate"
      ? first
      : kind === "gradient"
        ? measured.reduce((sum, point) => sum + point.value / measured.length, 0)
        : measured.length > 1
          ? latest - first
          : null;
  return { count: measured.length, first, latest, reference, third };
}

export function MetricSummary({
  series,
  kind,
  terminal = false,
}: {
  series: (MetricSeries & { terminal?: boolean })[];
  kind: MetricKind;
  terminal?: boolean;
}) {
  const format = (value: number) =>
    kind === "learningRate"
      ? value.toExponential(2)
      : kind === "accuracy"
        ? `${value.toFixed(1)}%`
        : value.toFixed(4);
  const referenceLabel = kind === "loss" ? "Lowest" : kind === "accuracy" ? "Highest" : "Peak";
  const thirdLabel =
    kind === "learningRate" ? "Initial" : kind === "gradient" ? "Average" : "Change from first";
  return (
    <div className="divide-y divide-border/70 border-t border-border/70">
      {series.map((item) => {
        const summary = summarizeMetric(item.points, kind);
        const third = summary?.third;
        const change = kind === "loss" || kind === "accuracy";
        const values = [
          {
            label: (item.terminal ?? terminal) ? "Last recorded" : "Latest",
            value: summary ? format(summary.latest) : "—",
          },
          { label: referenceLabel, value: summary ? format(summary.reference) : "—" },
          {
            label: thirdLabel,
            value:
              third == null
                ? "—"
                : change
                  ? `${third > 0 ? "+" : ""}${kind === "accuracy" ? `${third.toFixed(1)} pp` : format(third)}`
                  : format(third),
          },
        ];
        return (
          <div key={item.id}>
            {series.length > 1 && (
              <div className="flex items-center gap-2 px-3 pt-3 text-xs">
                <span
                  aria-hidden
                  className="size-2 shrink-0 rounded-xs"
                  style={{ backgroundColor: item.color }}
                />
                <span className="truncate">{item.name}</span>
              </div>
            )}
            <dl className="grid grid-cols-3 gap-2 px-3 py-3">
              {values.map(({ label, value }) => (
                <div className="min-w-0" key={label}>
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="mt-1 font-mono text-sm tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
          </div>
        );
      })}
    </div>
  );
}
