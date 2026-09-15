/** `value` is in percentage points — a 0.62 → 0.71 score move is `9`, not `0.09`
 *  (`fromScore` converts). Direction is a chevron and colour, never a `+`/`−` glyph. */

import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

export const fromScore = (delta: number): number => delta * 100;

/** Below this many points a change reads as noise and renders flat. */
const DELTA_EPSILON = 0.5;

export function DeltaChip({
  value,
  className,
  precision = 0,
  label,
  epsilon = DELTA_EPSILON,
}: {
  value: number;
  className?: string;
  precision?: number;
  /** Overrides the generated screen-reader text. */
  label?: string;
  /** Treat |value| below this as flat. Default 0.5pp; workshop uses ~0.05. */
  epsilon?: number;
}) {
  const flat = Math.abs(value) < epsilon;
  const up = value > 0;
  const magnitude = Math.abs(value).toFixed(precision);
  return (
    <span
      aria-label={label ?? (flat ? "No change" : `${up ? "Up" : "Down"} ${magnitude}%`)}
      className={cn(
        "inline-flex items-center gap-0.5 rounded-sm border px-1 py-0.5 font-mono text-xs font-semibold leading-none tabular-nums",
        flat
          ? "border-border bg-wash-raised text-muted-foreground"
          : up
            ? "border-success/40 bg-success/10 text-success"
            : "border-destructive/40 bg-destructive/10 text-destructive",
        className
      )}
      role="img"
    >
      {!flat &&
        (up ? (
          <Icon.chevronUp className="size-3 shrink-0" />
        ) : (
          <Icon.chevronDown className="size-3 shrink-0" />
        ))}
      {flat ? "0" : magnitude}%
    </span>
  );
}
