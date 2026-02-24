import type { HourlyBucket } from "@/api";

export function SparklineChart({ buckets }: { buckets: HourlyBucket[] }) {
  const recent = buckets.slice(-30);
  const vals = recent.map((b) => b.spanCount ?? 0);
  const max = Math.max(...vals, 1);
  const width = 500;
  const height = 80;
  const padding = 4;

  const points = recent.map((_, i) => {
    const x = padding + (i / Math.max(recent.length - 1, 1)) * (width - padding * 2);
    const y = height - padding - ((recent[i].spanCount ?? 0) / max) * (height - padding * 2);
    return `${x},${y}`;
  });

  const pathD = points.length > 0 ? `M${points.join(" L")}` : "";
  const areaD =
    points.length > 0
      ? `${pathD} L${padding + ((recent.length - 1) / Math.max(recent.length - 1, 1)) * (width - padding * 2)},${height - padding} L${padding},${height - padding} Z`
      : "";

  return (
    <div className="w-full" style={{ aspectRatio: `${width}/${height}` }}>
      <svg height="100%" preserveAspectRatio="none" viewBox={`0 0 ${width} ${height}`} width="100%">
        <path d={areaD} fill="var(--accent-warm)" opacity="0.08" />
        <path d={pathD} fill="none" stroke="var(--accent-warm)" strokeWidth="2" />
        {recent.map((b, i) => {
          const x = padding + (i / Math.max(recent.length - 1, 1)) * (width - padding * 2);
          const y = height - padding - ((b.spanCount ?? 0) / max) * (height - padding * 2);
          return (
            <circle
              cx={x}
              cy={y}
              fill="var(--accent-warm)"
              key={b.hour ?? `p-${i}`}
              r="2.5"
            />
          );
        })}
      </svg>
    </div>
  );
}

export function SummaryStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="mb-0.5 text-[0.7rem] font-semibold text-muted-foreground">{label}</p>
      <p className="text-lg font-bold text-foreground">{value}</p>
    </div>
  );
}
