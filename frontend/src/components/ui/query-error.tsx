import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { errorMessage } from "@/lib/notify";
import { cn } from "@/lib/utils";

export function QueryError({
  className,
  error,
  fallback,
  onRetry,
}: {
  className?: string;
  error?: unknown;
  fallback: string;
  onRetry?: () => Promise<unknown> | unknown;
}) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-wrap items-center gap-2 py-2 text-sm text-muted-foreground",
        className
      )}
      role="alert"
    >
      <Icon.warning className="size-3.5 shrink-0 text-warning" />
      <p className="min-w-0 flex-1">{errorMessage(error, fallback)}</p>
      {onRetry && (
        <Button onClick={() => void onRetry()} size="xs" type="button" variant="secondary">
          Try again
        </Button>
      )}
    </div>
  );
}
