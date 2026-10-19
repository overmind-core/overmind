import { Icon } from "@/components/ui/icons";
import { cn } from "@/lib/utils";

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
      className={cn(
        "animate-spin text-muted-foreground motion-reduce:animate-none",
        sizeMap[size],
        className
      )}
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
        "flex w-full min-w-0 items-center justify-center gap-2 p-4 text-sm text-muted-foreground",
        fullPage && "min-h-[400px] flex-1",
        className
      )}
      data-slot="loading-state"
      role="status"
    >
      <Spinner size={fullPage ? "lg" : "default"} />
      <span>{label ?? "Loading content"}</span>
    </div>
  );
}

export { LoadingState, Spinner };
