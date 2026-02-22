import { cn } from "@/lib/utils";

export function MiniStat({
  label,
  value,
  className,
}: {
  label: string;
  value: string;
  className?: string;
}) {
  return (
    <div className={cn("flex items-baseline gap-1.5", className)}>
      <span className="text-xs font-medium text-muted-foreground">{label}:</span>
      <span className="text-sm font-semibold">{value}</span>
    </div>
  );
}
