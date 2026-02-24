import type { HourlyBucket } from "@/api";

export type AnalyticsPreset = "past24h" | "past7d" | "past14d" | "past1m";
export type AnalyticsRange = AnalyticsPreset | { from: Date; to: Date };
export type AnalyticsAggregation = "hour" | "day";

export const RANGE_LABELS: Record<AnalyticsPreset, string> = {
  past24h: "24H",
  past7d: "7D",
  past14d: "14D",
  past1m: "1M",
};

const RANGE_MS: Record<AnalyticsPreset, number> = {
  past24h: 24 * 60 * 60 * 1000,
  past7d: 7 * 24 * 60 * 60 * 1000,
  past14d: 14 * 24 * 60 * 60 * 1000,
  past1m: 30 * 24 * 60 * 60 * 1000,
};

export function rangeLabel(range: AnalyticsRange): string {
  if (typeof range === "string") return RANGE_LABELS[range];
  const fmt = (d: Date) =>
    `${d.getDate()} ${d.toLocaleString("en", { month: "short" })}`;
  return `${fmt(range.from)} – ${fmt(range.to)}`;
}

export function clampBuckets(buckets: HourlyBucket[], range: AnalyticsRange): HourlyBucket[] {
  if (typeof range === "string") {
    const min = Date.now() - RANGE_MS[range];
    return buckets.filter((b) => {
      const ts = Date.parse(b.hour);
      return Number.isFinite(ts) ? ts >= min : true;
    });
  }
  const minMs = range.from.getTime();
  const maxMs = range.to.getTime() + 24 * 60 * 60 * 1000;
  return buckets.filter((b) => {
    const ts = Date.parse(b.hour);
    return Number.isFinite(ts) ? ts >= minMs && ts < maxMs : true;
  });
}

export function aggregateBuckets(
  buckets: HourlyBucket[],
  aggregation: AnalyticsAggregation,
): HourlyBucket[] {
  if (aggregation === "hour") return buckets;

  const byDay = new Map<
    string,
    {
      key: string;
      span_count: number;
      cost: number;
      scoreWeightedSum: number;
      latencyWeightedSum: number;
      scoreWeight: number;
      latencyWeight: number;
    }
  >();

  for (const b of buckets) {
    const d = new Date(b.hour);
    const key = Number.isFinite(d.getTime())
      ? `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
      : b.hour;
    const prev = byDay.get(key) ?? {
      cost: 0,
      key,
      latencyWeight: 0,
      latencyWeightedSum: 0,
      scoreWeight: 0,
      scoreWeightedSum: 0,
      span_count: 0,
    };

    const spanCount = b.spanCount ?? 0;
    const score = b.avgScore;
    const latency = b.avgLatencyMs;

    byDay.set(key, {
      ...prev,
      cost: prev.cost + (b.estimatedCost ?? 0),
      latencyWeight: prev.latencyWeight + (latency != null ? spanCount : 0),
      latencyWeightedSum: prev.latencyWeightedSum + (latency != null ? latency * spanCount : 0),
      scoreWeight: prev.scoreWeight + (score != null ? spanCount : 0),
      scoreWeightedSum: prev.scoreWeightedSum + (score != null ? score * spanCount : 0),
      span_count: prev.span_count + spanCount,
    });
  }

  return Array.from(byDay.values())
    .sort((a, b) => a.key.localeCompare(b.key))
    .map((g) => ({
      avgLatencyMs: g.latencyWeight > 0 ? g.latencyWeightedSum / g.latencyWeight : null,
      avgScore: g.scoreWeight > 0 ? g.scoreWeightedSum / g.scoreWeight : null,
      estimatedCost: g.cost,
      hour: g.key,
      spanCount: g.span_count,
    }));
}

export function aggregationForRange(range: AnalyticsRange): AnalyticsAggregation {
  if (typeof range === "string") {
    return range === "past24h" || range === "past7d" ? "hour" : "day";
  }
  const days = (range.to.getTime() - range.from.getTime()) / (24 * 60 * 60 * 1000);
  return days <= 7 ? "hour" : "day";
}
