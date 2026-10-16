import { Icon } from "@/components/ui/icons";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

// Content loads use skeletons; spinners are reserved for pending actions and live jobs.

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
      aria-busy="true"
      aria-label={label ?? "Loading content"}
      className={cn(
        "w-full min-w-0 overflow-hidden p-4",
        fullPage && "min-h-[400px] flex-1",
        className
      )}
      data-slot="loading-state"
      role="status"
    >
      <span className="sr-only">{label ?? "Loading content"}</span>
      <div aria-hidden="true" className="space-y-5">
        <div className="space-y-2">
          <Skeleton className="h-4 w-1/3 max-w-48" />
          <Skeleton className="h-3 w-1/2 max-w-72" />
        </div>
        <div className="space-y-4">
          {["w-full", "w-5/6", "w-full", "w-2/3"].map((width, index) => (
            <div className="flex items-center gap-3" key={`${width}-${index}`}>
              <Skeleton className="size-7 shrink-0" />
              <div className={cn("min-w-0 space-y-2", width)}>
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-2 w-1/2" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

export { LoadingState, Spinner };
