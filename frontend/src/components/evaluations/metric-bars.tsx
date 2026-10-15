import { useState } from "react";

import { Card } from "@/components/ui/card";
import { GraphTooltip } from "@/components/ui/graph-tooltip";
import { Icon } from "@/components/ui/icons";
import { seriesColor } from "@/lib/colors";

export function MetricBars({
  title,
  benchmark,
  description,
  rows,
  percentage = false,
  valueFormatter,
  onSelect,
  emptyHint,
}: {
  title: string;
  benchmark: string;
  description?: string;
  percentage?: boolean;
  valueFormatter?: (value: number) => string;
  onSelect?: (key: string) => void;
  emptyHint?: string;
  rows: { key: string; name: string; value: number; colorIndex: number }[];
}) {
  const [hover, setHover] = useState<{ x: number; row: number } | null>(null);
  const min = Math.min(0, ...rows.map((r) => r.value));
  const highest = percentage ? 1 : Math.max(0, ...rows.map((r) => r.value));
  const max = highest === min ? min + 1 : highest;
  const position = (value: number) => ((value - min) / (max - min)) * 100;
  const zero = position(0);
  const number =
    valueFormatter ??
    ((value: number) => (percentage ? `${(value * 100).toFixed(1)}%` : value.toFixed(4)));
  const row = hover ? rows[hover.row] : undefined;
  return (
    <Card className="min-w-0">
      <div className="flex items-start gap-2 border-b border-border px-5 py-4">
        <Icon.analytics className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{title}</h3>
          {description && <p className="mt-1 text-xs text-muted-foreground">{description}</p>}
        </div>
      </div>
      {rows.length === 0 && emptyHint ? (
        <p className="px-5 py-8 text-sm text-muted-foreground">{emptyHint}</p>
      ) : (
        <div className="relative" onMouseLeave={() => setHover(null)}>
          <svg
            aria-label={`${benchmark} · ${title}`}
            className="pointer-events-none absolute inset-0 h-full w-full"
            preserveAspectRatio="none"
            role="img"
            viewBox="0 0 100 100"
          >
            {[0.25, 0.5, 0.75].map((fraction) => (
              <line
                key={fraction}
                stroke="var(--border)"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
                x1={fraction * 100}
                x2={fraction * 100}
                y1={0}
                y2={100}
              />
            ))}
            {hover && (
              <line
                stroke="var(--muted-foreground)"
                strokeDasharray="3 3"
                vectorEffect="non-scaling-stroke"
                x1={hover.x}
                x2={hover.x}
                y1={0}
                y2={100}
              />
            )}
          </svg>
          {rows.map((item, index) => (
            <div
              className="relative h-12"
              key={item.key}
              onMouseMove={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                setHover({
                  row: index,
                  x: Math.max(0, Math.min(100, ((event.clientX - rect.left) / rect.width) * 100)),
                });
              }}
            >
              <button
                aria-label={`${item.name}: ${number(item.value)}`}
                className={`absolute inset-0 focus-visible:outline focus-visible:outline-ring ${onSelect ? "cursor-pointer" : "cursor-default"}`}
                onBlur={() => setHover(null)}
                onClick={() => onSelect?.(item.key)}
                onFocus={() => setHover({ row: index, x: position(item.value) })}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setHover(null);
                }}
                type="button"
              >
                <span
                  aria-hidden
                  className="absolute bottom-1 h-4 min-w-px"
                  style={{
                    backgroundColor: seriesColor(item.colorIndex),
                    left: `${Math.min(zero, position(item.value))}%`,
                    width: `${Math.abs(position(item.value) - zero)}%`,
                  }}
                />
              </button>
              <div className="pointer-events-none absolute inset-x-3 top-1.5 flex items-center justify-between gap-3 text-xs">
                <span className="flex min-w-0 items-center gap-2 bg-card/90">
                  <span
                    className="size-2 shrink-0 rounded-xs"
                    style={{ backgroundColor: seriesColor(item.colorIndex) }}
                  />
                  <span className="truncate">{item.name}</span>
                </span>
                <span className="shrink-0 bg-card/90 font-mono tabular-nums">
                  {number(item.value)}
                </span>
              </div>
            </div>
          ))}
          {row && hover && (
            <div
              className="pointer-events-none absolute z-10 max-w-full"
              style={{ right: 8, top: Math.min(hover.row + 1, rows.length) * 48 + 12 }}
            >
              <GraphTooltip
                active
                label={benchmark}
                payload={[{ color: seriesColor(row.colorIndex), name: row.name, value: row.value }]}
                valueFormatter={number}
              />
            </div>
          )}
          <div className="flex justify-between px-3 py-2 font-mono text-xs text-muted-foreground">
            <span>{number(min)}</span>
            <span>{number(max)}</span>
          </div>
        </div>
      )}
    </Card>
  );
}
