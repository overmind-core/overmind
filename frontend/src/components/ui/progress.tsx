import { cn } from "@/lib/utils";

/** Determinate when `percent` is a number, indeterminate when null. */
export function Progress({
  percent,
  label,
  className,
}: {
  percent: number | null;
  label: string;
  className?: string;
}) {
  const clamped = percent === null ? null : Math.max(0, Math.min(100, percent));
  return (
    <div
      aria-label={label}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={clamped ?? undefined}
      className={cn("h-1.5 w-full overflow-hidden rounded-sm bg-muted", className)}
      role="progressbar"
    >
      <div
        className={cn(
          "h-full rounded-sm bg-primary",
          clamped === null
            ? "progress-indeterminate"
            : "transition-[width] duration-300 motion-reduce:transition-none"
        )}
        style={clamped === null ? undefined : { width: `${clamped}%` }}
      />
    </div>
  );
}
