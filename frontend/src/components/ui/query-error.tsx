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
        "flex min-w-0 items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive",
        className
      )}
      role="alert"
    >
      <Icon.warning className="mt-0.5 size-4 shrink-0" />
      <p className="min-w-0 flex-1">{errorMessage(error, fallback)}</p>
      {onRetry && (
        <Button onClick={() => void onRetry()} size="sm" type="button" variant="secondary">
          Try again
        </Button>
      )}
    </div>
  );
}
