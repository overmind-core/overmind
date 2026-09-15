import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Icon } from "@/components/ui/icons";
import { Spinner } from "@/components/ui/spinner";
import { cn } from "@/lib/utils";

interface FailureCardProps {
  title: string;
  error?: string | null;
  onRetry: () => void;
  retryPending?: boolean;
  /** A single `<Link>`-like child; the card supplies the button chrome. */
  alternative?: ReactNode;
  className?: string;
}

export function FailureCard({
  title,
  error,
  onRetry,
  retryPending,
  alternative,
  className,
}: FailureCardProps) {
  return (
    <div className={cn("rounded-md border border-destructive/40 bg-destructive/10 p-3", className)}>
      <p className="text-sm font-medium text-destructive">{title}</p>
      {error?.trim() && (
        <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-destructive/90">
          {error.trim()}
        </p>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button disabled={retryPending} onClick={onRetry} size="sm">
          {retryPending ? <Spinner size="sm" /> : <Icon.refresh />}
          Retry
        </Button>
        {alternative && (
          <Button asChild size="sm" variant="secondary">
            {alternative}
          </Button>
        )}
      </div>
    </div>
  );
}
