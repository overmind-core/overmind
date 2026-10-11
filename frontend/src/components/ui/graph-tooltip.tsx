interface GraphTooltipEntry {
  name?: unknown;
  value?: unknown;
  color?: string;
  dataKey?: unknown;
}

export function GraphTooltip({
  active,
  label,
  payload,
  valueFormatter = (value) => value.toFixed(4),
  nameFormatter = (name) => name,
}: {
  active?: boolean;
  label?: string | number;
  payload?: readonly GraphTooltipEntry[];
  valueFormatter?: (value: number) => string;
  nameFormatter?: (name: string) => string;
}) {
  const entries = payload?.filter(
    (entry): entry is GraphTooltipEntry & { value: number } =>
      typeof entry.value === "number" && Number.isFinite(entry.value)
  );
  if (!active || !entries?.length) return null;

  return (
    <div
      className="min-w-48 max-w-sm overflow-hidden rounded-md border border-border bg-popover text-xs text-popover-foreground"
      role="tooltip"
    >
      {label != null && (
        <p className="border-b border-border/70 px-2.5 py-2 font-medium capitalize">{label}</p>
      )}
      <dl className="grid grid-cols-[minmax(0,1fr)_auto]">
        {entries.map((entry, index) => (
          <div
            className="col-span-2 grid grid-cols-subgrid border-b border-border/70 last:border-b-0"
            key={String(entry.dataKey ?? entry.name ?? index)}
          >
            <dt className="flex min-w-0 items-center gap-2 px-2.5 py-2">
              <span
                aria-hidden="true"
                className="size-2 shrink-0 rounded-xs"
                style={{ backgroundColor: entry.color }}
              />
              <span className="min-w-0 break-words">{nameFormatter(String(entry.name ?? ""))}</span>
            </dt>
            <dd className="flex items-center justify-end border-l border-border/70 px-2.5 py-2 font-mono tabular-nums">
              {valueFormatter(entry.value)}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
