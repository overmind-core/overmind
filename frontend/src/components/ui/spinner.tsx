import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

// Loading rule: `Skeleton` when the shape is known ahead of time, `LoadingState` for a
// region whose shape is not, `Spinner` only inside a control or inline in text — never
// as a page's or a panel's loading state.

const sizeMap = {
  default: "size-4",
  lg: "size-6",
  sm: "size-3.5",
} as const;

function Spinner({
  size = "default",
  className,
}: {
  size?: keyof typeof sizeMap;
  className?: string;
}) {
  return (
    <Icon.loader
      aria-hidden="true"
      className={cn("animate-spin text-muted-foreground", sizeMap[size], className)}
      data-slot="spinner"
    />
  );
}

function LoadingState({
  label,
  fullPage = false,
  className,
}: {
  label?: string;
  fullPage?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3",
        fullPage ? "min-h-[400px] flex-1" : "py-12",
        className
      )}
      data-slot="loading-state"
    >
      <Spinner size="lg" />
      {label ? <p className="text-sm text-muted-foreground">{label}</p> : null}
    </div>
  );
}

export { LoadingState, Spinner };
