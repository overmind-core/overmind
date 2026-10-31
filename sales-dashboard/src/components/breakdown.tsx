import { humanize } from "~/lib/format";

import { SectionCard } from "@/components/ui/section-card";

/** Labelled horizontal bars, largest first, scaled to the largest value. */
export function Breakdown({
  title,
  values,
  format,
  empty = "No data",
}: {
  title: string;
  values: Record<string, number | string>;
  format: (value: number) => string;
  empty?: string;
}) {
  const rows = Object.entries(values)
    .map(([key, value]) => ({ key, value: Number(value) }))
    .sort((a, b) => b.value - a.value);
  const max = Math.max(...rows.map((row) => row.value), 0);
  return (
    <SectionCard title={title}>
      {rows.length ? (
        <ul className="space-y-2">
          {rows.map((row) => (
            <li className="space-y-1" key={row.key}>
              <div className="flex justify-between gap-3 text-xs">
                <span>{humanize(row.key)}</span>
                <span className="font-mono tabular-nums">{format(row.value)}</span>
              </div>
              <div className="h-1.5 rounded-xs bg-wash-raised">
                <div
                  className="h-full rounded-xs bg-chart-1"
                  style={{ width: max ? `${(row.value / max) * 100}%` : 0 }}
                />
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{empty}</p>
      )}
    </SectionCard>
  );
}
