import { useEffect, useMemo, useState } from "react";

import {
  CartesianGrid,
  Line,
  LineChart,
  Tooltip as RechartsTooltip,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

import { Card } from "@/components/ui/card";
import { Icon } from "@/components/ui/icons";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SERIES_COLORS } from "@/lib/colors";
import { cn } from "@/lib/utils";
import type { EvaluatorScoreHistory } from "@/openapi";

const SERIES_HEX = SERIES_COLORS;

// Beyond this the chart reads as spaghetti; the rest start hidden behind the legend.
const DEFAULT_VISIBLE_LIMIT = 6;

type Period = "7d" | "30d" | "90d" | "all";

const PERIOD_DAYS: Record<Period, number | null> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
  all: null,
};

const PERIOD_LABEL: Record<Period, string> = {
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  all: "All time",
};

type ChartRow = { ts: number } & Record<string, number | null>;

function latestScore(series: EvaluatorScoreHistory): number {
  const last = series.points.at(-1);
  return last ? last.score : Number.NEGATIVE_INFINITY;
}

function defaultHiddenSeries(series: EvaluatorScoreHistory[]): Set<string> {
  if (series.length <= DEFAULT_VISIBLE_LIMIT) return new Set();
  const ranked = [...series].sort((a, b) => latestScore(b) - latestScore(a));
  return new Set(ranked.slice(DEFAULT_VISIBLE_LIMIT).map((s) => s.name));
}

function fmtDate(ts: number): string {
  return new Date(ts).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function fmtDateTime(ts: number): string {
  return new Date(ts).toLocaleString(undefined, {
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    month: "short",
  });
}

interface ChartTooltipProps {
  active?: boolean;
  payload?: Array<{ name: string; value: number | null; color: string }>;
  label?: number;
}

function ChartTooltip({ active, payload, label }: ChartTooltipProps) {
  if (!active || !payload?.length) return null;
  const scored = payload.filter((entry) => entry.value != null);
  if (scored.length === 0) return null;
  return (
    <div className="rounded-sm border border-border bg-background px-3 py-2 text-xs">
      <p className="mb-1.5 font-medium text-foreground">
        {label != null ? fmtDateTime(label) : ""}
      </p>
      {scored
        .slice()
        .sort((a, b) => (b.value ?? 0) - (a.value ?? 0))
        .map((entry) => (
          <div className="flex items-center gap-2" key={entry.name}>
            <span className="inline-block size-2 rounded-xs" style={{ background: entry.color }} />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="ml-auto font-mono tabular-nums text-foreground">
              {(entry.value as number).toFixed(1)}
            </span>
          </div>
        ))}
    </div>
  );
}

interface EvalScoreHistoryChartProps {
  series: EvaluatorScoreHistory[];
}

export function EvalScoreHistoryChart({ series }: EvalScoreHistoryChartProps) {
  const [period, setPeriod] = useState<Period>("all");
  const [hidden, setHidden] = useState<Set<string>>(() => defaultHiddenSeries(series));

  // Keyed on the names, not the array: a refetch of the same evaluators must not
  // wipe the user's legend toggles.
  const seriesNames = series.map((s) => s.name).join("\u0000");
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on the names, not the array identity
  useEffect(() => {
    setHidden(defaultHiddenSeries(series));
  }, [seriesNames]);

  const colorByName = useMemo(() => {
    const map = new Map<string, string>();
    series.forEach((s, i) => {
      map.set(s.name, SERIES_HEX[i % SERIES_HEX.length]);
    });
    return map;
  }, [series]);

  const cutoff = useMemo(() => {
    const days = PERIOD_DAYS[period];
    return days == null ? null : Date.now() - days * 86_400_000;
  }, [period]);

  // Evaluators are scored within one eval run, so points sharing a run id collapse
  // onto a single x position.
  const rows = useMemo<ChartRow[]>(() => {
    const byRun = new Map<string, ChartRow>();
    for (const s of series) {
      if (hidden.has(s.name)) continue;
      for (const point of s.points) {
        const ts = point.runAt.getTime();
        if (cutoff != null && ts < cutoff) continue;
        let row = byRun.get(point.runId);
        if (!row) {
          row = { ts };
          byRun.set(point.runId, row);
        }
        row[s.name] = point.score;
      }
    }
    return [...byRun.values()].sort((a, b) => a.ts - b.ts);
  }, [series, hidden, cutoff]);

  const visibleSeries = series.filter((s) => !hidden.has(s.name));

  const toggleSeries = (name: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(name)) {
        next.delete(name);
      } else {
        next.add(name);
      }
      return next;
    });

  return (
    <Card className="flex flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Icon.analytics className="size-4 text-muted-foreground" />
        <h3 className="text-xs">Score history</h3>
        <span className="text-xs text-muted-foreground">per evaluator · 0–100 over time</span>
        <div className="ml-auto">
          <Select onValueChange={(v) => setPeriod(v as Period)} value={period}>
            <SelectTrigger
              aria-label="Filter score history by time window"
              className="w-32 text-xs"
              size="sm"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {(Object.keys(PERIOD_LABEL) as Period[]).map((p) => (
                <SelectItem className="text-xs" key={p} value={p}>
                  {PERIOD_LABEL[p]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="flex h-56 items-center justify-center rounded-sm border border-dashed border-border text-xs text-muted-foreground">
          {visibleSeries.length === 0
            ? "All evaluators hidden — toggle one from the legend below."
            : "No completed runs in this time window."}
        </div>
      ) : (
        <div
          aria-label="Line chart of evaluator scores over time"
          className="cursor-default select-none [&_*]:outline-none"
          role="img"
        >
          <ResponsiveContainer height={260} width="100%">
            <LineChart data={rows} margin={{ bottom: 4, left: -12, right: 12, top: 8 }}>
              <CartesianGrid className="stroke-border/50" strokeDasharray="3 3" />
              <XAxis
                dataKey="ts"
                domain={["dataMin", "dataMax"]}
                scale="time"
                tick={{ fontSize: 10 }}
                tickFormatter={fmtDate}
                tickLine={false}
                type="number"
              />
              <YAxis domain={[0, 100]} tick={{ fontSize: 10 }} tickLine={false} width={40} />
              <RechartsTooltip
                content={<ChartTooltip />}
                cursor={{ stroke: "var(--muted-foreground)", strokeOpacity: 0.3 }}
              />
              {visibleSeries.map((s) => (
                <Line
                  connectNulls
                  dataKey={s.name}
                  dot={{ r: 2 }}
                  isAnimationActive={false}
                  key={s.name}
                  name={s.name}
                  stroke={colorByName.get(s.name)}
                  strokeWidth={1.5}
                  type="monotone"
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {series.map((s) => {
          const isHidden = hidden.has(s.name);
          return (
            <button
              aria-pressed={!isHidden}
              className={cn(
                "flex items-center gap-1.5 rounded-sm px-1 py-0.5 text-xs transition-colors hover:bg-wash-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring",
                isHidden ? "text-muted-foreground" : "text-muted-foreground"
              )}
              key={s.name}
              onClick={() => toggleSeries(s.name)}
              type="button"
            >
              <span
                className="size-2.5 rounded-sm"
                style={{
                  backgroundColor: isHidden ? "transparent" : colorByName.get(s.name),
                  boxShadow: isHidden ? `inset 0 0 0 1.5px ${colorByName.get(s.name)}` : undefined,
                }}
              />
              <span className={cn(isHidden && "line-through")}>{s.name}</span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}
