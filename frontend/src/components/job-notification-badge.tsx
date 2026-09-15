import { cn } from "@/lib/utils";

export type JobNotificationBadgeTone = "success" | "running";

/** Solid fill rather than the tinted chip triplet, so it reads at 18px. */
export function JobNotificationBadge({
  count,
  tone,
  className,
}: {
  count: number;
  tone: JobNotificationBadgeTone;
  className?: string;
}) {
  if (count <= 0) return null;
  return (
    <span
      aria-hidden
      className={cn(
        "flex h-[18px] min-w-[18px] items-center justify-center rounded-sm px-0.5 text-sm font-bold leading-none tabular-nums",
        tone === "success"
          ? "bg-success text-success-foreground"
          : "bg-primary text-primary-foreground",
        className
      )}
    >
      {count > 9 ? "9+" : count}
    </span>
  );
}
